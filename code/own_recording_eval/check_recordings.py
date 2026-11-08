"""녹음 파일 품질 점검: 6_own 폴더를 훑어서 파일마다 음량, 찢어짐, 배경 잡음, 고음 잡음, 끊김, 좌우 위상을 계산합니다.
(오디오 내용은 어디에도 전송하지 않고, 숫자와 파일 이름만 출력합니다.)

사전 설치:  pip install numpy imageio-ffmpeg
사용법:    python check_recordings.py "<6_own 폴더>"
결과는 화면에 출력되고 recording_report.csv로도 저장됩니다. (3시간짜리도 한 파일에 몇 분 걸립니다)
"""
import csv
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np

EXTS = {".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".3gp", ".wma", ".amr"}
SR = 16000
FRAME = SR // 2            # 0.5초 단위로 분석
CHUNK_FRAMES = 120         # 60초씩 읽어서 메모리를 아낌
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
HF_BIN = np.fft.rfftfreq(FRAME, 1 / SR) >= 4000
WINDOW = np.hanning(FRAME)


def probe(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
    m = re.search(r"Audio: (\w+).*?, (\d+) Hz, ([^,\n]+)", r.stderr)
    return (m.group(1), int(m.group(2)), m.group(3).strip()) if m else ("?", 0, "?")


def read_exact(f, n):
    buf = b""
    while len(buf) < n:
        b = f.read(n - len(buf))
        if not b:
            break
        buf += b
    return buf


def analyze(path):
    codec, sr0, layout = probe(path)
    ch = 1 if layout.startswith(("mono", "1 channel")) else 2   # 모노를 2채널로 늘리면 ffmpeg가 -3dB 줄이므로 그대로 읽음
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", str(ch), "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    sL = sR = sLL = sRR = sLR = 0.0
    n_all = n_clip = n_zero_frames = 0
    power = 0.0
    peak = 0.0
    rms_list, hf_list = [], []
    while True:
        raw = read_exact(p.stdout, CHUNK_FRAMES * FRAME * 2 * ch)
        if not raw:
            break
        x = np.frombuffer(raw[: len(raw) // (2 * ch) * (2 * ch)], dtype=np.int16).reshape(-1, ch).astype(np.float32) / 32768
        if ch == 2:
            L, R = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
            sL += L.sum(); sR += R.sum(); sLL += (L * L).sum(); sRR += (R * R).sum(); sLR += (L * R).sum()
            m = x[:, 0] if L.std() >= R.std() else x[:, 1]    # 더 큰 채널로 분석
        else:
            m = x[:, 0]
        n_all += len(m)
        n_clip += int((np.abs(m) >= 0.99).sum())
        power += float((m.astype(np.float64) ** 2).sum())
        peak = max(peak, float(np.abs(m).max()))
        nf = len(m) // FRAME
        if nf:
            fr = m[: nf * FRAME].reshape(nf, FRAME)
            rms_list.append(np.sqrt((fr ** 2).mean(axis=1)))
            n_zero_frames += int((np.abs(fr).max(axis=1) == 0).sum())
            spec = np.abs(np.fft.rfft(fr * WINDOW, axis=1)) ** 2
            hf_list.append(spec[:, HF_BIN].sum(axis=1) / (spec.sum(axis=1) + 1e-20))
    p.wait()
    if n_all == 0:
        return {"오류": "읽기 실패"}
    n = n_all
    corr = None
    vL, vR = sLL / n - (sL / n) ** 2, sRR / n - (sR / n) ** 2
    if vL > 1e-12 and vR > 1e-12:
        corr = (sLR / n - (sL / n) * (sR / n)) / np.sqrt(vL * vR)
    rms = np.concatenate(rms_list) if rms_list else np.zeros(1)
    hf = np.concatenate(hf_list) if hf_list else np.zeros(1)
    fdb = 20 * np.log10(rms + 1e-9)
    live = rms > 0
    noise_floor = float(np.percentile(fdb[live], 10)) if live.any() else -120.0
    quiet = fdb <= np.percentile(fdb, 20)
    return {
        "길이(분)": n / SR / 60, "형식": f"{codec} {sr0}Hz {layout}",
        "전체음량(dBFS)": 10 * np.log10(power / n + 1e-12), "큰소리(dBFS)": float(np.percentile(fdb, 95)),
        "최대(dBFS)": 20 * np.log10(peak + 1e-9), "잡음바닥(dBFS)": noise_floor,
        "고음비중(조용한구간)": float(np.median(hf[quiet])), "소리있는비율": float((fdb > noise_floor + 12).mean()),
        "찢어짐(%)": 100 * n_clip / n, "정확한무음(%)": 100 * n_zero_frames / max(len(rms), 1),
        "좌우상관": None if corr is None else float(corr),
    }


def judge(r):
    bad, warn, info = [], [], []
    if r["찢어짐(%)"] > 0.1: bad.append(f"소리가 찢어짐(클리핑) {r['찢어짐(%)']:.2f}%")
    if r["정확한무음(%)"] > 0.5: bad.append(f"디지털 무음 {r['정확한무음(%)']:.1f}% (노이즈 게이트·끊김 의심)")
    if r["큰소리(dBFS)"] < -35: warn.append("큰 소리도 -35dBFS 미만: 너무 작게 녹음됨")
    if r["잡음바닥(dBFS)"] > -45: warn.append("배경 잡음이 큼(조용한 구간도 -45dBFS 이상)")
    if r["고음비중(조용한구간)"] > 0.45: warn.append("조용한 구간에 고음(4kHz 이상) 잡음 비중이 큼: 시시거림 가능")
    if r["길이(분)"] < 5 / 60: warn.append("5초보다 짧음")
    if r["좌우상관"] is not None and r["좌우상관"] < 0: info.append("좌우 위상 반대(변환 때 한 채널만 쓰므로 문제 아님)")
    return ("문제" if bad else "주의" if warn else "OK"), bad + warn + info


def main():
    root = Path(sys.argv[1])
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS)
    print(f"{len(files)}개 파일 점검 시작 ({root})\n")
    rows = []
    for f in files:
        rel = str(f.relative_to(root))
        try:
            r = analyze(f)
        except Exception as e:
            r = {"오류": str(e)[:80]}
        if "오류" in r:
            print(f"[읽기 실패] {rel}: {r['오류']}")
            rows.append({"파일": rel, "판정": "읽기 실패", "사유": r["오류"]})
            continue
        verdict, reasons = judge(r)
        print(f"[{verdict}] {rel}\n    {r['길이(분)']:.1f}분 | {r['형식']} | 전체 {r['전체음량(dBFS)']:.0f} / 큰소리 {r['큰소리(dBFS)']:.0f} / "
              f"잡음바닥 {r['잡음바닥(dBFS)']:.0f} dBFS | 고음비중 {r['고음비중(조용한구간)']:.2f} | 찢어짐 {r['찢어짐(%)']:.2f}% | "
              f"소리있는비율 {r['소리있는비율']:.0%} | 좌우상관 {'-' if r['좌우상관'] is None else format(r['좌우상관'], '.2f')}")
        for s in reasons:
            print(f"    - {s}")
        rows.append({"파일": rel, "판정": verdict, "사유": " / ".join(reasons), **r})
    if rows:
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open("recording_report.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader(); w.writerows(rows)
        by = {}
        for r in rows:
            top = Path(r["파일"]).parts[0] if len(Path(r["파일"]).parts) > 1 else "(루트)"
            d = by.setdefault(top, {"OK": 0, "주의": 0, "문제": 0, "읽기 실패": 0, "분": 0.0})
            d[r["판정"]] += 1
            d["분"] += r.get("길이(분)", 0) or 0
        print("\n== 폴더별 요약 ==")
        for k, d in by.items():
            print(f"{k}: 총 {d['분']:.0f}분 | OK {d['OK']} / 주의 {d['주의']} / 문제 {d['문제']} / 읽기 실패 {d['읽기 실패']}")
        print("\n저장: recording_report.csv")


if __name__ == "__main__":
    main()
