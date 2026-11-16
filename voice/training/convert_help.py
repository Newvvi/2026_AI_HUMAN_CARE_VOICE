"""AI Hub 위급상황 음성 데이터에서 '목표 말 전용' 클립 데이터셋을 만든다.

- 양성: 도움요청 클래스의 네 계열 (A 도와주세요 / B 살려·구해주세요 / C 사람 살려 / D 119·일일구 불러·신고)
- 어려운 오답: 도움요청 안의 개 경고(문다, 개조심해 ...), 응급의료, 낙상 말소리
- 제외: 신고·경찰·구조 계열 등 (양성도 오답도 아님, 학습에 쓰지 않음)
- 4초 창으로 자르지 않고 클립을 그대로(16kHz 모노, 이벤트 구간 그대로) 저장한다.
  학습 노트북에서 배경 소리에 넣어 조합한다. (예전처럼 짧은 클립을 반복해서 채우지 않는다.)

사용:
  python convert_help.py plan                 규모와 문장별 역할을 확인 (아무것도 저장하지 않음)
  python convert_help.py run                  변환
  python convert_help.py extra_plan           추가 데이터(help_data2)의 규모 확인
  python convert_help.py extra                추가 데이터 변환: 신고·와주세요 계열(E, 알림 대상이 아닌 종류)과 양성 추가분
      (기본 데이터 help_data가 바탕화면이나 LAB 아래에 있으면 자동으로 찾는다. 다른 곳이면 --base "경로")
  python convert_help.py plan <01.데이터 경로> <출력 폴더>   (경로를 바꿀 때)
기본 출력 폴더는 바탕화면의 help_data (추가 데이터는 help_data2) 이다. 필요 패키지: pip install numpy scipy soundfile
"""
import os, re, io, sys, csv, json, math, time, random, zipfile, collections

try:
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
except ImportError as e:
    print("Missing package:", e)
    print("Run this, then run again:  pip install numpy scipy soundfile")
    raise SystemExit(1)

def _opt(name):
    """--base 경로 처럼 이름을 붙인 옵션을 꺼낸다(순서와 상관없이 쓸 수 있다)"""
    if name in sys.argv:
        i = sys.argv.index(name)
        v = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        del sys.argv[i:i + 2]
        return v
    return None

