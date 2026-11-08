"""파이에서 Whisper(tiny/base/small) 처리 속도 시험: 마이크 없이 WAV 구간 파일로 잽니다.

준비:  pip install faster-whisper numpy   (처음 실행할 때 모델을 내려받으므로 인터넷이 필요합니다)
사용법: python3 bench_asr_pi.py pi_bench [--threads 4] [--sizes tiny,base,small]
        (pi_bench 폴더는 07·09 노트북이 드라이브에 만든 것을 파이로 복사한 것)
출력:   구간당 처리 시간, 실시간 대비 배율(1보다 작으면 실시간보다 빠름), 온도·성능 제한 표시, 인식된 글자
"""
import argparse
import csv
import re
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

END = r"(?:주세요|주십시오|주십쇼|줘요|줘(?!서)|주라|달라|주셔요)"
KEY = re.compile(r"(?:^|[\s,.!?])(?:(?:도와|살려|구해)\s*%s|사람\s*살려|119\s*(?:불러|신고|에\s*전화)|일일구\s*(?:불러|신고))" % END)

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--sizes", default="tiny,base,small")
args = ap.parse_args()


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip() or "?"
    except Exception:
        return "?"


def read_wav(p):
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, p
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


folder = Path(args.folder)
items = list(csv.DictReader(open(folder / "bench_list.csv", encoding="utf-8-sig")))
audio = [read_wav(folder / r["file"]) for r in items]
print(f"구간 {len(items)}개 (합계 {sum(len(a) for a in audio) / 16000:.0f}초), 스레드 {args.threads}")
print("시작 온도:", sh(["vcgencmd", "measure_temp"]), "| 성능제한:", sh(["vcgencmd", "get_throttled"]))

for size in args.sizes.split(","):
    model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=args.threads)
    list(model.transcribe(audio[0], language="ko", beam_size=1)[0])          # 워밍업(시간에 포함 안 함)
    times, e_hit, c_hit, lines = [], 0, 0, []
    for r, a in zip(items, audio):
        t0 = time.time()
        segs, _ = model.transcribe(a, language="ko", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                  temperature=0.0, without_timestamps=True, max_new_tokens=32)
        text = " ".join(s.text.strip() for s in segs)
        times.append(time.time() - t0)
        hit = bool(KEY.search(text))
        if r["source"] == "E":
            e_hit += hit
        else:
            c_hit += hit
        lines.append(f"  [{r['source']}] {len(a) / 16000:4.1f}초 -> {times[-1]:5.2f}초 | {'단어' if hit else '    '} | {text[:34]}")
    n_e = sum(r["source"] == "E" for r in items)
    total_audio = sum(len(a) for a in audio) / 16000
    print(f"\n=== {size} (int8) ===")
    print("\n".join(lines))
    print(f"평균 {np.mean(times):.2f}초/구간, 최대 {max(times):.2f}초, 실시간 대비 {sum(times) / total_audio:.2f}배 "
          f"| 위급 구간 인식 {e_hit}/{n_e}, 일반 구간 오인식 {c_hit}/{len(items) - n_e}")
    print("온도:", sh(["vcgencmd", "measure_temp"]), "| 성능제한:", sh(["vcgencmd", "get_throttled"]), "(0x80000은 '과거에 온도 제한이 걸린 적 있음'이라는 뜻이고, 새로 걸리면 값이 바뀜)")
