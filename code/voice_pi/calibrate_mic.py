"""
calibrate_mic.py: 마이크 도착 후 소리 크기 기준(--gate-db)을 정하는 도구 (조용한 방 + "도와주세요" 녹음 두 개로 계산)

녹음 방법 (reSpeaker, 카드 번호는 arecord -l로 확인. 2채널로 녹음한 뒤 채널을 골라 분석):
  arecord -D plughw:<카드번호>,0 -f S16_LE -r 16000 -c 2 -d 20 quiet.wav        # 조용한 방 20초 (아무 소리도 내지 않기)
  arecord -D plughw:<카드번호>,0 -f S16_LE -r 16000 -c 2 -d 90 speech.wav       # 1m 거리에서 "도와주세요"를 5초 간격으로 15번쯤

사용법:  python3 calibrate_mic.py --quiet quiet.wav --speech speech.wav [--channel 0]
출력:    잡음 수준, 발화 크기 분포, 추천 --gate-db, 경고(너무 작음, 찌그러짐, 채널 차이)
기준: 추천값 = min(발화 하위 10% 크기 - 3dB, -25dBFS), 그리고 잡음 95% + 8dB 이상이어야 함. 2026-11-08 S23 녹음에서 -28이 나온 방식과 같음.
"""
import argparse
import wave

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--quiet", required=True)
ap.add_argument("--speech", required=True)
ap.add_argument("--channel", type=int, default=0, help="2채널 wav에서 분석할 채널 (서비스의 --mic-channel과 같게)")
ap.add_argument("--gate-now", type=float, default=-28.0, help="지금 쓰는 기준 (비교용)")
args = ap.parse_args()
FR = 8000


def load(path, ch):
    """wav는 채널을 골라서 읽고, 그 외(m4a 등)는 faster-whisper의 읽기 함수를 씀. 반환: (샘플, 채널 수, 두 채널 음량 dB 또는 None)"""
    try:
        with wave.open(path, "rb") as w:
            n, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            assert sw == 2, "16비트 wav만 지원"
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if sr != 16000:
            print(f"  주의: {path}의 샘플레이트가 {sr}Hz입니다 (16000Hz로 녹음 권장). 분석은 그대로 진행")
        x = raw.reshape(-1, n)
        lv = [10 * np.log10(float((x[:, c] ** 2).mean()) + 1e-12) for c in range(n)] if n > 1 else None
        return x[:, min(ch, n - 1)].copy(), n, lv
    except wave.Error:
        from faster_whisper.audio import decode_audio as _fw_decode
        try:
            x = _fw_decode(path, sampling_rate=16000)
        except TypeError:                      # faster-whisper와 PyAV 버전이 안 맞을 때: PyAV로 직접 읽기
            import av
            container = av.open(path)
            rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(audio=0):
                chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
            chunks += [f.to_ndarray().reshape(-1) for f in (rs.resample(None) or [])]
            container.close()
            x = np.concatenate(chunks).astype(np.float32) / 32768
        return np.asarray(x, dtype=np.float32), 1, None


def frame_db(x):
    nf = len(x) // FR
    if nf == 0:
        raise SystemExit("녹음이 0.5초보다 짧습니다")
    f = x[:nf * FR].reshape(nf, FR).astype(np.float64)
    return 10 * np.log10((f ** 2).mean(axis=1) + 1e-12)


def segments(db, thr, gap=2):                       # gap 프레임(=1초) 이하로 끊기면 한 발화
    idx = np.flatnonzero(db >= thr)
    segs = []
    for i in idx:
        if segs and i - segs[-1][1] <= gap:
            segs[-1][1] = i
        else:
            segs.append([i, i])
    return [(a, b) for a, b in segs]


q, qn, qlv = load(args.quiet, args.channel)
s, sn, slv = load(args.speech, args.channel)
qdb, sdb = frame_db(q), frame_db(s)
noise_med, noise_p95, noise_max = float(np.median(qdb)), float(np.percentile(qdb, 95)), float(qdb.max())
thr = noise_p95 + 6
segs = segments(sdb, thr)
peaks = np.array([sdb[a:b + 1].max() for a, b in segs]) if segs else np.array([])
print(f"[조용한 방] {len(q) / 16000:.0f}초 | 프레임 음량 중앙 {noise_med:.1f} / 95% {noise_p95:.1f} / 최대 {noise_max:.1f} dBFS")
print(f"[발화] {len(s) / 16000:.0f}초 | 발화로 본 구간 {len(segs)}개 (잡음 95% + 6dB = {thr:.1f}dBFS 이상)")
if len(peaks) == 0:
    raise SystemExit("발화 구간을 찾지 못했습니다. 마이크 이득을 올리거나 더 크게·가깝게 말해서 다시 녹음하세요.")
p10, med, mn, mx = float(np.percentile(peaks, 10)), float(np.median(peaks)), float(peaks.min()), float(peaks.max())
print(f"  발화 최대 음량: 하위 10% {p10:.1f} / 중앙 {med:.1f} / 최소 {mn:.1f} / 최대 {mx:.1f} dBFS")
rec = min(p10 - 3.0, -25.0)
floor = noise_p95 + 8.0
print()
print(f"추천 --gate-db {rec:.0f}  (지금 쓰는 값 {args.gate_now:.0f})")
warns = []
if rec < floor:
    warns.append(f"추천값({rec:.0f})이 잡음(95% {noise_p95:.1f} + 8dB = {floor:.1f})보다 낮습니다. 잡음 구간이 Whisper로 많이 넘어갑니다. 마이크 이득을 올리거나 소리 나는 가전을 멀리하세요.")
    rec = max(rec, floor)
    print(f"  → 잡음을 피하려면 --gate-db {rec:.0f} 이상이 필요하지만, 그러면 작은 발화는 걸러집니다.")
if med < -35:
    warns.append(f"발화 중앙 {med:.1f}dBFS로 너무 작습니다. alsamixer에서 Capture 이득을 올리거나 마이크에 더 가까이 말하세요.")
clip = float((np.abs(s) >= 0.99).mean())
if clip > 0.001:
    warns.append(f"발화 녹음에서 {clip:.2%}가 찌그러졌습니다. alsamixer에서 Capture 이득을 낮추세요.")
if slv and len(slv) == 2:
    print(f"채널별 평균 음량: 채널0 {slv[0]:.1f} / 채널1 {slv[1]:.1f} dB (분석에 쓴 채널 {args.channel})")
    if abs(slv[0] - slv[1]) > 6:
        louder = int(np.argmax(slv))
        warns.append(f"두 채널 음량이 {abs(slv[0] - slv[1]):.0f}dB 다릅니다. 더 큰 채널({louder})을 --mic-channel로 쓰세요.")
n_hits = int((sdb >= rec).sum())
print(f"이 기준이면 발화 녹음의 {int((peaks >= rec).sum())}/{len(peaks)}개 발화가 기준을 넘고, 조용한 방 프레임 중 기준을 넘는 것은 {int((qdb >= rec).sum())}/{len(qdb)}개입니다.")
for w in warns:
    print("주의:", w)
if not warns:
    print("문제 없음. 확인 후 --gate-db를 위 값으로 쓰세요. 설정 파일이면 \"gate_db\": %.0f" % rec)
