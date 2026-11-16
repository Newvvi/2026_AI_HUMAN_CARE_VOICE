#!/usr/bin/env python3
"""Pi 4 + reSpeaker 시험 녹음 도우미 v2  (파이에서 실행, asr 가상환경)

  python3 rec_session.py check          시작 전 점검: 마이크·음성 서비스·설정·디스크·온도 + 10초 소리 크기 확인
  python3 rec_session.py practice       연습 (저장 안 함): 4개만 말해 보며 박자 익히기
  python3 rec_session.py go             안내 세션: 남은 녹음을 순서대로 하나씩 진행 (가장 많이 쓰는 명령)
  python3 rec_session.py go 3m          특정 자리만: quiet / 1m / 3m / side / lying / tv / loud
  python3 rec_session.py go R           학습용(R) 녹음만: 1m → 3m → 옆방 순서로 진행
  python3 rec_session.py P1_1m          코드 하나만 한 번 녹음
  python3 rec_session.py status         진행 현황 (자리별·코드별·남은 개수)
  python3 rec_session.py long N2_night  긴 녹음을 백그라운드로 시작 (SSH가 끊겨도 계속)
  python3 rec_session.py stop           진행 중인 긴 녹음 멈추기
  python3 rec_session.py pack           PC로 옮길 묶음 파일 만들기 (--long 이면 긴 녹음도 포함)
  python3 rec_session.py plan           전체 녹음 목록

옵션: --spk S2 (다른 사람이 녹음할 때), --no-web (휴대폰 화면 끄기), --hours 1.5 (긴 녹음 길이 바꾸기)
녹음 중 휴대폰 화면: 같은 와이파이에서 http://<파이 IP>:8765
"""
import os, sys, csv, json, time, math, wave, shutil, signal, argparse, threading, subprocess, collections, tarfile, unicodedata
import numpy as np

VERSION = "2.0"
HOME = os.path.expanduser("~")
OUTD = os.environ.get("REC_OUT", os.path.join(HOME, "own_test"))
DEV = os.environ.get("REC_DEV", "plughw:CARD=seeed2micvoicec,DEV=0")
CARD = "seeed2micvoicec"
SERVICE = os.environ.get("REC_SERVICE", "voice_service")
SR = 16000
FAKE = os.environ.get("REC_FAKE")              # 시험용: 마이크 대신 가짜 소리 ("1"=정상, "miss5"=5번 빠짐, "split"=말 끊김)
SPEED = float(os.environ.get("REC_SPEED", "1"))  # 시험용: 시간 빨리 감기
PORT = int(os.environ.get("REC_PORT", "8765"))
LEAD, TAIL = 6.0, 6.0          # 녹음 시작 뒤·마지막 말 뒤 조용히 있는 시간
SPEAK_SEC, READY_SEC = 2.5, 2.0  # 초록(말하기) 시간, 노랑(준비) 시간
CHUNK = 1600                   # 0.1초씩 읽음

# ---- 말 목록 (v1과 같음) ----
A1, A2, A3, A4 = "도와주세요", "도와줘요", "누가 좀 도와주세요", "제발 도와주세요"
B1, B2, C1, D1, D2 = "살려주세요", "구해주세요", "사람 살려", "119 불러주세요", "119에 신고해 주세요"
CYCLE16 = [A1, B1, C1, D1, A2, B2, C1, D2, A3, B1, C1, D1, A4, B2, C1, D2]
# 학습용 보강 녹음(R): 살려 5 / 사람 살려 4 / 119 5 / 도와주세요 2 (모델 쪽 요청)
R16 = [B1, C1, D1, B2, A1, C1, D2, B1, D1, C1, B2, D2, A3, B1, C1, D1]
NEAR18 = ["도와주셔서 감사합니다", "도와줘서 고마워", "제가 도와드릴까요", "도와줄 사람 있어요", "살려 줘서 고마워요", "사람이 살려면 물을 마셔야 해요",
          "그 책 구해 줬어요", "구해 주셔서 감사합니다", "119에서 전화 왔어요", "119 안전센터에 다녀왔어요", "이름 좀 불러 주세요", "택시 좀 불러 주세요",
          "살려고 먹는 거예요", "문 좀 열어 주세요", "물 좀 주세요", "여기 좀 봐 주세요", "사람 많이 왔네", "도와주는 사람 많네"]
E8 = ["신고해 주세요", "신고 좀 해주세요", "경찰에 신고해 주세요", "빨리 와 주세요", "누구 없어요", "누가 좀 와 주세요", "경찰 불러줘", "구급차 불러줘요"]
HARD8 = ["아파요", "배가 아파요", "숨이 안 쉬어져요", "넘어졌어요", "미끄러졌어요", "개조심해", "문다", "벌집 조심해"]
NOISE20 = ["쿵(물건 떨어뜨리기)", "문 쾅 닫기", "기침", "박수", "웃음", "으악(비명)", "쿵(물건 떨어뜨리기)", "문 쾅 닫기", "기침", "박수", "웃음", "으악(비명)",
           "쿵(물건 떨어뜨리기)", "으악(비명)", "쿵(물건 떨어뜨리기)", "문 쾅 닫기", "쿵(물건 떨어뜨리기)", "기침", "으악(비명)", "쿵(물건 떨어뜨리기)"]
FAMILY = {A1: "A", A2: "A", A3: "A", A4: "A", B1: "B", B2: "B", C1: "C", D1: "D", D2: "D"}
FAMILY.update({t: "near" for t in NEAR18}); FAMILY.update({t: "E" for t in E8})
FAMILY.update({t: "hard" for t in HARD8}); FAMILY.update({t: "noise" for t in NOISE20})

NORMAL = "평소 대화하듯 보통 크기로 말해요."
WEAK = "쓰러진 직후처럼 숨차고 작게 말해요. 속삭임은 아니에요."
def seq(desc, phrases, n, delay, dist, how, interval=6.5, sset="시험", station="1m"):
    return dict(kind="seq", desc=desc, seq=phrases, files=n, delay=delay, dist=dist, how=how, interval=interval, set=sset, station=station)

