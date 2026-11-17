#!/usr/bin/env python3
"""음성 위급 감지 서비스 (Pi 4 + reSpeaker, ONNX 모델)

  python3 kws_service.py                     마이크로 실행 (서비스 모드)
  python3 kws_service.py --file X.wav        파일로 시험 (빠르게 처리, 감지마다 클립 저장)
  python3 kws_service.py --file X.wav --no-mqtt   MQTT 보내지 않고 JSON만 출력
  python3 kws_service.py --replay samples/   마이크 없이 시험: 엔터를 누를 때마다 녹음해 둔 위급 말을
                                             실시간으로 흘려 보냄 (모델·판단·클립·MQTT는 실제와 똑같이 동작)

감지 → 클립 저장(말 시작 10초 전 ~ 말 끝 1초 후) → JSON 4필드를 MQTT QoS 1로 전송.
클립은 http://<파이IP>:8080/clips/<incident_id>.wav 로 제공.
설정: 같은 폴더의 kws_service.json (없으면 기본값으로 만듦)
"""
import os, sys, io, csv, json, time, wave, queue, signal, argparse, threading, subprocess, collections, datetime
from functools import partial
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = {
    "model_dir": os.path.join(HERE, "model"), "model": "r3_tcresnet.onnx", "config": "r3_tcresnet_config.json",
    "thr": 0.95,                        # 모델 설정(0.90)을 덮어씀: 오경보 0 기준
    "gate_rel_db": 15.0, "gate_abs_dbfs": -65.0,   # 학습·평가와 같은 게이트 (예전 서비스 -43dBFS는 쓰지 않음)
    "device": "plughw:CARD=seeed2micvoicec,DEV=0",
    "http_port": 8080, "public_host": "",          # 비우면 파이 IP 자동
    "pre_s": 10.0, "post_s": 1.0, "max_clips": 300,
    "mqtt": {"host": "", "port": 1883, "topic": "", "username": "", "password": "", "tls": False, "client_id": "voice-pi"},
    "data_dir": os.path.join(HERE, "data"),
}
SR, CH = 16000, 2
HOP = 4000                       # 0.25초마다 판단
FR = 320                         # 20ms 크기 계산 단위
KST = datetime.timezone(datetime.timedelta(hours=9))


def load_cfg(path):
    if not os.path.exists(path):
        json.dump(DEFAULT, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("설정 파일을 만들었어요:", path)
    c = json.load(open(path, encoding="utf-8"))
    out = json.loads(json.dumps(DEFAULT))
    for k, v in c.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    for k in ("model_dir", "data_dir"):                  # 상대 경로는 이 파일이 있는 폴더 기준
        out[k] = os.path.join(HERE, os.path.expanduser(out[k]))
    return out


def my_ip():
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split()[0]
    except Exception:
        return "127.0.0.1"


def cpu_temp():
    try:
        t = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3).stdout
        thr = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout.strip().split("=")[-1]
        return float(t.split("=")[1].split("'")[0]), thr
    except Exception:
        return None, ""


# ------------------------------------------------------------------ 모델 (학습 노트북의 np_features와 같은 계산)
class Engine:
    def __init__(self, cfg):
        import onnxruntime as ort
        md = cfg["model_dir"]
        mc = json.load(open(os.path.join(md, cfg["config"]), encoding="utf-8"))
        self.win, self.n_fft, self.hop = mc["win"], mc["n_fft"], mc["hop"]
        self.alert = mc["alert"]
        self.fb = np.load(os.path.join(md, "mel_fb.npy")).astype(np.float32)
        self.hann = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(self.n_fft) / self.n_fft)).astype(np.float32)
        nfr = 1 + (self.win - self.n_fft) // self.hop
        self.idx = np.arange(self.n_fft)[None, :] + self.hop * np.arange(nfr)[:, None]
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2              # 쿨러 없음: 코어 2개만 사용
        self.sess = ort.InferenceSession(os.path.join(md, cfg["model"]), so, providers=["CPUExecutionProvider"])
        self.model_thr = mc.get("thr")
        self.ms = collections.deque(maxlen=240)

    def score(self, x):
        t0 = time.perf_counter()
        s = np.fft.rfft(x[self.idx] * self.hann, axis=1)
        L = np.log((s.real ** 2 + s.imag ** 2) @ self.fb.T + 1e-10).T
        L = L - L.mean(axis=1, keepdims=True)
        L = (L / (np.sqrt((L ** 2).mean()) + 1e-5)).astype(np.float32)
        lg = self.sess.run(None, {"feat": L[None, None]})[0][0]
        p = np.exp(lg - lg.max())
        p /= p.sum()
        self.ms.append((time.perf_counter() - t0) * 1000)
        return float(p[self.alert].sum())


