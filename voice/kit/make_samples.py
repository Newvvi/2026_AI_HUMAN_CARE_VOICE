#!/usr/bin/env python3
"""시험 키트용 샘플 만들기 (음성 파이에서 실행, 가상환경 ~/asr)
~/own_test 의 보정·시험 녹음(C1, P1)에서 위급 말을 잘라 내고, 실제 모델로 감지되는 것만 계열별로 골라 저장한다.
배경 소리는 조용한 방 녹음(C0)을 쓴다.
  python3 make_samples.py <저장 폴더> [kws_service.py가 있는 폴더]"""
import os, sys, io, csv, wave, glob, tempfile, contextlib, collections
import numpy as np

out = sys.argv[1]
svc_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi")
sys.path.insert(0, os.path.abspath(svc_dir))
import kws_service as K

OWN = os.environ.get("REC_OUT", os.path.expanduser("~/own_test"))
SR = 16000
PER_FAMILY = 2
NAMES = {"A": "도와주세요", "B": "살려주세요", "C": "사람살려", "D": "119"}


def ch0(p):
    with wave.open(p) as w:
        ch, raw = w.getnchannels(), w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, ch)[:, 0].astype(np.float32) / 32768.0


def save(p, x):
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


os.makedirs(out, exist_ok=True)
c0 = sorted(glob.glob(os.path.join(OWN, "audio", "C0_quiet", "*.wav")))
bg = ch0(c0[0]) if c0 else (np.random.default_rng(0).standard_normal(SR * 30) * 2e-4).astype(np.float32)
save(os.path.join(out, "background.wav"), bg[:SR * 30])

cfg = K.load_cfg(os.path.join(svc_dir, "kws_service.json"))
cfg["mqtt"]["host"] = ""
eng = K.Engine(cfg)
tmp = tempfile.mkdtemp()
snd = K.Sender(cfg, tmp, False)

rows = [r for r in csv.DictReader(open(os.path.join(OWN, "labels.csv"), encoding="utf-8-sig"))
        if r["code"] in ("C1_1m", "P1_1m") and r["timing"] == "detected" and r["family"] in NAMES]
cache, picked = {}, collections.defaultdict(list)
for r in rows:
    fam = r["family"]
    if len(picked[fam]) >= PER_FAMILY or any(q["text"] == r["text"] and q["file"] == r["file"] for q in picked[fam]):
        continue
    if r["file"] not in cache:
        cache[r["file"]] = ch0(os.path.join(OWN, r["file"]))
    x = cache[r["file"]]
    a, b = int((float(r["start_s"]) - 0.4) * SR), int((float(r["end_s"]) + 0.6) * SR)
    cut = x[max(a, 0):b]
    test = np.concatenate([bg[:SR * 8], bg[SR * 8:SR * 8 + len(cut)] + cut, bg[SR * 12:SR * 16]])   # 배경 위에 얹어 실제 모델로 확인
    det = K.Detector(cfg, eng, snd, tmp, tmp, 0, True)
    with contextlib.redirect_stdout(io.StringIO()):                 # 확인용 감지 출력은 숨김
        for i in range(0, len(test) - K.HOP + 1, K.HOP):
            det.push(test[i:i + K.HOP])
        if det.ev is not None:
            det.try_finish(det.n / SR, force=True)
    if det.events:
        r["cut"] = cut
        picked[fam].append(r)
k = 0
for fam in "ABCD":
    for r in picked[fam]:
        k += 1
        name = "%02d_%s_%s.wav" % (k, fam, r["text"].replace(" ", ""))
        save(os.path.join(out, name), r["cut"])
        print("  %s  (%s)" % (name, r["file"]))
print("샘플 %d개 + background.wav → %s" % (k, out))