PLAN = collections.OrderedDict([
    ("C0_quiet", dict(kind="silence", desc="조용한 방 60초", files=1, sec=60, delay=5, dist="-", how="아무 소리도 내지 마세요. 움직이지도 말아요.", set="보정", station="quiet")),
    ("R1_1m", seq("1m, 평소 목소리 (학습용)", R16, 3, 8, "1m", NORMAL, sset="학습")),
    ("R3_weak_1m", seq("1m, 약한 목소리 (학습용)", R16, 2, 8, "1m", WEAK, sset="학습")),
    ("C1_1m", seq("1m, 평소 목소리 (보정용)", CYCLE16, 1, 8, "1m", NORMAL, sset="보정")),
    ("P1_1m", seq("1m, 평소 목소리", CYCLE16, 3, 8, "1m", NORMAL)),
    ("P5a_weak_1m", seq("1m, 약한 목소리", CYCLE16, 1, 8, "1m", "쓰러진 직후처럼 숨차고 작게 말해요. 속삭임은 아니에요.")),
    ("N4_near", seq("1m, 비슷하지만 도움 요청이 아닌 말", NEAR18, 3, 8, "1m", NORMAL, sset="시험(오답)")),
    ("N5_E", seq("1m, 신고·경찰 계열 (알림 대상 아님)", E8, 2, 8, "1m", NORMAL, sset="시험(오답)")),
    ("N6_hard", seq("1m, 아프다·넘어졌다·개 경고 말", HARD8, 2, 8, "1m", NORMAL, sset="시험(오답)")),
    ("N7_noise", seq("1m, 생활 소리와 비명 (말 없음)", NOISE20, 1, 8, "1m", "화면에 나온 소리를 한 번씩 내요. 말은 하지 않아요.", interval=6.0, sset="시험(오답)")),
    ("R2_3m", seq("3m, 평소 목소리 (학습용)", R16, 2, 15, "3m", NORMAL, sset="학습", station="3m")),
    ("C2_3m", seq("3m, 평소 목소리 (보정용)", CYCLE16, 1, 15, "3m", NORMAL, sset="보정", station="3m")),
    ("P2_3m", seq("3m, 평소 목소리", CYCLE16, 3, 15, "3m", NORMAL, station="3m")),
    ("P5b_weak_3m", seq("3m, 약한 목소리", CYCLE16, 1, 15, "3m", "쓰러진 직후처럼 숨차고 작게 말해요. 속삭임은 아니에요.", station="3m")),
    ("R4_side", seq("옆방, 문 열림 (학습용)", R16, 1, 25, "옆방(열림)", NORMAL, sset="학습", station="side")),
    ("P3_side_open", seq("옆방, 문 열림", CYCLE16, 2, 25, "옆방(열림)", NORMAL, station="side")),
    ("P4_side_closed", seq("옆방, 문 닫힘", CYCLE16, 2, 25, "옆방(닫힘)", NORMAL, station="side")),
    ("P7_lying_2m", seq("바닥에 누워서 2m", CYCLE16, 2, 15, "2m 누움", "바닥에 누운 채(어려우면 앉은 채) 평소 목소리로 말해요.", station="lying")),
    ("P8_tv_1m", seq("TV 켠 채 1m", CYCLE16, 2, 8, "1m+TV", "TV는 평소 볼륨 그대로 두고, 평소 목소리로 말해요.", interval=8.0, station="tv")),
    ("P6_loud_1m", seq("1m, 큰 목소리(외침)", CYCLE16, 1, 8, "1m", "멀리 있는 사람을 부르듯 크게 외쳐요.", station="loud")),
    ("N1_day", dict(kind="long", desc="낮 활동 3시간 (TV·대화·통화·요리·청소를 평소처럼, 위급한 말은 하지 않음)", hours=3.0, files=1, set="시험(오답)", station="long")),
    ("N2_night", dict(kind="long", desc="밤 8시간 (평소 취침 환경, 안정성 시험 겸함)", hours=8.0, files=1, set="시험(오답)", station="long")),
    ("N3_tv_alarm", dict(kind="long", desc="TV 사건사고 뉴스·위급 장면 20분 (울려도 정상일 수 있는 어려운 경우)", hours=1 / 3.0, files=1, set="시험(어려움)", station="long")),
])
STATIONS = collections.OrderedDict([
    ("quiet", ("조용한 방", "마이크는 실제 설치 위치 그대로. 방 안 사람은 움직이지 않을 자리에 있어요.")),
    ("1m", ("마이크에서 1m", "줄자로 마이크에서 1m를 재고 그 자리에 서요(바닥에 테이프로 표시). 마이크를 보고 말해요. 화면이 보이는 자리예요.")),
    ("3m", ("마이크에서 3m", "줄자로 3m를 재고 그 자리에 서요(테이프 표시). 휴대폰 화면을 켜고 들고 가세요.")),
    ("side", ("옆방", "옆방에서 말할 자리와 문 위치를 정해 테이프로 표시하고 매번 같게 해요. 휴대폰 화면을 들고 가세요.")),
    ("lying", ("바닥 2m, 누운 자세", "마이크에서 2m 떨어진 바닥에 누워요. 휴대폰 화면을 얼굴 옆에 두세요.")),
    ("tv", ("TV 켜고 1m", "TV를 평소 볼륨으로 켜고, 마이크에서 1m 자리에 서요. 위급한 장면이 없는 평범한 방송으로 고르세요.")),
    ("loud", ("1m, 외침", "1m 자리에서 크게 외쳐요. 이웃에 미리 양해를 구하거나 낮 시간에 하세요.")),
])
TARGET_OTHER_SPK = {"P1_1m": 1, "P2_3m": 1}   # 다른 사람(S2, S3…)은 이것만

# ---------------------------------------------------------------- 공통
def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def mixer():
    if FAKE:
        return "24", "0.0045xFs"
    try:
        pga = run(["amixer", "-c", CARD, "sget", "PGA"])
        hpf = run(["amixer", "-c", CARD, "sget", "ADC HPF Cut-off"])
        g = next((l.split("Capture")[1].split("[")[0].strip() for l in pga.splitlines() if "Front Left:" in l), None)
        h = next((l.split(":", 1)[1].strip() for l in hpf.splitlines() if "Item0" in l), None)
        return g, h
    except Exception:
        return None, None

def temp():
    if FAKE:
        return None
    t = run(["vcgencmd", "measure_temp"])
    try:
        return float(t.split("=")[1].split("'")[0])
    except Exception:
        return None

def my_ip():
    ip = run(["hostname", "-I"]).split()
    return ip[0] if ip else "<파이 IP>"

def dw(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)

def pad(s, w):
    return s + " " * max(0, w - dw(s))