# ------------------------------------------------------------------ 클립 서버
def start_http(root, port):
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def list_directory(self, path):
            self.send_error(403)
            return None
    srv = ThreadingHTTPServer(("0.0.0.0", port), partial(H, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ MQTT (QoS 1, 실패하면 pending에 쌓고 다시 보냄)
class Sender:
    def __init__(self, cfg, data_dir, enabled):
        self.m, self.enabled = cfg["mqtt"], enabled and bool(cfg["mqtt"]["host"]) and bool(cfg["mqtt"]["topic"])
        self.pending = os.path.join(data_dir, "pending.jsonl")
        self.q, self.cli = queue.Queue(), None
        if enabled and not self.enabled:
            print("MQTT 설정(host, topic)이 비어 있어요 → 보내지 않고 JSON만 기록해요")
        if self.enabled:
            try:
                import paho.mqtt.client as mqtt
            except ImportError:
                sys.exit("paho-mqtt가 없어요:  pip install paho-mqtt")
            try:
                self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.m["client_id"])
            except AttributeError:
                self.cli = mqtt.Client(client_id=self.m["client_id"])
            if self.m["username"]:
                self.cli.username_pw_set(self.m["username"], self.m["password"])
            if self.m["tls"]:
                self.cli.tls_set()
            self.cli.reconnect_delay_set(1, 30)
            self.cli.connect_async(self.m["host"], int(self.m["port"]), keepalive=30)
            self.cli.loop_start()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, msg):
        self.q.put(msg)

    def _publish(self, msg):
        if not self.enabled:
            return "보내지 않음"
        if not self.cli.is_connected():
            return "연결 안 됨"
        info = self.cli.publish(self.m["topic"], json.dumps(msg, ensure_ascii=False), qos=1)
        try:
            info.wait_for_publish(timeout=10)
        except Exception as e:
            return "실패 %s" % e
        return "전송" if info.is_published() else "확인 없음"

    def _run(self):
        last_retry = 0.0
        while True:
            try:
                msg = self.q.get(timeout=5)
            except queue.Empty:
                msg = None
            if msg is not None:
                r = self._publish(msg)
                msg["_result"] = r
                if self.enabled and r != "전송":
                    with open(self.pending, "a", encoding="utf-8") as f:
                        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                print("  MQTT: %s" % r, flush=True)
            if self.enabled and time.time() - last_retry > 30 and os.path.exists(self.pending) and self.cli.is_connected():
                last_retry = time.time()
                rows = [json.loads(l) for l in open(self.pending, encoding="utf-8") if l.strip()]
                left = [m for m in rows if self._publish({k: v for k, v in m.items() if not k.startswith("_")}) != "전송"]
                with open(self.pending, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in left)
                if len(left) < len(rows):
                    print("  MQTT: 밀린 %d건 다시 보냄" % (len(rows) - len(left)), flush=True)


