"""
make_soak_wav.py: 연속 일상 소리에 위급 발화(카나리아)를 일정 간격으로 끼워 넣은 장시간 시험 파일을 만든다.
사용법:  python3 make_soak_wav.py --base c.m4a --canary e1.m4a e2.m4a --every 20 --minutes 180 --out soak.wav
결과:    soak.wav (16kHz 모노 16비트), soak_canaries.json (끼워 넣은 구간 [시작초, 끝초, 출처])
이 파일을 `voice_service.py --wav soak.wav --realtime`으로 흘려 보낸 뒤 `check_soak.py`로 감지·오경보를 확인한다.
"""
import argparse
import json
import wave

import numpy as np
from faster_whisper.audio import decode_audio as _fw_decode


def decode_audio(path, sampling_rate=16000):
    try:
        return _fw_decode(path, sampling_rate=sampling_rate)
    except TypeError:                      # faster-whisper와 PyAV 버전이 안 맞을 때: PyAV로 직접 읽기
        import av
        container = av.open(path)
        rs = av.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
        chunks = []
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in (rs.resample(None) or [])]
        container.close()
        return np.concatenate(chunks).astype(np.float32) / 32768

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True, help="바탕이 되는 연속 일상 소리 (예: 3시간 녹음)")
ap.add_argument("--canary", nargs="+", required=True, help="끼워 넣을 위급 발화 녹음 (앞 12초를 번갈아 사용)")
ap.add_argument("--every", type=float, default=20, help="끼워 넣는 간격(분)")
ap.add_argument("--minutes", type=float, default=180, help="바탕 소리에서 사용할 길이(분)")
ap.add_argument("--len", type=float, default=12, help="끼워 넣는 길이(초)")
ap.add_argument("--out", default="soak.wav")
args = ap.parse_args()
SR = 16000
base = np.asarray(decode_audio(args.base, sampling_rate=SR), dtype=np.float32)[: int(args.minutes * 60 * SR)]
cans = [np.asarray(decode_audio(p, sampling_rate=SR), dtype=np.float32)[: int(args.len * SR)] for p in args.canary]
marks, k = [], 0
t = args.every * 60
while t + args.len < len(base) / SR:
    c = cans[k % len(cans)]
    base[int(t * SR):int(t * SR) + len(c)] = c
    marks.append([round(t, 1), round(t + len(c) / SR, 1), args.canary[k % len(cans)]])
    k += 1; t += args.every * 60
with wave.open(args.out, "wb") as w:                       # 3시간짜리도 메모리를 아끼도록 10분씩 나눠서 씀
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    step = SR * 600
    for i in range(0, len(base), step):
        w.writeframes((np.clip(base[i:i + step], -1, 1) * 32767).astype(np.int16).tobytes())
json.dump(marks, open(args.out.rsplit(".", 1)[0] + "_canaries.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"저장: {args.out} ({len(base) / SR / 60:.0f}분), 끼워 넣은 위급 발화 {len(marks)}개")