def mmss(s):
    s = max(0, int(s)); return "%d:%02d" % (s // 60, s % 60)

def path(*p):
    return os.path.join(OUTD, *p)

def sleep(s):
    time.sleep(s / SPEED)

# ---------------------------------------------------------------- 폴더 · 이전 파일 정리
def ensure_dirs():
    for d in ("", "audio", "rejected", "long", ".tmp"):
        os.makedirs(path(d), exist_ok=True)
    migrate_v1()
    readme = path("README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(README)

README = """own_test 폴더 구조 (rec_session.py v2가 만듦)

audio/<코드>/<코드>_<화자>_<번호>.wav   통과한 짧은 녹음 (16kHz, 16비트, 2채널, 평가는 0번 채널)
audio/<코드>/<코드>_<화자>_<번호>.json  그 파일의 정보: 설정·점검값·발화별 시각
rejected/                               버리거나 다시 녹음한 파일 (평가에 쓰지 않음, 이유는 .json에)
long/<코드>/                            긴 녹음 (1시간 단위 파일) + 온도 기록
labels.csv     발화마다 한 줄: 파일, 순번, 말, 계열, target(1=알림 대상), 신호 시각(cue_s), 실제 시작·끝(start_s, end_s), timing
               timing=detected 는 소리에서 찾은 실제 구간, cue 는 소리가 작아 못 찾아 화면 신호 시각으로 기록한 것
manifest.csv   통과한 파일마다 한 줄 (평가 스크립트는 이것과 labels.csv만 읽으면 됨)
log.csv        모든 녹음 시도 기록 (저장·버림·중단 포함)
long_log.csv   긴 녹음 기록
보정(C*)은 기준값 조정에만, 시험(P*, N*)은 마지막에 한 번만 평가에 써요.
학습용(R*)은 모델 학습에만 써요. 시험 파일(P*, N*)과 섞지 않아요 (set 열이 "학습"). 정답표 target=1 은 C, P, R.
"""

def migrate_v1():
    """v1이 만든 T_*.wav, X_*.wav를 새 구조로 옮긴다 (한 번만)"""
    old = [f for f in os.listdir(OUTD) if f.endswith(".wav") and (f.startswith("T_") or f.startswith("X_"))]
    if not old:
        return
    oklog = {}
    lp = path("log.csv")
    if os.path.exists(lp):
        try:
            with open(lp, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            if rows and "ok" in rows[0]:
                for r in rows:
                    oklog[r["file"]] = r
                os.rename(lp, path("log_v1.csv"))
        except Exception:
            pass
    oldlab = collections.defaultdict(list)
    if os.path.exists(path("labels.csv")):
        with open(path("labels.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if rows and "cue_s" not in rows[0]:
            for r in rows:
                oldlab[r["file"]].append(r)
            os.rename(path("labels.csv"), path("labels_v1.csv"))
    for f in sorted(old):
        r = oklog.get(f)
        code = next((c for c in PLAN if f.replace("X_", "", 1).startswith("T_%s_" % c)), None)
        if f.startswith("T_") and r and r.get("ok") == "1" and code:
            spk = r.get("speaker", "S1")
            take = next_take(code, spk)
            name = "%s_%s_%02d" % (code, spk, take)
            os.makedirs(path("audio", code), exist_ok=True)
            os.rename(path(f), path("audio", code, name + ".wav"))
            p = PLAN[code]
            phrases = []
            for lr in oldlab.get(f, []):
                phrases.append(dict(idx=int(lr["idx"]), text=lr["text"], family=lr["family"], cue_s=None,
                                    start_s=float(lr["start_s"]), end_s=float(lr["end_s"]), timing="v1_" + lr.get("timing", "")))
            meta = dict(file="audio/%s/%s.wav" % (code, name), code=code, speaker=spk, take=take, set=p["set"], dist=p["dist"],
                        desc=p["desc"], pga=r.get("pga"), hpf=r.get("hpf"), sec=float(r.get("sec") or 0), noise_db=r.get("noise_db"),
                        peak_db=r.get("peak_db"), clip_pct=r.get("clip_pct"), snr_db=r.get("snr_db"), phrases=phrases,
                        n_expected=len(p.get("seq", [])), n_detected=len(phrases), n_cue=0, kept_with_issues="",
                        time=r.get("time"), version="v1(옮김)")
            save_meta(meta)
            if phrases:
                append_labels(meta)
            log_attempt(meta["file"], code, spk, "옮김(v1 통과)", "")
        else:
            reason = "v1에서 다시 녹음 판정" if f.startswith("X_") else "v1 점검 기록 없음 (녹음 도중 중단된 것으로 보임)"
            os.rename(path(f), path("rejected", f))
            with open(path("rejected", f[:-4] + ".json"), "w", encoding="utf-8") as jf:
                json.dump(dict(file=f, reason=reason, version="v1"), jf, ensure_ascii=False, indent=1)
            log_attempt("rejected/" + f, code or "?", "S1", "옮김(버림)", reason)
    write_manifest()
    print("이전 파일을 새 폴더 구조로 옮겼어요. (status로 확인)")

# ---------------------------------------------------------------- 기록 파일
def accepted():
    """통과한 짧은 녹음 메타 목록"""
    out = []
    ad = path("audio")
    if not os.path.isdir(ad):
        return out
    for code in sorted(os.listdir(ad)):
        for f in sorted(os.listdir(os.path.join(ad, code))):
            if f.endswith(".json"):
                with open(os.path.join(ad, code, f), encoding="utf-8") as jf:
                    out.append(json.load(jf))
    return out

def count_ok():
    c = collections.Counter()
    for m in accepted():
        c[(m["code"], m["speaker"])] += 1
    return c

def next_take(code, spk):
    d = path("audio", code)
    k = 1
    while os.path.exists(os.path.join(d, "%s_%s_%02d.wav" % (code, spk, k))):
        k += 1
    return k

def save_meta(meta):
    with open(path(meta["file"][:-4] + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

LAB_COLS = ["file", "idx", "text", "family", "target", "cue_s", "start_s", "end_s", "timing", "speaker", "code", "set", "dist", "take"]
def append_labels(meta):
    new = not os.path.exists(path("labels.csv"))
    with open(path("labels.csv"), "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LAB_COLS)
        tgt = 1 if meta["code"][0] in "CPR" else 0
        for ph in meta["phrases"]:
            w.writerow([meta["file"], ph["idx"], ph["text"], ph["family"], tgt, ph["cue_s"], ph["start_s"], ph["end_s"], ph["timing"],
                        meta["speaker"], meta["code"], meta["set"], meta["dist"], meta["take"]])

def log_attempt(file, code, spk, result, detail):
    new = not os.path.exists(path("log.csv"))
    with open(path("log.csv"), "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "file", "code", "speaker", "result", "detail"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), file, code, spk, result, detail])

MAN_COLS = ["file", "code", "set", "dist", "speaker", "take", "sec", "n_expected", "n_detected", "n_cue", "noise_db", "peak_db", "clip_pct",
            "snr_db", "pga", "hpf", "kept_with_issues", "time", "version"]
def write_manifest():
    with open(path("manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(MAN_COLS)
        for m in accepted():
            w.writerow([m.get(k, "") for k in MAN_COLS])

# ---------------------------------------------------------------- 휴대폰 화면 (웹)
STATE = dict(color="gray", head="대기 중", big="", sub="", info="", cnt="", prog=0)
LOCK = threading.Lock()
def ui_set(**kw):
    with LOCK:
        STATE.update(kw)

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>녹음 신호</title><style>
html,body{margin:0;height:100%;font-family:system-ui,-apple-system,sans-serif;color:#fff;background:#3a3a3a;transition:background .12s}
#w{display:flex;flex-direction:column;height:100%;padding:5vw;box-sizing:border-box}
#info{font-size:4.2vw;opacity:.8}#head{font-size:9vw;font-weight:800;margin-top:2vw}
#big{flex:1;display:flex;align-items:center;justify-content:center;text-align:center;font-size:15vw;font-weight:800;line-height:1.2;word-break:keep-all}
#sub{font-size:6.5vw;text-align:center;min-height:9vw}#cnt{font-size:5vw;text-align:right;opacity:.85}
#bar{height:2.5vw;background:rgba(255,255,255,.25);margin-top:3vw;border-radius:2vw;overflow:hidden}#p{height:100%;background:#fff;width:0}
</style></head><body><div id="w"><div id="info"></div><div id="head"></div><div id="big"></div><div id="sub"></div><div id="cnt"></div><div id="bar"><div id="p"></div></div></div>
<script>
const C={green:"#12823a",yellow:"#c08a00",gray:"#3a3a3a",blue:"#1f4e9c",red:"#a01d1d"};
async function tick(){try{const r=await fetch("/s",{cache:"no-store"});const s=await r.json();
document.body.style.background=C[s.color]||"#3a3a3a";for(const k of ["info","head","big","sub","cnt"])document.getElementById(k).textContent=s[k]||"";
document.getElementById("p").style.width=Math.round((s.prog||0)*100)+"%";}catch(e){document.getElementById("head").textContent="연결 끊김 (다시 연결 중)";}
setTimeout(tick,150)}tick();
</script></body></html>"""

def start_web(enabled=True):
    if not enabled:
        return None
    try:
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/s"):
                    with LOCK:
                        body = json.dumps(STATE, ensure_ascii=False).encode()
                    ctype = "application/json; charset=utf-8"
                else:
                    body, ctype = PAGE.encode(), "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return "http://%s:%d" % (my_ip(), PORT)
    except OSError:
        return None

# ---------------------------------------------------------------- 터미널 화면
TC = dict(green="\033[42;30;1m", yellow="\033[43;30;1m", gray="\033[100;97;1m", blue="\033[44;97;1m", red="\033[41;97;1m")
R, B, DIM = "\033[0m", "\033[1m", "\033[2m"
TTY = sys.stdout.isatty()

def screen(lines):
    if not TTY:
        return
    sys.stdout.write("\033[H" + "".join(l + "\033[K\n" for l in lines) + "\033[J")
    sys.stdout.flush()

def cls():
    if TTY:
        sys.stdout.write("\033[2J\033[H\033[?25l"); sys.stdout.flush()

def show_cursor():
    if TTY:
        sys.stdout.write("\033[?25h"); sys.stdout.flush()

def width():
    return max(50, min(shutil.get_terminal_size((80, 24)).columns, 90))

def banner(color, text):
    W = width()
    return TC[color] + pad("  " + text, W) + R

def meter(db, n=30):
    k = int(round((min(0, max(-70, db)) + 70) / 70 * n))
    col = "\033[31m" if db > -3 else ("\033[33m" if db > -12 else "\033[32m")
    return col + "█" * k + R + DIM + "░" * (n - k) + R + " %5.1f dB" % db

def grid(phrases, cur, done_upto):
    out, row = [], []
    for i, p in enumerate(phrases):
        tag = "%2d.%s" % (i + 1, p)
        if i == cur:
            s = TC["green"] + tag + R
        elif i < done_upto:
            s = DIM + tag + R
        else:
            s = tag
        row.append(pad(tag, 22).replace(tag, s))
        if len(row) == 4:
            out.append("  " + "".join(row)); row = []
    if row:
        out.append("  " + "".join(row))
    return out

def ask(prompt, auto=""):
    show_cursor()
    if AUTO:
        print(prompt + " " + auto)
        return auto
    try:
        return input(prompt + " ").strip().lower()
    except EOFError:
        return "q"

AUTO = False

# ---------------------------------------------------------------- 소리 받기
class RecError(Exception):
    pass

class MicSource:
    def __init__(self):
        self.p = subprocess.Popen(["arecord", "-q", "-D", DEV, "-f", "S16_LE", "-r", str(SR), "-c", "2", "-t", "raw"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    def read(self, frames):
        need, buf = frames * 4, b""
        while len(buf) < need:
            b = self.p.stdout.read(need - len(buf))
            if not b:
                break
            buf += b
        if not buf:
            err = self.p.stderr.read().decode(errors="ignore")
            if "busy" in err.lower():
                raise RecError("마이크를 다른 프로그램이 쓰고 있어요. 음성 서비스를 끄세요:  sudo systemctl stop %s" % SERVICE)
            raise RecError("마이크에서 소리를 못 받았어요: " + (err.strip() or "원인 모름"))
        return buf

    def close(self):
        try:
            self.p.terminate(); self.p.wait(2)
        except Exception:
            self.p.kill()

class FakeSource:
    """시험용 가짜 소리: 신호(cue) 0.5초 뒤 1.3초 길이의 말"""
    def __init__(self, sec, cues, mode="1"):
        rng = np.random.default_rng(7)
        n = int((sec + 1) * SR)
        x = rng.standard_normal(n) * 10 ** (-70 / 20)
        for i, c in enumerate(cues):
            if mode.startswith("miss") and i + 1 == int(mode[4:]):
                continue
            a = int((c + 0.5) * SR)
            if mode == "split" and i == 3:     # 4번 말을 두 덩어리로 (중간 0.8초 쉼)
                for a2, L in ((a, int(0.6 * SR)), (a + int(1.4 * SR), int(0.7 * SR))):
                    x[a2:a2 + L] += rng.standard_normal(L) * np.hanning(L) * 10 ** (-28 / 20)
                continue
            L = int(1.3 * SR)
            x[a:a + L] += rng.standard_normal(L) * np.hanning(L) * 10 ** (-28 / 20)
        y = (np.clip(np.stack([x, x * 0.9], 1), -1, 1) * 32767).astype("<i2")
        self.b, self.pos = y.tobytes(), 0

    def read(self, frames):
        sleep(frames / SR)
        b = self.b[self.pos:self.pos + frames * 4]
        self.pos += frames * 4
        return b

    def close(self):
        pass

def open_source(sec, cues):
    if FAKE:
        return FakeSource(sec, cues, FAKE)
    return MicSource()

def level_db(b):
    x = np.frombuffer(b, dtype="<i2").reshape(-1, 2)[:, 0].astype(np.float32) / 32768.0
    return 20 * math.log10(float(np.sqrt((x ** 2).mean())) + 1e-9)

# ---------------------------------------------------------------- 분석
def load(p):
    with wave.open(p) as w:
        ch, raw = w.getnchannels(), w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, ch).astype(np.float32) / 32768.0

def analyze(x, min_len=0.3, floor_win=(1.0, 4.0)):
    """0번 채널: 잡음 바닥, 최대, 찌그러짐, 소리 구간(0.6초 이내 틈은 한 덩어리)"""
    c = x[:, 0]
    fr = 320
    n = len(c) // fr
    e = 10 * np.log10((c[:n * fr].reshape(n, fr) ** 2).mean(1) + 1e-12)
    a, b = int(floor_win[0] * 50), int(floor_win[1] * 50)
    floor = float(np.median(e[a:b])) if n > b else float(np.median(e))
    thr = max(floor + 12.0, -70.0)
    on = e > thr
    on[:50] = False
    segs, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and (on[j] or (j + 1 < n and on[j + 1:j + 31].any())):
                j += 1
            if j - i >= int(min_len * 50):
                segs.append((i * fr / SR, j * fr / SR, float(e[i:j].max())))
            i = j
        else:
            i += 1
    peak = 20 * math.log10(float(np.abs(c).max()) + 1e-9)
    clip = float((np.abs(c) >= 0.999).mean()) * 100
    sp = float(np.median([s[2] for s in segs])) if segs else floor
    return dict(floor=floor, peak=peak, clip=clip, segs=segs, snr=sp - floor, sec=len(c) / SR)

def match(r, phrases, cues, interval):
    """소리 구간을 화면 신호 칸에 배정. 한 칸 안의 여러 덩어리는 하나로 합친다"""
    out, extra = [], []
    wins = [(c - 1.0, c + interval - 1.0) for c in cues]
    slots = [[] for _ in cues]
    for s in r["segs"]:
        k = next((i for i, (lo, hi) in enumerate(wins) if lo <= s[0] < hi), None)
        (slots[k].append(s) if k is not None else extra.append(s))
    for i, (ph, c) in enumerate(zip(phrases, cues)):
        ss = slots[i]
        if ss:
            out.append(dict(idx=i + 1, text=ph, family=FAMILY[ph], cue_s=round(c, 2), start_s=round(min(s[0] for s in ss), 2),
                            end_s=round(max(s[1] for s in ss), 2), peak_db=round(max(s[2] for s in ss), 1), pieces=len(ss), timing="detected"))
        else:
            out.append(dict(idx=i + 1, text=ph, family=FAMILY[ph], cue_s=round(c, 2), start_s=round(c, 2),
                            end_s=round(c + SPEAK_SEC + 0.5, 2), peak_db=None, pieces=0, timing="missing"))
    return out, extra

# ---------------------------------------------------------------- 녹음 한 번
def phase(t, cues, end):
    if not cues:
        return "quiet", -1, end - t
    for i, c in enumerate(cues):
        if c - READY_SEC <= t < c:
            return "ready", i, c - t
        if c <= t < c + SPEAK_SEC:
            return "speak", i, c + SPEAK_SEC - t
    if t < cues[0]:
        return "lead", 0, cues[0] - t
    if t >= cues[-1] + SPEAK_SEC:
        return "tail", len(cues), end - t
    j = next(i for i, c in enumerate(cues) if c > t)
    return "rest", j, cues[j] - t

def capture(code, p, spk, take_label, tmp, sec, phrases, cues, practice=False):
    """녹음하면서 화면(터미널·휴대폰)에 신호를 보여 준다. 반환: 실제 녹음 길이(초)"""
    is_noise = code == "N7_noise"
    n = len(phrases)
    info = "%s · %s · %s · 화자 %s" % ("연습" if practice else code, p["desc"], take_label, spk)
    src = open_source(sec, cues)
    w = wave.open(tmp, "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    total, got, lvl, k = int(sec * SR), 0, -90.0, 0
    cls()
    try:
        while got < total:
            b = src.read(min(CHUNK, total - got))
            if not b:
                break
            w.writeframes(b)
            got += len(b) // 4
            lvl = level_db(b)
            t = got / SR
            k += 1
            st, i, rem = phase(t, cues, sec)
            sec_left = int(math.ceil(rem))
            if st == "quiet":
                col, head, big, sub = "gray", "● 녹음 중 — 조용히", "소리 내지 마세요", "끝까지 %d초" % sec_left
            elif st == "lead":
                col, head, big, sub = "gray", "● 녹음 중 — 조용히 기다려요", phrases[0], "첫 번째 말까지 %d초" % sec_left
            elif st == "ready":
                col, head, big, sub = "yellow", "준비 … %d" % sec_left, phrases[i], "곧 말할 차례예요"
            elif st == "speak":
                col, head, big = "green", "▶ 지금 %s" % ("이 소리를 내세요" if is_noise else "말하세요"), phrases[i]
                sub = "한 번만" if is_noise else "한 번만, 또박또박"
            elif st == "rest":
                col, head, big, sub = "gray", "조용히", phrases[i], "다음 차례까지 %d초" % sec_left
            else:
                col, head, big, sub = "gray", "조용히 — 거의 끝났어요", "쉿", "끝까지 %d초" % sec_left
            cur = i if st in ("ready", "speak") else -1
            done = i if st in ("rest", "ready", "speak") else (n if st == "tail" else 0)
            cnt = "%d / %d" % (min(i + 1, n), n) if n and st in ("ready", "speak") else ("%d / %d 끝" % (done, n) if n else "")
            ui_set(color=col, head=head, big=big, sub=sub, info=info, cnt=cnt, prog=got / total)
            if k % 2 == 0 or got >= total:
                lines = [B + info + R, "", banner(col, head), "", "      " + B + big + R, "      " + sub, ""]
                if n:
                    lines += grid(phrases, cur, done) + [""]
                lines += ["  마이크 " + meter(lvl), "  진행   " + mmss(t) + " / " + mmss(sec) + "   " + cnt, "",
                          DIM + "  Ctrl+C: 이 녹음 중단 (저장하지 않아요)" + R]
                screen(lines)
    finally:
        src.close()
        w.close()
    return got / SR

def run_one(code, spk, web, delay=None, practice=False):
    """한 파일 녹음 → 점검 → 저장/다시/버리기.  반환: saved / rejected / aborted / quit / skip"""
    p = PLAN[code]
    if practice:
        phrases, interval = CYCLE16[:4], 6.5
    elif p["kind"] == "silence":
        phrases, interval = [], 0
    else:
        phrases, interval = p["seq"], p["interval"]
    n = len(phrases)
    sec = p["sec"] if p["kind"] == "silence" and not practice else LEAD + n * interval + TAIL
    cues = [LEAD + i * interval for i in range(n)]
    have = count_ok()[(code, spk)]
    need = target_for(code, spk)
    take_label = "연습" if practice else "파일 %d/%d" % (have + 1, need)
    pga, hpf = mixer()

    # --- 준비 화면
    cls(); show_cursor()
    print(banner("blue", "%s  %s" % ("연습 (저장 안 함)" if practice else code, take_label)))
    print()
    print("  내용  : " + B + p["desc"] + R)
    print("  자리  : " + STATIONS.get(p["station"], ("", ""))[1])
    print("  방법  : " + p["how"])
    if n:
        print("  순서  : 화면에 초록색이 뜰 때 한 번 말해요. 노랑=준비, 회색=조용히. (약 %.1f초마다 하나, 모두 %d개)" % (interval, n))
        print()
        for l in grid(phrases, -1, 0):
            print(l)
    print()
    print("  길이  : %s (+ 이동 %d초)" % (mmss(sec), delay if delay is not None else p["delay"]))
    if web:
        print("  휴대폰: " + B + web + R + "  (같은 와이파이, 화면 꺼짐 시간을 길게)")
    if pga is not None and (pga != "24" or "0.0045" not in str(hpf)):
        print("\033[33m  주의: 이득 %s, 고역 필터 %s — 계획(24, 0.0045xFs)과 달라요.\033[0m" % (pga, hpf))
    if spk != "S1":
        print("\033[33m  다른 사람(%s) 녹음: 녹음 전에 동의를 받았는지 확인하세요.\033[0m" % spk)
    print()
    ui_set(color="blue", head="준비 화면", big=code if not practice else "연습", sub="파이에서 엔터를 누르면 시작해요", info=p["desc"], cnt=take_label, prog=0)
    a = ask("  [Enter] 시작   [s] 이번 건 건너뛰기   [q] 끝내기 >", "")
    if a == "q":
        return "quit"
    if a == "s":
        return "skip"

    # --- 이동 카운트다운
    d = delay if delay is not None else p["delay"]
    cls()
    t_end = time.time() + d / SPEED
    while True:
        left = int(math.ceil((t_end - time.time()) * SPEED))
        if left <= 0:
            break
        first = phrases[0] if phrases else "조용히"
        ui_set(color="blue", head="자리로 이동하세요", big=str(left), sub="첫 번째: " + first, info=p["desc"] + " · " + p["dist"], cnt=take_label, prog=0)
        screen([banner("blue", "자리로 이동하세요  (%s)" % p["dist"]), "", "      " + B + "%d초 뒤 녹음 시작" % left + R, "",
                "      첫 번째: " + first, "", DIM + "  Ctrl+C: 취소" + R])
        sleep(0.2)

    tmp = path(".tmp", "rec_%d.wav" % os.getpid())
    try:
        got = capture(code, p, spk, take_label, tmp, sec, phrases, cues, practice)
    except KeyboardInterrupt:
        show_cursor()
        if os.path.exists(tmp):
            os.remove(tmp)
        ui_set(color="red", head="중단했어요", big="", sub="파이 화면을 보세요", prog=0)
        print("\n\n  중단했어요. 이 녹음은 저장하지 않았어요.")
        if not practice:
            log_attempt("", code, spk, "중단", "Ctrl+C")
        return "aborted"
    except RecError as e:
        show_cursor()
        if os.path.exists(tmp):
            os.remove(tmp)
        ui_set(color="red", head="마이크 오류", big="", sub=str(e), prog=0)
        print("\n\n  \033[31m" + str(e) + "\033[0m")
        return "quit"
    show_cursor()
    if got < sec - 0.5:
        os.remove(tmp)
        print("\n  녹음이 %.0f초에서 끊겼어요 (예정 %.0f초). 다시 해 주세요." % (got, sec))
        return "aborted"

    # --- 점검
    x = load(tmp)
    if p["kind"] == "silence" and not practice:
        r = analyze(x, min_len=0.3, floor_win=(1.0, 59.0))
        res, extra = [], r["segs"]
    else:
        r = analyze(x, min_len=0.06 if code == "N7_noise" else 0.3)
        res, extra = match(r, phrases, cues, interval)
    miss = [q for q in res if q["timing"] == "missing"]
    lead_x = [s for s in extra if cues and s[0] < cues[0] - 1.0]
    tail_x = [s for s in extra if cues and s[0] >= cues[0] - 1.0]
    blocks, warns = [], []
    if p["kind"] == "silence" and not practice and extra:
        blocks.append("조용해야 하는데 소리가 %d번 잡혔어요 (%s초)" % (len(extra), ", ".join("%.0f" % s[0] for s in extra[:5])))
    if r["clip"] > 0.2 and code != "P6_loud_1m":
        blocks.append("소리가 너무 커서 찌그러졌어요 (%.2f%%). 마이크에서 조금 떨어지거나 목소리를 줄이세요" % r["clip"])
    if miss:
        blocks.append("%s번 말이 안 잡혔어요" % ", ".join(str(q["idx"]) for q in miss))
    if lead_x:
        warns.append("첫 말 전에 소리가 잡혔어요 (%s초)" % ", ".join("%.1f" % s[0] for s in lead_x[:3]))
    if tail_x:
        warns.append("신호 칸 밖에서 소리가 잡혔어요 (%s초)" % ", ".join("%.1f" % s[0] for s in tail_x[:3]))
    if res and p["dist"] in ("1m",) and r["snr"] < 15 and "weak" not in code:
        warns.append("1m인데 말소리가 잡음보다 %.0fdB만 커요 (마이크 방향·이득 확인)" % r["snr"])
    split = [q for q in res if q["pieces"] > 1]

    cls()
    ok = not blocks
    print(banner("green" if ok else ("yellow" if not [b for b in blocks if "찌그러" in b or "조용해야" in b] else "red"),
                 "결과: %s  %s" % ("연습" if practice else code, ("✓ 좋아요" if ok else "확인이 필요해요"))))
    print()
    if res:
        found = n - len(miss)
        print("  %d개 중 %d개 확인" % (n, found) + ("   (끊겨 잡힌 말 %d개는 하나로 합쳤어요)" % len(split) if split else ""))
        for q in res:
            if q["timing"] == "missing":
                print("  \033[31m%2d. %s — 안 잡힘\033[0m" % (q["idx"], pad(q["text"], 22)))
            else:
                print("  %2d. %s 신호 뒤 %4.1f초에 시작, %3.1f초, %5.1f dB  ✓" % (q["idx"], pad(q["text"], 22), q["start_s"] - q["cue_s"], q["end_s"] - q["start_s"], q["peak_db"]))
    print()
    print("  점검: 잡음 %.1f dBFS | 최대 %.1f dBFS | 찌그러짐 %.2f%% | 말소리-잡음 %.0f dB | 이득 %s, 필터 %s" % (r["floor"], r["peak"], r["clip"], r["snr"], pga, hpf))
    for b in blocks:
        print("  \033[31m✗ " + b + "\033[0m")
    for wv in warns:
        print("  \033[33m! " + wv + "\033[0m")
    print()
    if practice:
        os.remove(tmp)
        ui_set(color="green" if ok else "yellow", head="연습 끝", big="", sub="파이 화면을 보세요", prog=1)
        print("  연습이라 저장하지 않았어요. " + ("박자가 잘 맞았어요." if ok else "안 잡힌 말이 있으면 신호(초록)가 뜬 직후에 말해 보세요."))
        return "practice"

    only_miss = blocks and all("안 잡혔어요" in b for b in blocks)
    ui_set(color="green" if ok else "yellow", head="결과: " + ("좋아요" if ok else "확인 필요"), big="", sub="파이 화면에서 선택하세요", prog=1)
    if ok:
        a = ask("  [Enter] 저장   [r] 다시 녹음   [d] 버리기 >", "")
        choice = "save" if a in ("", "y") else ("redo" if a == "r" else "drop")
    elif only_miss:
        print("  - 말을 빼먹었거나 다른 말을 했다면  →  [r] 다시 녹음 (권장)")
        print("  - 분명히 말했는데 작아서 안 잡힌 거라면 →  [k] 보관 (그 말은 신호 시각으로 기록)")
        a = ask("  [r] 다시 녹음   [k] 보관   [d] 버리기 >", "r")
        choice = "keep" if a == "k" else ("drop" if a == "d" else "redo")
    else:
        a = ask("  [r] 다시 녹음   [d] 버리기 >", "r")
        choice = "drop" if a == "d" else "redo"

    stamp = time.strftime("%Y%m%d_%H%M%S")
    meta = dict(code=code, speaker=spk, set=p["set"], dist=p["dist"], desc=p["desc"], pga=pga, hpf=hpf, sec=round(r["sec"], 1),
                noise_db=round(r["floor"], 1), peak_db=round(r["peak"], 1), clip_pct=round(r["clip"], 3), snr_db=round(r["snr"], 1),
                n_expected=n, n_detected=n - len(miss), n_cue=0, interval=interval, lead=LEAD, temp_c=temp(),
                problems=blocks, warnings=warns, kept_with_issues="", time=time.strftime("%Y-%m-%d %H:%M:%S"), version=VERSION)
    if choice in ("save", "keep"):
        for q in res:
            if q["timing"] == "missing":
                q["timing"] = "cue"
        meta["n_cue"] = len(miss)
        if choice == "keep":
            meta["kept_with_issues"] = "; ".join(blocks)
        take = next_take(code, spk)
        name = "%s_%s_%02d" % (code, spk, take)
        os.makedirs(path("audio", code), exist_ok=True)
        meta.update(file="audio/%s/%s.wav" % (code, name), take=take, phrases=res)
        shutil.move(tmp, path(meta["file"]))
        save_meta(meta)
        if res:
            append_labels(meta)
        write_manifest()
        log_attempt(meta["file"], code, spk, "저장" if choice == "save" else "보관(확인필요)", "; ".join(blocks + warns))
        print("\n  \033[32m✓ 저장: %s  (정답 %d개 기록)\033[0m" % (meta["file"], len(res)))
        return "saved"
    name = "%s_%s_%s.wav" % (code, spk, stamp)
    shutil.move(tmp, path("rejected", name))
    meta.update(file="rejected/" + name, phrases=res, reason="다시 녹음" if choice == "redo" else "버림")
    with open(path("rejected", name[:-4] + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log_attempt(meta["file"], code, spk, meta["reason"], "; ".join(blocks + warns))
    print("\n  rejected/ 로 옮겼어요 (평가에 안 써요).")
    return "redo" if choice == "redo" else "rejected"

# ---------------------------------------------------------------- 계획 · 진행
def target_for(code, spk):
    if spk == "S1":
        return PLAN[code]["files"]
    return TARGET_OTHER_SPK.get(code, 0)

def short_codes(station=None):
    return [c for c, p in PLAN.items() if p["kind"] != "long" and (station is None or p["station"] == station)]

def next_needed(spk, station, skipped, prefix=None):
    cnt = count_ok()
    for st in STATIONS:
        if station and st != station:
            continue
        for c in short_codes(st):
            if prefix and not c.startswith(prefix):
                continue
            if c not in skipped and cnt[(c, spk)] < target_for(c, spk):
                return c
    return None

def long_running():
    pf = path("long", ".running")
    if os.path.exists(pf):
        try:
            pid, code = open(pf).read().split()[:2]
            os.kill(int(pid), 0)
            return int(pid), code
        except Exception:
            os.remove(pf)
    return None

def preflight(verbose=True):
    """반환: (막는 문제, 알림)"""
    stop, note = [], []
    lr = long_running()
    if lr:
        stop.append("긴 녹음(%s)이 진행 중이라 마이크를 쓸 수 없어요. 끝나거나 'stop' 후에 하세요." % lr[1])
    if not FAKE:
        if "seeed" not in run(["arecord", "-l"]):
            stop.append("reSpeaker 마이크가 안 보여요 (arecord -l). 연결·드라이버를 확인하세요.")
        if run(["systemctl", "is-active", SERVICE]).strip() == "active":
            stop.append("음성 서비스가 켜져 있어요. 끄세요:  sudo systemctl stop %s" % SERVICE)
    pga, hpf = mixer()
    if pga is None:
        note.append("이득·필터 설정을 못 읽었어요")
    elif pga != "24" or "0.0045" not in str(hpf):
        note.append("이득 %s, 필터 %s — 계획은 24, 0.0045xFs" % (pga, hpf))
    free = shutil.disk_usage(OUTD).free / 1e9
    if free < 1.0:
        stop.append("디스크 여유가 %.1fGB뿐이에요" % free)
    t = temp()
    if t and t > 75:
        note.append("온도가 %.0f℃로 높아요" % t)
    if verbose:
        print(B + "시작 전 점검" + R)
        print("  마이크 %s | 음성 서비스 %s | 이득 %s, 필터 %s | 디스크 여유 %.1fGB | 온도 %s" % (
            "가짜(시험)" if FAKE else "있음", "꺼짐" if not [s for s in stop if "음성 서비스" in s] else "켜짐", pga, hpf, free, "%.0f℃" % t if t else "-"))
        for s in stop:
            print("  \033[31m✗ " + s + "\033[0m")
        for s in note:
            print("  \033[33m! " + s + "\033[0m")
        if not stop:
            print("  \033[32m✓ 녹음할 수 있어요\033[0m")
    return stop, note

def cmd_go(args):
    stop, _ = preflight()
    if stop:
        return
    web = start_web(not args.no_web)
    st = args.code if args.code in STATIONS else None
    prefix = "R" if args.code in ("R", "train") else None
    if args.code and not st and not prefix:
        print("자리 이름은 %s 중 하나이거나, R(학습용만)이에요." % " / ".join(STATIONS)); return
    skipped, last_station, made = set(), None, 0
    try:
        while True:
            code = next_needed(args.spk, st, skipped, prefix)
            if not code:
                break
            sta = PLAN[code]["station"]
            if sta != last_station:
                cls(); show_cursor()
                name, how = STATIONS[sta]
                left = [c for c in short_codes(sta) if (not prefix or c.startswith(prefix)) and count_ok()[(c, args.spk)] < target_for(c, args.spk)]
                nfile = sum(target_for(c, args.spk) - count_ok()[(c, args.spk)] for c in left)
                print(banner("blue", "자리: %s" % name))
                print("\n  " + how)
                print("  이 자리에서 남은 녹음: %d개 파일 (%s)" % (nfile, ", ".join(left)))
                if web:
                    print("  휴대폰 화면: " + B + web + R)
                print("  마이크 위치·방향은 바꾸지 마세요.\n")
                ui_set(color="blue", head="자리: " + name, big="", sub=how, info="", cnt="", prog=0)
                a = ask("  준비되면 [Enter]   [s] 이 자리 건너뛰기   [q] 끝내기 >", "")
                if a == "q":
                    break
                if a == "s":
                    skipped.update(short_codes(sta)); last_station = sta; continue
                last_station = sta
            res = run_one(code, args.spk, web, args.delay)
            if res == "quit":
                break
            if res == "skip":
                skipped.add(code)
            if res == "saved":
                made += 1
            if res in ("aborted", "redo", "rejected", "saved"):
                if ask("\n  [Enter] 계속   [q] 끝내기 >", "") == "q":
                    break
    except KeyboardInterrupt:
        pass
    show_cursor()
    ui_set(color="gray", head="세션 끝", big="", sub="", prog=0)
    print("\n이번에 저장한 파일 %d개.\n" % made)
    cmd_status()

def cmd_single(code, args):
    if code not in PLAN:
        print("모르는 코드예요: %s  (plan으로 목록 보기)" % code); return
    if PLAN[code]["kind"] == "long":
        print("긴 녹음은  python3 rec_session.py long %s  로 시작해요." % code); return
    stop, _ = preflight()
    if stop:
        return
    web = start_web(not args.no_web)
    while True:
        r = run_one(code, args.spk, web, args.delay)
        if r != "redo" or ask("\n  [Enter] 바로 다시 녹음   [q] 끝내기 >", "q") == "q":
            break
    show_cursor()

def cmd_practice(args):
    stop, _ = preflight()
    if stop:
        return
    web = start_web(not args.no_web)
    run_one("P1_1m", args.spk, web, 5 if args.delay is None else args.delay, practice=True)
    show_cursor()

def cmd_check(args):
    stop, _ = preflight()
    if stop:
        return
    print("\n10초 동안 소리 크기를 봐요. 처음 4초는 조용히, 그 뒤에 1m 자리에서 '도와주세요'를 두세 번 말해 보세요.")
    ask("[Enter] 시작 >", "")
    src = open_source(11, [5.0, 7.5])
    lv, t0 = [], 0.0
    cls()
    try:
        while t0 < 10:
            b = src.read(CHUNK)
            if not b:
                break
            t0 += len(b) / 4 / SR
            lv.append((t0, level_db(b)))
            screen([banner("gray" if t0 < 4 else "green", "조용히" if t0 < 4 else "▶ '도와주세요' 말해 보기"), "", "  마이크 " + meter(lv[-1][1]),
                    "  %.0f / 10초" % t0])
    except RecError as e:
        show_cursor(); print("\n\033[31m" + str(e) + "\033[0m"); return
    finally:
        src.close()
    show_cursor()
    quiet = [v for t, v in lv if 1 < t < 4]
    loud = [v for t, v in lv if t >= 4]
    fl, pk = (np.median(quiet) if quiet else -90), (max(loud) if loud else -90)
    print("\n  조용할 때 %.1f dB, 말할 때 최대 %.1f dB, 차이 %.0f dB" % (fl, pk, pk - fl))
    if pk - fl < 15:
        print("  \033[33m! 말소리가 잘 안 잡혀요. 마이크 방향, 이득(24), 음성 서비스 꺼짐을 확인하세요.\033[0m")
    elif pk > -3:
        print("  \033[33m! 1m에서 너무 커요. 찌그러질 수 있어요.\033[0m")
    else:
        print("  \033[32m✓ 소리 크기 괜찮아요.\033[0m")

def cmd_status(args=None):
    cnt = count_ok()
    acc = accepted()
    spks = sorted(set(k[1] for k in cnt) | {"S1"})
    print(B + "진행 현황" + R + "  (%s)" % OUTD)
    for sta, (name, _) in STATIONS.items():
        codes = short_codes(sta)
        need = sum(PLAN[c]["files"] for c in codes)
        have = sum(min(cnt[(c, "S1")], PLAN[c]["files"]) for c in codes)
        mark = "\033[32m완료\033[0m" if have >= need else "%d/%d" % (have, need)
        print("  %s %s" % (pad(name, 22), mark))
        for c in codes:
            h, nd = cnt[(c, "S1")], PLAN[c]["files"]
            bar = "■" * min(h, nd) + "□" * max(0, nd - h)
            print("      %s %s %d/%d" % (pad(c, 16), bar, h, nd))
    others = [s for s in spks if s != "S1"]
    if others:
        print("  다른 사람: " + ", ".join("%s(%s)" % (s, ", ".join("%s %d/%d" % (c, cnt[(c, s)], n) for c, n in TARGET_OTHER_SPK.items())) for s in others))
    print("  긴 녹음:")
    for c in ("N1_day", "N2_night", "N3_tv_alarm"):
        d = path("long", c)
        fs = sorted(f for f in os.listdir(d) if f.endswith(".wav")) if os.path.isdir(d) else []
        hrs = sum(os.path.getsize(os.path.join(d, f)) for f in fs) / (SR * 4 * 3600)
        print("      %s %.1f / %.1f시간" % (pad(c, 16), hrs, PLAN[c]["hours"]))
    lr = long_running()
    if lr:
        print("  \033[33m진행 중: %s (pid %d)\033[0m" % (lr[1], lr[0]))
    rej = len([f for f in os.listdir(path("rejected")) if f.endswith(".wav")]) if os.path.isdir(path("rejected")) else 0
    fam = collections.Counter(q["family"] for m in acc if m["code"][0] == "P" for q in m.get("phrases", []))
    kept = sum(1 for m in acc if m.get("kept_with_issues"))
    print("  시험 양성 발화 계열별: %s | 확인필요로 보관한 파일 %d | 버린 파일 %d" % (dict(sorted(fam.items())) or "-", kept, rej))
    rn = sum(PLAN[c]["files"] for c in PLAN if c.startswith("R"))
    rh = sum(min(cnt[(c, "S1")], PLAN[c]["files"]) for c in PLAN if c.startswith("R"))
    print("  학습용(R) 녹음: %d/%d 파일" % (rh, rn))
    nx = next_needed("S1", None, set())
    print("  다음 녹음: %s" % (nx + " (" + STATIONS[PLAN[nx]["station"]][0] + ")" if nx else "짧은 녹음 모두 끝"))

def cmd_plan(args=None):
    tot_u, tot_m = 0, 0.0
    for sta, (name, _) in STATIONS.items():
        print(B + "[%s] %s" % (sta, name) + R)
        for c in short_codes(sta):
            p = PLAN[c]
            if p["kind"] == "silence":
                m, nu = p["sec"] / 60, 0
            else:
                nu = len(p["seq"]); m = (LEAD + nu * p["interval"] + TAIL) / 60
            tot_u += nu * p["files"]; tot_m += m * p["files"]
            print("  %s %d개  %s  (%s)" % (pad(c, 16), p["files"], p["desc"], ("발화 %d개 · %.1f분" % (nu, m)) if nu else "%.0f초" % p["sec"]))
    print(B + "[long] 긴 녹음" + R)
    for c in ("N1_day", "N2_night", "N3_tv_alarm"):
        print("  %s %s (%.1f시간)" % (pad(c, 16), PLAN[c]["desc"], PLAN[c]["hours"]))
    print("\n짧은 녹음 합계: 발화 %d개, 녹음 약 %.0f분. 긴 녹음 합계 11시간 20분." % (tot_u, tot_m))

# ---------------------------------------------------------------- 긴 녹음
def cmd_long(args):
    code = args.code
    if code not in PLAN or PLAN[code]["kind"] != "long":
        print("긴 녹음 코드: N1_day / N2_night / N3_tv_alarm"); return
    if not args.fg:
        stop, note = preflight()
        if stop:
            return
        hours = args.hours or PLAN[code]["hours"]
        need = hours * 0.23 + 1.0
        free = shutil.disk_usage(OUTD).free / 1e9
        if free < need:
            print("디스크 여유 %.1fGB, 필요 약 %.1fGB" % (free, need)); return
        logf = path("long", "%s_%s.log" % (code, time.strftime("%Y%m%d_%H%M")))
        cmd = [sys.executable, os.path.abspath(__file__), "long", code, "--fg", "--hours", str(hours)]
        with open(logf, "w") as lf:
            pr = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        time.sleep(2)
        if pr.poll() is not None:
            print("시작하지 못했어요. 기록:"); print(open(logf).read()); return
        print("\033[32m✓ %s 녹음을 백그라운드로 시작했어요 (%.1f시간). SSH를 닫아도 계속돼요.\033[0m" % (code, hours))
        print("  진행 확인:  python3 ~/rec_session.py status")
        print("  기록 보기:  tail %s" % logf)
        print("  멈추기:     python3 ~/rec_session.py stop")
        print("  " + PLAN[code]["desc"])
        return
    # ---- 실제 녹음 (백그라운드 프로세스)
    hours = args.hours or PLAN[code]["hours"]
    d = path("long", code)
    os.makedirs(d, exist_ok=True)
    with open(path("long", ".running"), "w") as f:
        f.write("%d %s\n" % (os.getpid(), code))
    stop_ev = threading.Event()
    cur = {"p": None}

    def on_sig(*_):
        stop_ev.set()
        if cur["p"]:
            cur["p"].send_signal(signal.SIGINT)
    signal.signal(signal.SIGTERM, on_sig)
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    pga, hpf = mixer()
    tlog = os.path.join(d, "temp_%s.csv" % time.strftime("%Y%m%d_%H%M"))

    def logger():
        with open(tlog, "a", encoding="utf-8") as f:
            while not stop_ev.is_set():
                f.write("%s,%s,%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), temp(), run(["vcgencmd", "get_throttled"]).strip()))
                f.flush()
                stop_ev.wait(60)
    threading.Thread(target=logger, daemon=True).start()
    print("%s 시작 %s %.2f시간 이득 %s 필터 %s" % (time.strftime("%H:%M:%S"), code, hours, pga, hpf), flush=True)
    total, k, t0, files = hours * 3600.0, 0, time.time(), []
    while total > 1 and not stop_ev.is_set():
        seg = min(3600.0, total)
        k += 1
        fp = os.path.join(d, "%s_%s_%02d.wav" % (code, time.strftime("%Y%m%d_%H%M%S"), k))
        print("%s 파일 %d (%.0f분): %s" % (time.strftime("%H:%M:%S"), k, seg / 60, os.path.basename(fp)), flush=True)
        if FAKE:
            n = int(min(seg, 30) * SR)
            x = (np.random.default_rng(k).standard_normal((n, 2)) * 300).astype("<i2")
            with wave.open(fp, "wb") as w:
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(x.tobytes())
            stop_ev.wait(seg / SPEED)
        else:
            cur["p"] = subprocess.Popen(["arecord", "-q", "-D", DEV, "-f", "S16_LE", "-r", str(SR), "-c", "2", "-d", str(int(seg)), fp])
            rc = cur["p"].wait()
            if rc != 0 and not stop_ev.is_set():
                print("arecord 오류 %s — 30초 뒤 다시 시도" % rc, flush=True)
                stop_ev.wait(30)
        files.append(fp)
        total -= seg
    stop_ev.set()
    temps = []
    for l in open(tlog, encoding="utf-8"):
        q = l.strip().split(",")
        try:
            temps.append(float(q[1]))
        except Exception:
            pass
    dur = (time.time() - t0) / 3600
    lg = path("long_log.csv")
    new = not os.path.exists(lg)
    with open(lg, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "code", "files", "hours", "planned_hours", "stopped_early", "pga", "hpf", "max_temp", "temp_log"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), code, "|".join(os.path.basename(x) for x in files), round(dur, 2), hours,
                    int(total > 1), pga, hpf, max(temps) if temps else "", os.path.basename(tlog)])
    print("%s 끝: 파일 %d개, %.2f시간, 최고 온도 %s" % (time.strftime("%H:%M:%S"), len(files), dur, max(temps) if temps else "-"), flush=True)
    try:
        os.remove(path("long", ".running"))
    except OSError:
        pass

def cmd_stop(args):
    lr = long_running()
    if not lr:
        print("진행 중인 긴 녹음이 없어요."); return
    os.kill(lr[0], signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not long_running():
            break
    print("%s 녹음을 멈췄어요. 그때까지 녹음된 부분은 저장돼요." % lr[1])

def cmd_pack(args):
    name = os.path.join(HOME, "own_test_%s%s.tar.gz" % (time.strftime("%Y%m%d_%H%M"), "_long" if args.long else ""))
    with tarfile.open(name, "w:gz") as tf:
        for item in ("audio", "labels.csv", "manifest.csv", "log.csv", "long_log.csv", "README.txt", "labels_v1.csv", "log_v1.csv"):
            if os.path.exists(path(item)):
                tf.add(path(item), arcname="own_test/" + item)
        if os.path.isdir(path("long")):
            for root, _, fs in os.walk(path("long")):
                for f in fs:
                    if f.startswith(".") or (f.endswith(".wav") and not args.long):
                        continue
                    full = os.path.join(root, f)
                    tf.add(full, arcname="own_test/" + os.path.relpath(full, OUTD))
    mb = os.path.getsize(name) / 1e6
    print("✓ %s (%.0fMB)" % (name, mb))
    print("PC(PowerShell)에서 받기:")
    print("  scp %s@%s:%s ." % (os.environ.get("USER", "user01"), my_ip(), name))
    print("  tar -xzf %s" % os.path.basename(name))

# ---------------------------------------------------------------- 시작
if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("cmd", nargs="?", default="help")
    ap.add_argument("code", nargs="?")
    ap.add_argument("--spk", default="S1")
    ap.add_argument("--delay", type=int)
    ap.add_argument("--hours", type=float)
    ap.add_argument("--fg", action="store_true")
    ap.add_argument("--long", action="store_true")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--yes", action="store_true")     # 시험용: 모든 질문에 기본값
    a = ap.parse_args()
    AUTO = a.yes
    os.makedirs(OUTD, exist_ok=True)
    ensure_dirs()
    try:
        if a.cmd == "go":
            cmd_go(a)
        elif a.cmd == "check":
            cmd_check(a)
        elif a.cmd == "practice":
            cmd_practice(a)
        elif a.cmd == "status":
            cmd_status(a)
        elif a.cmd == "plan":
            cmd_plan(a)
        elif a.cmd == "long":
            cmd_long(a)
        elif a.cmd == "stop":
            cmd_stop(a)
        elif a.cmd == "pack":
            cmd_pack(a)
        elif a.cmd in ("help", "-h", "--help"):
            print(__doc__)
        else:
            cmd_single(a.cmd, a)
    finally:
        show_cursor()