# ------------------------------------------------------------------ 감지 본체
class Detector:
    def __init__(self, cfg, engine, sender, clip_dir, data_dir, t0_wall, file_mode):
        self.cfg, self.eng, self.snd = cfg, engine, sender
        self.clip_dir, self.data_dir, self.t0, self.file_mode = clip_dir, data_dir, t0_wall, file_mode
        self.keep = int(60 * SR)                         # 최근 60초 보관
        self.buf, self.base = np.zeros(0, np.float32), 0     # base = buf[0]의 절대 샘플 번호
        self.db, self.dbbase, self.rem = np.zeros(0, np.float32), 0, np.zeros(0, np.float32)
        self.n = 0                                       # 지금까지 받은 샘플 수
        self.scores = collections.deque(maxlen=3)
        self.run, self.last_fire, self.ev = 0, -1e9, None
        self.host = cfg["public_host"] or my_ip()
        self.stat = collections.Counter()
        self.events = []

    def floor_thr(self, rel):
        tail = self.db[-int(60 * SR / FR):]
        floor = float(np.percentile(tail, 20)) if len(tail) >= 100 else -90.0
        return max(floor + rel, self.cfg["gate_abs_dbfs"]) if rel == self.cfg["gate_rel_db"] else floor + rel

    def push(self, x):
        self.buf = np.concatenate([self.buf, x])
        self.n += len(x)
        r = np.concatenate([self.rem, x])               # 0.25초(4000)가 20ms(320)로 나눠떨어지지 않아 남는 부분을 다음으로 넘김
        nf = len(r) // FR
        e = 10 * np.log10((r[:nf * FR].reshape(nf, FR) ** 2).mean(1) + 1e-12).astype(np.float32)
        self.rem = r[nf * FR:]
        self.db = np.concatenate([self.db, e])
        if len(self.buf) > self.keep + 20 * SR:
            cut = len(self.buf) - self.keep
            self.buf, self.base = self.buf[cut:], self.base + cut
            cf = len(self.db) - self.keep // FR
            self.db, self.dbbase = self.db[cf:], self.dbbase + cf
        if self.n < self.eng.win:
            return
        self.step()

    def step(self):
        t = self.n / SR
        gthr = self.floor_thr(self.cfg["gate_rel_db"])
        gate = bool((self.db[-int(1.5 * SR / FR):] >= gthr).any())
        self.stat["steps"] += 1
        if gate:
            self.stat["gate"] += 1
            s = self.eng.score(self.buf[-self.eng.win:])
        else:
            s = 0.0
        self.scores.append(s)
        sm = sum(self.scores) / 3.0
        if self.ev is not None:
            self.ev["conf"] = max(self.ev["conf"], sm)
        self.run = self.run + 1 if sm >= self.cfg["thr"] else 0
        if self.run >= 2 and t - self.last_fire >= 3.0 and self.ev is None:
            self.last_fire = t
            self.ev = {"t_fire": t, "conf": sm, "start": self.speech_start(t)}
        if self.ev is not None:
            self.try_finish(t)

    def speech_start(self, t):
        """감지 시점 앞의 말 덩어리 시작(0.6초 이내 틈은 이어 봄, 최대 6초 전까지)"""
        thr = self.floor_thr(12.0)
        i = int(t * SR / FR) - self.dbbase - 1
        lo = max(0, i - int(6 * SR / FR))
        loud = np.where(self.db[lo:i + 1] >= thr)[0]
        if len(loud) == 0:
            return t - 2.0
        k = len(loud) - 1
        while k > 0 and loud[k] - loud[k - 1] <= 30:
            k -= 1
        return (lo + loud[k] + self.dbbase) * FR / SR

    def try_finish(self, t, force=False):
        """말이 끝나고(0.6초 조용) 1초가 더 지나면 클립 저장 → 전송"""
        ev = self.ev
        thr = self.floor_thr(12.0)
        i0 = int(ev["t_fire"] * SR / FR) - self.dbbase - int(1.0 * SR / FR)
        seg = self.db[max(i0, 0):]
        loud = np.where(seg >= thr)[0]
        end = (max(i0, 0) + loud[-1] + 1 + self.dbbase) * FR / SR if len(loud) else ev["t_fire"]
        quiet_for = t - end
        if not force and quiet_for < 0.6 + self.cfg["post_s"] and t - ev["t_fire"] < 8.0:
            return
        end = min(end, ev["t_fire"] + 8.0)
        a = max(int((ev["start"] - self.cfg["pre_s"]) * SR), self.base)
        b = min(int((end + self.cfg["post_s"]) * SR), self.n)
        self.emit(ev, self.buf[a - self.base:b - self.base], a / SR, b / SR, end)
        self.ev = None

    def emit(self, ev, clip, a_s, b_s, end):
        # occurred_at = 실제로 소리가 난 순간(말 시작). 판정을 끝낸 시각(t_fire)이 아님 — 앱이 영상과 10초 안에 묶는 기준(11/17 앱 팀 요청)
        occ = datetime.datetime.fromtimestamp(self.t0 + ev["start"], KST).replace(microsecond=0)
        iid = "A_" + occ.strftime("%y%m%d%H%M%S")
        k = 1
        while os.path.exists(os.path.join(self.clip_dir, iid + ".wav")):
            k += 1
            iid = "A_" + occ.strftime("%y%m%d%H%M%S") + "_%d" % k     # 같은 초에 두 번(파일 시험 때만 생김)
        p = os.path.join(self.clip_dir, iid + ".wav")
        with wave.open(p + ".tmp", "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(clip, -1, 1) * 32767).astype("<i2").tobytes())
        os.replace(p + ".tmp", p)                         # 저장을 끝낸 뒤에 보냄
        msg = {"incident_id": iid, "confidence": round(float(ev["conf"]), 2), "occurred_at": occ.isoformat(),
               "clip_url": "http://%s:%d/clips/%s.wav" % (self.host, self.cfg["http_port"], iid)}
        delay = self.n / SR - ev["t_fire"]
        print("[감지] %s  말 %.1f~%.1f초  클립 %.1f초  점수 %.2f  (감지 후 %.1f초 뒤 전송)" % (
            iid, ev["start"], end, b_s - a_s, ev["conf"], delay), flush=True)
        print("  " + json.dumps(msg, ensure_ascii=False), flush=True)
        self.snd.send(dict(msg))
        self.events.append((ev["t_fire"], ev["start"], end))
        lp = os.path.join(self.data_dir, "events.csv")
        new = not os.path.exists(lp)
        with open(lp, "a", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            if new:
                wr.writerow(["incident_id", "occurred_at", "confidence", "speech_start_s", "speech_end_s", "clip_s", "source"])
            wr.writerow([iid, msg["occurred_at"], msg["confidence"], round(ev["start"], 2), round(end, 2), round(b_s - a_s, 1),
                         "file" if self.file_mode else getattr(self, "source", "mic")])
        clips = sorted(f for f in os.listdir(self.clip_dir) if f.endswith(".wav"))
        for f in clips[:max(0, len(clips) - self.cfg["max_clips"])]:
            os.remove(os.path.join(self.clip_dir, f))


# ------------------------------------------------------------------ 실행
# ------------------------------------------------------------------ 마이크 없는 시험용 소리 (재생 소스)
class ReplaySource:
    """배경 소리(background.wav, 없으면 아주 작은 잡음)를 실시간으로 흘리다가, 요청이 오면 위급 말 샘플을 섞어 넣는다.
    모델·게이트·판단 규칙은 마이크 때와 똑같이 이 소리를 듣는다."""

    def __init__(self, folder, auto_s=0.0):
        fs = sorted(f for f in os.listdir(folder) if f.endswith(".wav"))
        bgf = [f for f in fs if f.startswith("background")]
        self.bg = read_wav(os.path.join(folder, bgf[0])) if bgf else (np.random.default_rng(0).standard_normal(SR * 10) * 2e-4).astype(np.float32)
        self.names = [f for f in fs if not f.startswith("background")]
        self.samples = [read_wav(os.path.join(folder, f)) for f in self.names]
        if not self.samples:
            sys.exit("%s에 위급 말 샘플(wav)이 없어요" % folder)
        self.q, self.k, self.bi = collections.deque(), 0, 0
        self.lock = threading.Lock()
        self.auto_s = auto_s
        self.next_auto = time.time() + auto_s if auto_s else None
        self.t0, self.n = time.time(), 0

    def trigger(self, idx=None):
        with self.lock:
            i = self.k % len(self.samples) if idx is None else idx % len(self.samples)
            self.k += 1
            self.q.append(self.samples[i].copy())
        print("  ▶ 흘려 보냄: %s" % self.names[i], flush=True)

    def read(self):
        wait = self.t0 + (self.n + HOP) / SR - time.time()          # 실제 시간에 맞춰 0.25초씩
        if wait > 0:
            time.sleep(wait)
        idx = (self.bi + np.arange(HOP)) % len(self.bg)
        x = self.bg[idx].copy()
        self.bi = (self.bi + HOP) % len(self.bg)
        if self.next_auto and time.time() >= self.next_auto:
            self.next_auto += self.auto_s
            self.trigger()
        with self.lock:
            if self.q:
                cur = self.q[0]
                m = min(HOP, len(cur))
                x[:m] += cur[:m]
                if m == len(cur):
                    self.q.popleft()
                else:
                    self.q[0] = cur[m:]
        self.n += HOP
        return x


def stdin_trigger(src, stop):
    """엔터 = 다음 샘플, 숫자 = 그 번호 샘플, q = 끝내기"""
    while not stop.is_set():
        line = sys.stdin.readline()
        if not line:
            return
        t = line.strip().lower()
        if t == "q":
            stop.set()
            return
        src.trigger(int(t) - 1 if t.isdigit() else None)


def read_wav(p):
    with wave.open(p) as w:
        ch, sr, raw = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
    if sr != SR:
        sys.exit("16kHz wav만 돼요 (%dHz)" % sr)
    return np.frombuffer(raw, dtype="<i2").reshape(-1, ch)[:, 0].astype(np.float32) / 32768.0


def stats_line(det, eng, t_start):
    ms = list(eng.ms)
    temp, thr = cpu_temp()
    gate = det.stat["gate"] / max(det.stat["steps"], 1)
    return ("[상태] %s  가동 %.0f분 | 판단 %.1fms(최대 %.1f) | 게이트 열림 %.0f%% | 온도 %s | throttled %s | 감지 %d" % (
        time.strftime("%H:%M:%S"), (time.time() - t_start) / 60, np.mean(ms) if ms else 0, max(ms) if ms else 0, 100 * gate,
        "%.1f℃" % temp if temp else "-", thr or "-", len(det.events)), temp, thr, gate, ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--no-mqtt", action="store_true")
    ap.add_argument("--replay", help="마이크 대신 이 폴더의 샘플을 흘려 보내는 시험 모드")
    ap.add_argument("--auto", type=float, default=0, help="--replay에서 N초마다 자동으로 흘려 보냄")
    ap.add_argument("--cfg", default=os.path.join(HERE, "kws_service.json"))
    a = ap.parse_args()
    cfg = load_cfg(a.cfg)
    data_dir = cfg["data_dir"]
    clip_dir = os.path.join(data_dir, "www", "clips")
    os.makedirs(clip_dir, exist_ok=True)
    eng = Engine(cfg)
    snd = Sender(cfg, data_dir, not a.no_mqtt)
    print("모델 %s | 기준 %.3f (모델 설정 %.3f) | 게이트 바닥+%.0fdB, %.0fdBFS 이상" % (
        cfg["model"], cfg["thr"], eng.model_thr or 0, cfg["gate_rel_db"], cfg["gate_abs_dbfs"]))

    if a.file:
        x = read_wav(a.file)
        det = Detector(cfg, eng, snd, clip_dir, data_dir, time.time(), True)
        t1 = time.time()
        for i in range(0, len(x) - HOP + 1, HOP):
            det.push(x[i:i + HOP])
        if det.ev is not None:
            det.try_finish(det.n / SR, force=True)
        dur = len(x) / SR
        print("파일 %.0f초 처리 %.1f초 | 감지 %d번 | 판단 평균 %.1fms | 게이트 열림 %.0f%%" % (
            dur, time.time() - t1, len(det.events), np.mean(eng.ms) if eng.ms else 0, 100 * det.stat["gate"] / max(det.stat["steps"], 1)))
        time.sleep(2)                                   # 마지막 MQTT 결과 출력 기다림
        return

    srv = start_http(os.path.join(data_dir, "www"), cfg["http_port"])
    host = cfg["public_host"] or my_ip()
    print("=" * 60)
    print("  이 파이 주소: %s   (앱·영상 브리지는 이 주소의 1883 포트로 접속)" % host)
    print("  클립 서버:   http://%s:%d/clips/" % (host, cfg["http_port"]))
    print("  MQTT:        %s:%s  토픽 %s  계정 %s" % (cfg["mqtt"]["host"] or "-", cfg["mqtt"]["port"], cfg["mqtt"]["topic"] or "-", cfg["mqtt"]["username"] or "-"))
    print("=" * 60, flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    p, src = None, None
    if a.replay:
        src = ReplaySource(a.replay, a.auto)
        print("마이크 없는 시험 모드: 샘플 %d개" % len(src.samples))
        for i, n in enumerate(src.names, 1):
            print("   %d. %s" % (i, n))
        print("엔터 = 다음 샘플 흘려 보내기 | 번호+엔터 = 그 샘플 | q+엔터 = 끝내기%s" % (" | %g초마다 자동" % a.auto if a.auto else ""), flush=True)
        threading.Thread(target=stdin_trigger, args=(src, stop), daemon=True).start()
    else:
        p = subprocess.Popen(["arecord", "-q", "-D", cfg["device"], "-f", "S16_LE", "-r", str(SR), "-c", str(CH), "-t", "raw"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    det = Detector(cfg, eng, snd, clip_dir, data_dir, time.time(), False)
    det.source = "replay" if src is not None else "mic"
    t_start, last_stat = time.time(), time.time()
    sp = os.path.join(data_dir, "stats.csv")
    need = HOP * CH * 2
    while not stop.is_set():
        if src is not None:
            det.push(src.read())
            b = None
        else:
            b = b""
        while b is not None and len(b) < need:
            c = p.stdout.read(need - len(b))
            if not c:
                break
            b += c
        if b is not None and len(b) < need:
            err = p.stderr.read().decode(errors="ignore")
            print("마이크 오류: %s" % (err.strip() or "소리를 못 받음"), flush=True)
            if "busy" in err.lower():
                print("  다른 프로그램(rec_session, voice_service)이 마이크를 쓰고 있어요")
            break
        if b is not None:
            x = np.frombuffer(b, dtype="<i2").reshape(-1, CH)[:, 0].astype(np.float32) / 32768.0
            det.push(x)
        if time.time() - last_stat >= 60:
            last_stat = time.time()
            line, temp, thr, gate, ms = stats_line(det, eng, t_start)
            print(line, flush=True)
            new = not os.path.exists(sp)
            with open(sp, "a", newline="") as f:
                wr = csv.writer(f)
                if new:
                    wr.writerow(["time", "uptime_min", "infer_ms_mean", "infer_ms_max", "gate_open", "temp_c", "throttled", "events"])
                wr.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), round((time.time() - t_start) / 60, 1), round(float(np.mean(ms)), 1) if ms else "",
                             round(max(ms), 1) if ms else "", round(gate, 3), temp, thr, len(det.events)])
    if det.ev is not None:
        det.try_finish(det.n / SR, force=True)
    if p is not None:
        p.terminate()
    time.sleep(1)
    srv.shutdown()
    print("멈췄어요.")


if __name__ == "__main__":
    main()