OPT_BASE, OPT_OUT, OPT_ROOT = _opt("--base"), _opt("--out"), _opt("--root")
mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
ROOT = OPT_ROOT or (sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\Parkj\Desktop\LAB\2_raw_data\088.위급상황_음성-음향_데이터\01.데이터")
DESK = os.path.join(os.path.expanduser("~"), "Desktop")
EXTRA = mode.startswith("extra")
OUT = OPT_OUT or (sys.argv[3] if len(sys.argv) > 3 else os.path.join(DESK, "help_data2" if EXTRA else "help_data"))

def find_base():
    """이미 변환한 기본 데이터(help_data) 폴더를 찾는다: 바탕화면, 그 아래 LAB 폴더(깊이 4까지)"""
    if OPT_BASE:
        return OPT_BASE
    if len(sys.argv) > 4:
        return sys.argv[4]
    cands = [os.path.join(DESK, "help_data")]
    lab = os.path.join(DESK, "LAB")
    if os.path.isdir(lab):
        for dp, dn, fn in os.walk(lab):
            if dp[len(lab):].count(os.sep) >= 4:
                dn[:] = []
            if os.path.basename(dp) == "help_data":
                cands.append(dp)
    for c in cands:
        if os.path.exists(os.path.join(c, "meta_Training.csv")):
            return c
    return cands[0]

BASE = find_base()
EXTRA_FAMILY = {"A": 2200, "B": 2400, "C": 800, "D": 2200}    # 추가로 담을 양성 수(기본 800개에 더함)
E_NOTE_CAP, E_TOTAL = 100, 2400                               # E: 문장당·전체 최대 클립 수
E_SKIP = re.compile(r"(112|불러|구조|구급|응급차)")             # 목표 말과 뜻·소리가 너무 가까운 문장은 E로도 쓰지 않음

SR = 16000
CAP_FAMILY = 800            # Training: 계열(A~D)마다 최대 클립 수 (Validation은 전부)
STRATA = (("senior", 0.30), ("female", 0.35), ("other", 0.35))   # 노인 / 여성(노인 제외) / 그 밖
CAP_NOTE = {"neg_dog": 60, "neg_med": 40, "neg_fall": 40}        # Training: 문장당 최대 클립 수
VAL_NEG_DIV = 2             # Validation의 오답은 상한을 이 값으로 나눔
CLIP_MIN_SEC, CLIP_MAX_SEC = 0.8, 8.0
LEN_TOL = 0.15              # wav 길이가 JSON 이벤트 구간 길이와 ±15% 안이어야 함
SHARD_CLIPS = 1500
SEED = 0
rng = random.Random(SEED)

# ---------- 문장 -> 역할 ----------
def norm(t):
    return re.sub(r"[\s\.,!?~…\-'\"“”‘’]", "", str(t))

FAMILIES = [
    ("D", re.compile(r"(119|일일구)(좀)?(불러|신고|에신고|에전화|전화)")),
    ("C", re.compile(r"사람(좀)?살려")),
    ("A", re.compile(r"도와(주세요|줘요|줘|주십시오|주실)")),
    ("B", re.compile(r"(살려|구해)(주세요|줘요|줘|주십시오|주실)")),
]
DOG = re.compile(r"(개|강아지|문다|벌집|물지|물어)")      # 동물·벌 경고 말(도움 요청이 아님)
EXCL = re.compile(r"(신고|경찰|구조|구급|응급차|출동|병원|와주세요|누구없|누구있|112|부탁)")

def role_of(cid, note):
    n = norm(note)
    fam = next((name for name, rx in FAMILIES if rx.search(n)), None)
    if cid == "14":
        if fam:
            return "pos_" + fam
        if DOG.search(n):
            return "neg_dog"
        if EXCL.search(n):
            return "excl"
        return "other"          # 어디에도 안 맞음: 사용하지 않고 계획 화면에 보여 줌
    if cid in ("7", "10"):
        if fam:
            return "excl"        # 다른 클래스에 목표 말이 있으면 어느 쪽에도 쓰지 않음
        return "neg_med" if cid == "7" else "neg_fall"
    return None

# ---------- zip 찾기 ----------
labels = {}
sources = collections.defaultdict(list)
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.lower().endswith(".zip"):
            continue
        p = os.path.join(dp, f)
        sp = "Training" if "Training" in p else ("Validation" if "Validation" in p else "?")
        m = re.match(r"^(.*)_(\d+)\.zip$", f)
        if m:
            sources[(sp, m.group(1))].append(p)
        else:
            labels[(sp, f[:-4])] = p
if not labels:
    sys.exit("라벨 zip을 찾지 못했습니다. 경로를 확인하세요: " + ROOT)

zcache = {}
def zopen(p):
    if p not in zcache:
        zcache[p] = zipfile.ZipFile(p)
    return zcache[p]

report = []
def log(s=""):
    print(s)
    report.append(str(s))

def num(x):
    try:
        return float(x)
    except Exception:
        return None

# ---------- 후보 ----------
def build_candidates(split):
    cands, skipped = [], collections.Counter()
    for (sp, cls), lp in sorted(labels.items()):
        if sp != split:
            continue
        cid = cls.split(".")[0]
        if cid not in ("14", "7", "10") or (sp, cls) not in sources:
            continue
        winfo = {}
        for sp_path in sources[(sp, cls)]:
            for i in zopen(sp_path).infolist():
                if i.filename.lower().endswith(".wav"):
                    st = os.path.splitext(os.path.basename(i.filename))[0]
                    if st.endswith("_label"):
                        st = st[:-6]
                    winfo[st] = (sp_path, i)
        zl = zopen(lp)
        for info in zl.infolist():
            if not info.filename.lower().endswith(".json"):
                continue
            stem = os.path.splitext(os.path.basename(info.filename))[0]
            if stem not in winfo:
                skipped[cls + ": no wav"] += 1
                continue
            try:
                d = json.loads(zl.read(info).decode("utf-8-sig"))
                an = (d.get("annotations") or [{}])[0]
                if an.get("audioType") != "voice":
                    skipped[cls + ": not voice"] += 1
                    continue
                ar = an.get("area") or {}
                s, e = num(ar.get("start")), num(ar.get("end"))
                if s is None or e is None or e <= s:
                    skipped[cls + ": bad area"] += 1
                    continue
                alen = e - s
                if not (CLIP_MIN_SEC <= alen <= CLIP_MAX_SEC):
                    skipped[cls + ": length out of range"] += 1
                    continue
                note = str(an.get("note"))
                role = role_of(cid, note)
                cands.append({
                    "split": sp, "cls": cls, "role": role, "stem": stem, "alen": alen, "note": note,
                    "situation": str((an.get("categories") or {}).get("category_03")),
                    "gender": str(an.get("gender")), "generation": str(an.get("generation")),
                    "place": str((d.get("audio") or {}).get("acquisitionPlace")), "dist": str(an.get("soundDistance")),
                    "zip": winfo[stem][0], "info": winfo[stem][1]})
            except Exception:
                skipped[cls + ": json error"] += 1
    return cands, skipped

# ---------- 선택 ----------
def rr_pick(items, k):
    """문장(note)별로 돌아가며 무작위로 k개를 뽑는다."""
    by = collections.defaultdict(list)
    for c in items:
        by[c["note"]].append(c)
    for v in by.values():
        rng.shuffle(v)
    keys = sorted(by)
    idx = {x: 0 for x in keys}
    out, active = [], keys[:]
    while active and len(out) < k:
        nxt = []
        for x in active:
            if len(out) >= k:
                break
            out.append(by[x][idx[x]])
            idx[x] += 1
            if idx[x] < len(by[x]):
                nxt.append(x)
        active = nxt
    return out

def stratum(c):
    if c["generation"] == "senior":
        return "senior"
    return "female" if c["gender"] == "female" else "other"

def select(cands, split):
    sel = []
    for fam in "ABCD":
        items = [c for c in cands if c["role"] == "pos_" + fam]
        if split == "Validation" or len(items) <= CAP_FAMILY:
            sel.extend(items)
            continue
        pools = {s: [c for c in items if stratum(c) == s] for s, _ in STRATA}
        got, taken = [], set()
        for s, share in STRATA:
            pick = rr_pick(pools[s], int(CAP_FAMILY * share))
            got.extend(pick)
            taken.update(id(c) for c in pick)
        rest = [c for c in items if id(c) not in taken]      # 모자란 층의 몫을 나머지에서 채움
        got.extend(rr_pick(rest, CAP_FAMILY - len(got)))
        sel.extend(got)
    for role, cap in CAP_NOTE.items():
        cap = cap if split == "Training" else max(1, cap // VAL_NEG_DIV)
        by = collections.defaultdict(list)
        for c in cands:
            if c["role"] == role:
                by[c["note"]].append(c)
        for k in sorted(by):
            v = by[k]
            rng.shuffle(v)
            sel.extend(v[:cap])
    return sel

def select_extra(cands, split):
    """기본 데이터에 없는 클립 중에서: E(신고·와주세요 계열)와 양성 추가분"""
    have = set()
    mp = os.path.join(BASE, "meta_%s.csv" % split)
    if not os.path.exists(mp):
        sys.exit("기본 데이터(help_data)를 찾지 못했습니다. 찾아본 곳: " + mp + "\n"
                 "추가 데이터는 '이미 만든 기본 데이터와 겹치지 않는 클립'만 골라서, 기본 데이터의 목록(meta_Training.csv)이 필요합니다.\n"
                 "1) help_data 폴더를 옮겼다면 위치를 알려 주세요:\n"
                 "     python convert_help.py extra --base \"help_data 폴더 경로\"\n"
                 "2) 폴더를 지웠다면 python convert_help.py run 으로 2분 만에 다시 만들 수 있습니다(같은 설정이라 같은 클립이 선택됩니다).")
    with open(mp, encoding="utf-8-sig") as f:
        have = {r["stem"] for r in csv.DictReader(f)}
    sel = []
    es = [c for c in cands if c["role"] == "excl" and c["cls"].startswith("14") and not E_SKIP.search(norm(c["note"]))]
    for c in es:
        c["role"] = "req_E"
    by = collections.defaultdict(list)
    for c in es:
        by[c["note"]].append(c)
    capped = []
    for k in sorted(by):
        rng.shuffle(by[k])
        capped.extend(by[k][:E_NOTE_CAP if split == "Training" else E_NOTE_CAP // 4])
    sel.extend(rr_pick(capped, E_TOTAL if split == "Training" else E_TOTAL // 4))
    if split == "Training":
        for fam, cap in EXTRA_FAMILY.items():
            rest = [c for c in cands if c["role"] == "pos_" + fam and c["stem"] not in have]
            sel.extend(rr_pick(rest, cap))
    return sel

# ---------- 오디오 ----------
def load_16k(b):
    x, sr = sf.read(io.BytesIO(b), dtype="float32", always_2d=True)
    ch = x.shape[1]
    corr, anti = 1.0, 0
    if ch >= 2:
        l, r = x[:, 0], x[:, 1]
        if np.std(l) > 1e-6 and np.std(r) > 1e-6:
            corr = float(np.corrcoef(l, r)[0, 1])
        if corr < -0.2:                  # 좌우가 반대 위상이면 평균이든 차이든 한쪽 성분만 남아 말소리가 사라질 수 있다.
            rl, rr = float(np.sqrt(np.mean(l ** 2))), float(np.sqrt(np.mean(r ** 2)))
            x = (l if rl >= rr else r)[:, None]   # 그래서 더 큰 채널 하나만 쓴다 (YAMNet 실험 E-302에서 확인한 방식)
            anti = 1
        else:
            x = x.mean(axis=1, keepdims=True)
    x = x[:, 0]
    if sr != SR:
        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g).astype("float32")
    return x, sr, ch, corr, anti

class Writer:
    def __init__(self, split):
        self.split, self.shard, self.n = split, 0, 0
        self.buf, self.rows, self.off, self.all_rows = [], [], 0, []
    def fname(self):
        return "clips_%s_%03d.npy" % (self.split, self.shard)
    def add(self, x16, row):
        row["shard_file"], row["offset"], row["length"] = self.fname(), self.off, len(x16)
        self.buf.append(x16)
        self.off += len(x16)
        self.all_rows.append(row)
        self.n += 1
        if len(self.buf) >= SHARD_CLIPS:
            self.flush()
    def flush(self):
        if self.buf:
            np.save(os.path.join(OUT, self.fname()), np.concatenate(self.buf))
            self.buf, self.off = [], 0
            self.shard += 1
    def close(self):
        self.flush()
        if self.all_rows:
            cols = ["shard_file", "offset", "length", "split", "role", "cls", "stem", "note", "situation", "gender",
                    "generation", "place", "dist", "src_sr", "src_ch", "ch_corr", "antiphase", "clip_sec", "rms_dbfs", "peak_dbfs"]
            with open(os.path.join(OUT, "meta_%s.csv" % self.split), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(self.all_rows)

# ---------- 계획 ----------
t0 = time.time()
plans = {}
for split in ("Training", "Validation"):
    cands, skipped = build_candidates(split)
    sel = select_extra(cands, split) if EXTRA else select(cands, split)
    plans[split] = sel
    log("")
    log("===== %s =====" % split)
    cnt_c = collections.Counter(c["role"] for c in cands)
    cnt_s = collections.Counter(c["role"] for c in sel)
    for role in sorted(cnt_c, key=str):
        log("  %-9s 후보 %6d -> 선택 %5d" % (role, cnt_c[role], cnt_s.get(role, 0)))
    log("  선택한 양성의 노인/여성/그 밖:")
    for fam in "ABCD":
        sub = [c for c in sel if c["role"] == "pos_" + fam]
        s = collections.Counter(stratum(c) for c in sub)
        d = collections.Counter(c["dist"] for c in sub)
        log("    %s: %d클립 (노인 %d, 여성 %d, 그 밖 %d) | 거리 %s" % (
            fam, len(sub), s["senior"], s["female"], s["other"], ", ".join("%s:%d" % kv for kv in d.most_common())))
    if skipped:
        log("  선택 전에 뺀 것: " + ", ".join("%s=%d" % kv for kv in sorted(skipped.items())))
    if EXTRA:
        log("  [E로 담는 문장] " + ", ".join("%s:%d" % kv for kv in collections.Counter(c["note"] for c in sel if c["role"] == "req_E").most_common()))
    elif split == "Training":
        log("")
        log("  [도움요청 문장별 역할] (어디에도 안 맞는 'other'가 있으면 알려 주세요)")
        notes = collections.defaultdict(lambda: [0, None])
        for c in cands:
            if c["cls"].startswith("14"):
                notes[c["note"]][0] += 1
                notes[c["note"]][1] = c["role"]
        for note, (n, r) in sorted(notes.items(), key=lambda kv: -kv[1][0]):
            log("    %6d  %-9s %s" % (n, r, note))
        log("  [파일 이름 예 10개 - 화자를 알 수 있는지 보려고]")
        for c in rng.sample(cands, min(10, len(cands))):
            log("    %s | %s | %s | %s" % (c["stem"], c["role"], c["gender"], c["generation"]))

if mode not in ("run", "extra"):
    log("")
    log("plan only. 변환하려면:  python convert_help.py %s" % ("extra" if EXTRA else "run"))
    path = os.path.join(DESK, "convert_help_%splan.txt" % ("extra_" if EXTRA else ""))
else:
    os.makedirs(OUT, exist_ok=True)
    fails = collections.Counter()
    for split in ("Training", "Validation"):
        sel = plans[split][:]
        rng.shuffle(sel)
        wr = Writer(split)
        for n, c in enumerate(sel, 1):
            try:
                x, sr, ch, corr, anti = load_16k(zopen(c["zip"]).read(c["info"]))
            except Exception:
                fails["decode error"] += 1
                continue
            sec = len(x) / float(SR)
            if abs(sec - c["alen"]) > LEN_TOL * c["alen"]:
                fails["wav length != area length"] += 1
                continue
            rms = 20 * math.log10(float(np.sqrt(np.mean(x.astype("float64") ** 2))) + 1e-9)
            pk = 20 * math.log10(float(np.max(np.abs(x))) + 1e-9)
            x16 = np.round(np.clip(x, -1.0, 1.0) * 32767).astype("int16")
            row = dict(c)
            row.update(src_sr=sr, src_ch=ch, ch_corr=round(corr, 3), antiphase=anti,
                       clip_sec=round(sec, 2), rms_dbfs=round(rms, 1), peak_dbfs=round(pk, 1))
            wr.add(x16, row)
            if n % 500 == 0:
                print("  %s: %d/%d 클립, %.0f초" % (split, n, len(sel), time.time() - t0))
        wr.close()
        log("done %s: 클립 %d개, 파일 %d개" % (split, wr.n, wr.shard))
    if fails:
        log("dropped: " + ", ".join("%s=%d" % kv for kv in sorted(fails.items())))
    log("elapsed %.0f s. output: %s" % (time.time() - t0, OUT))
    path = os.path.join(OUT, "convert_help_report.txt")

try:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\n저장: " + path)
except Exception as e:
    print("보고서 저장 실패(화면 출력은 정상):", e)
