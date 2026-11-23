"""
독거노인 안심 모니터 - 서버
AI 휴먼케어 학생연구회 · 독거노인 위험 감지 및 알림 시스템

역할 (김우석 MQTT 배포 문서 v1.1.0의 '앱' 자리):
  - app 계정으로 R1 브로커에 접속해 home/alert/#, home/status/# 를 구독만 한다 (발행 없음).
  - 받은 원문을 그대로 기록하고, 팀 JSON 규약(4필드)을 검사해 화면에 보여 준다.
  - 확인됨/오경보 표시는 이 노트북에만 저장한다 (파이로 보내지 않음).
  - 판단 등급(긴급/주의)은 만들지 않는다.

실행:
  실시간: python server.py --host <R1 IP>      (비밀번호는 실행 시 입력 또는 MQTT_PASSWORD)
  데모  : python server.py --demo
"""
import argparse
import getpass
import json
import os
import queue
import re
import socket
import threading
import time
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

KST = timezone(timedelta(hours=9))
BASE = Path(__file__).resolve().parent
ALERT_PREFIX, STATUS_PREFIX = "home/alert/", "home/status/"
CONTRACT_FIELDS = ("incident_id", "confidence", "occurred_at", "clip_url")
LABELS = ("confirmed", "false_alarm", "none")
CLIP_MAX_BYTES = 50 * 1024 * 1024


def now():
    return datetime.now(KST)


def iso(dt):
    return dt.isoformat(timespec="milliseconds")


# ───────────────────────── 규약 검사 ─────────────────────────
def check_contract(source, raw):
    """팀 JSON 규약(4필드)을 검사한다. 메시지는 고치지 않고 결과만 돌려준다.
    level: ok / warn / error"""
    problems = []  # (level, 내용)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, {"level": "error", "problems": [["error", "JSON으로 읽을 수 없음"]]}
    if not isinstance(data, dict):
        return None, {"level": "error", "problems": [["error", "JSON 객체가 아님"]]}

    for k in CONTRACT_FIELDS:
        if k not in data:
            problems.append(["error", f"필수 필드 없음: {k}"])
    extra = [k for k in data if k not in CONTRACT_FIELDS]
    if extra:
        problems.append(["warn", "규약 외 필드: " + ", ".join(extra)])

    iid = data.get("incident_id")
    if "incident_id" in data:
        if not isinstance(iid, str) or not iid.strip():
            problems.append(["error", "incident_id가 비어 있거나 문자열이 아님"])
        elif not re.fullmatch(r"[A-Za-z]+_\d{12}(_\d+)?", iid):
            problems.append(["warn", "incident_id가 '접두어_YYMMDDhhmmss' 예시 형식과 다름"])
        elif source == "audio" and not iid.startswith("A_"):
            problems.append(["warn", "음성 incident_id가 A_로 시작하지 않음"])

    if "confidence" in data:
        c = data.get("confidence")
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            problems.append(["error", "confidence가 숫자가 아님"])
        elif not 0 <= c <= 1:
            problems.append(["warn", "confidence가 0~1 범위를 벗어남"])

    if "occurred_at" in data:
        try:
            t = datetime.fromisoformat(str(data["occurred_at"]))
            if t.tzinfo is None:
                problems.append(["warn", "occurred_at에 시간대(+09:00)가 없음"])
        except ValueError:
            problems.append(["error", "occurred_at을 시각으로 읽을 수 없음"])

    if "clip_url" in data:
        u = data.get("clip_url")
        if not isinstance(u, str) or not re.match(r"https?://", u):
            problems.append(["error", "clip_url이 http(s) 주소가 아님"])

    level = "error" if any(p[0] == "error" for p in problems) else "warn" if problems else "ok"
    return data, {"level": level, "problems": problems}


def parse_time(value):
    try:
        t = datetime.fromisoformat(str(value))
        return t if t.tzinfo else t.replace(tzinfo=KST)
    except ValueError:
        return None


# ───────────────────────── 저장소 ─────────────────────────
class Store:
    def __init__(self, data_dir, live):
        self.dir = Path(data_dir)
        (self.dir / "clips").mkdir(parents=True, exist_ok=True)
        self.events_file = self.dir / "events.jsonl"   # 받은 원문 그대로 (경보·상태)
        self.labels_file = self.dir / "labels.jsonl"   # 확인됨/오경보 표시
        self.live = live
        self.lock = threading.Lock()
        self.events, self.order, self.noid = {}, [], 0
        self.devices = {d: {"value": "unknown", "since": None, "retained": False} for d in ("audio", "vision")}
        self.history = []          # 상태 변화 기록
        self.broker = {"connected": not live, "since": iso(now()), "error": None}
        self.started_at = iso(now())
        self.clients = []
        self._replay_files()

    # 실시간 전달 (SSE)
    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, kind, data):
        msg = json.dumps({"kind": kind, "data": data}, ensure_ascii=False)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def _append(self, path, obj):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _replay_files(self):
        """다시 켜도 이전 기록이 화면에 남도록 파일에서 복원"""
        if self.events_file.exists():
            for line in self.events_file.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "alert":
                    self.ingest_alert(r["topic"], r["payload"], r["received_at"], save=False, notify=False)
                elif r.get("type") == "status":
                    self.ingest_status(r["topic"], r["payload"], r["received_at"], r.get("retained", False),
                                       save=False, notify=False)
        if self.labels_file.exists():
            for line in self.labels_file.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    self.set_label(r["key"], r["label"], r["labeled_at"], save=False, notify=False)
                except (ValueError, KeyError):
                    continue

    # 경보 메시지
    def ingest_alert(self, topic, raw, received_at=None, save=True, notify=True):
        received_at = received_at or iso(now())
        source = topic[len(ALERT_PREFIX):] or "unknown"
        if save:
            self._append(self.events_file, {"type": "alert", "topic": topic, "payload": raw, "received_at": received_at})
        data, contract = check_contract(source, raw)
        iid = data.get("incident_id") if data else None
        with self.lock:
            if isinstance(iid, str) and iid.strip():
                key = iid.strip()
            else:
                self.noid += 1
                key = f"규약오류-{self.noid}"
            if key in self.events:                       # QoS 1 중복 전달 등
                ev = self.events[key]
                ev["dup_count"] += 1
                if raw != ev["raw"]:
                    ev["dup_conflict"] = True           # 같은 번호인데 내용이 다름
                kind, out = "dup", {"key": key, "dup_count": ev["dup_count"], "dup_conflict": ev.get("dup_conflict", False)}
            else:
                occ = parse_time(data.get("occurred_at")) if data else None
                rec = parse_time(received_at)
                conf = data.get("confidence") if data else None
                ev = {
                    "key": key, "source": source, "topic": topic, "raw": raw,
                    "incident_id": iid, "confidence": conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None,
                    "occurred_at": data.get("occurred_at") if data else None,
                    "clip_url": data.get("clip_url") if data else None,
                    "received_at": received_at,
                    "delay_sec": round((rec - occ).total_seconds(), 1) if occ and rec else None,
                    "same_second": bool(isinstance(iid, str) and re.search(r"_\d+$", iid) and iid.count("_") >= 2),
                    "contract": contract, "dup_count": 0, "label": "none", "labeled_at": None, "response_sec": None,
                }
                self.events[key] = ev
                self.order.append(key)
                kind, out = "alert", ev
        if notify:
            self.broadcast(kind, out)

    # 상태 메시지 (online/offline 문자열)
    def ingest_status(self, topic, payload, received_at=None, retained=False, save=True, notify=True):
        received_at = received_at or iso(now())
        device = topic[len(STATUS_PREFIX):] or "unknown"
        value = str(payload).strip()
        if save:
            self._append(self.events_file, {"type": "status", "topic": topic, "payload": value,
                                            "received_at": received_at, "retained": retained})
        with self.lock:
            d = self.devices.setdefault(device, {"value": "unknown", "since": None, "retained": False})
            d.update({"value": value, "since": received_at, "retained": retained})
            h = {"device": device, "value": value, "at": received_at, "retained": retained}
            self.history.append(h)
        if notify:
            self.broadcast("status", h)

    def set_broker(self, connected, error=None):
        with self.lock:
            self.broker = {"connected": connected, "since": iso(now()), "error": error}
            b = dict(self.broker)
            self.history.append({"device": "broker", "value": "connected" if connected else "disconnected",
                                 "at": b["since"], "retained": False})
        self.broadcast("broker", b)

    # 확인됨 / 오경보
    def set_label(self, key, label, labeled_at=None, save=True, notify=True):
        if label not in LABELS:
            return None
        labeled_at = labeled_at or iso(now())
        with self.lock:
            ev = self.events.get(key)
            if not ev:
                return None
            ev["label"] = label
            ev["labeled_at"] = labeled_at if label != "none" else None
            if label != "none":
                rec, lab = parse_time(ev["received_at"]), parse_time(labeled_at)
                ev["response_sec"] = round((lab - rec).total_seconds(), 1) if rec and lab else None
            else:
                ev["response_sec"] = None
            out = {k: ev[k] for k in ("key", "label", "labeled_at", "response_sec")}
        if save:
            self._append(self.labels_file, {"key": key, "label": label, "labeled_at": labeled_at})
        if notify:
            self.broadcast("label", out)
        return out

    def snapshot(self):
        with self.lock:
            devices = {k: dict(v) for k, v in self.devices.items()}
            if self.live and not self.broker["connected"]:
                for v in devices.values():   # 문서 규칙: 브로커와 끊기면 둘 다 확인 불가
                    v["value"] = "unknown"
            return {"events": [self.events[k] for k in self.order], "devices": devices,
                    "broker": dict(self.broker), "history": list(self.history[-500:]),
                    "started_at": self.started_at}

    def reset(self):
        with self.lock:
            self.events, self.order, self.history, self.noid = {}, [], [], 0
            for d in self.devices.values():
                d.update({"value": "unknown", "since": None, "retained": False})
        for f in (self.events_file, self.labels_file):
            if f.exists():
                f.unlink()
        self.broadcast("reset", {})

    def archive(self):
        """지금까지의 기록(원문·표시·클립)을 data/archive/<시각>/ 로 옮기고 새로 시작"""
        import shutil
        dest = self.dir.parent / "archive" / f"{self.dir.name}_{now().strftime('%Y%m%d_%H%M%S')}"
        dest.mkdir(parents=True, exist_ok=True)
        with self.lock:
            for f in (self.events_file, self.labels_file):
                if f.exists():
                    shutil.move(str(f), str(dest / f.name))
            clips = self.dir / "clips"
            if clips.exists() and any(clips.iterdir()):
                shutil.move(str(clips), str(dest / "clips"))
            clips.mkdir(parents=True, exist_ok=True)
            self.events, self.order, self.noid = {}, [], 0
            t = iso(now())
            self.history = [{"device": d, "value": v["value"], "at": t, "retained": False} for d, v in self.devices.items()]
        self.broadcast("reset", {})
        return str(dest)


# ───────────────────────── 클립 가져오기·보관 ─────────────────────────
def fetch_clip(store, key):
    """clip_url을 서버가 대신 가져와 노트북에 보관한다.
    - 휴대폰·브라우저가 클립 서버에 직접 붙지 않아도 재생 가능 (CORS 문제 회피)
    - 원본 서버에서 지워져도 기록이 남음"""
    ev = store.events.get(key)
    if not ev or not ev.get("clip_url"):
        return {"ok": False, "error": "clip_url 없음"}
    url = ev["clip_url"]
    ext = Path(url.split("?")[0]).suffix.lower() or ".bin"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    path = store.dir / "clips" / f"{safe}{ext}"
    info = {"url": url, "path": path, "cached": path.exists()}
    if not path.exists():
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "care-dashboard"})
            with urllib.request.urlopen(req, timeout=8) as r:
                ctype = r.headers.get("Content-Type", "")
                body = r.read(CLIP_MAX_BYTES + 1)
            if len(body) > CLIP_MAX_BYTES:
                return {"ok": False, "error": "클립이 50MB를 넘음"}
            path.write_bytes(body)
            info.update({"http_status": 200, "content_type": ctype, "fetch_ms": int((time.time() - t0) * 1000)})
        except Exception as e:  # 접속 불가, 404 등
            return {"ok": False, "error": f"클립 접근 실패: {e}", "fetch_ms": int((time.time() - t0) * 1000)}
    info.update({"ok": True, "bytes": path.stat().st_size, "ext": ext})
    if ext == ".wav":
        try:
            with wave.open(str(path)) as w:
                info.update({"duration_sec": round(w.getnframes() / w.getframerate(), 2),
                             "sample_rate": w.getframerate(), "channels": w.getnchannels()})
        except wave.Error as e:
            info["warn"] = f"wav 헤더를 읽지 못함: {e}"
    return info


# ───────────────────────── MQTT (실시간 모드) ─────────────────────────
def start_mqtt(store, host, port, username, password):
    import paho.mqtt.client as paho

    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                         client_id=f"care-dashboard-{socket.gethostname()}"[:40], protocol=paho.MQTTv311)
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=1, max_delay=10)

    def on_connect(c, ud, flags, rc, props):
        if rc.is_failure:
            store.set_broker(False, f"연결 거부: {rc} (계정·비밀번호 확인)")
            return
        store.set_broker(True)
        c.subscribe([(ALERT_PREFIX + "#", 1), (STATUS_PREFIX + "#", 1)])

    def on_disconnect(c, ud, flags, rc, props):
        store.set_broker(False, f"연결 끊김: {rc}" if rc.is_failure else None)

    def on_connect_fail(c, ud):
        store.set_broker(False, f"브로커 {host}:{port} TCP 연결 실패 (IP·네트워크 확인)")

    def on_message(c, ud, m):
        try:
            payload = m.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload = m.payload.decode("utf-8", errors="replace")
        if m.topic.startswith(ALERT_PREFIX):
            store.ingest_alert(m.topic, payload)
        elif m.topic.startswith(STATUS_PREFIX):
            store.ingest_status(m.topic, payload, retained=bool(m.retain))

    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.on_connect_fail, client.on_message = on_connect_fail, on_message
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    return client


# ───────────────────────── 데모 재생 ─────────────────────────
class Demo:
    def __init__(self, store, web_port):
        self.store, self.web_port = store, web_port
        self.thread, self.stop_flag = None, threading.Event()

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, speed=1.0):
        if self.running():
            return False
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, args=(max(0.5, min(float(speed), 20.0)),), daemon=True)
        self.thread.start()
        self.store.broadcast("demo", {"running": True, "speed": speed})
        return True

    def stop(self):
        self.stop_flag.set()

    def _clip(self, name):
        return f"http://127.0.0.1:{self.web_port}/samples/clips/{name}"

    def _run(self, speed):
        steps = json.loads((BASE / "samples" / "demo_scenario.json").read_text(encoding="utf-8"))["steps"]
        t0, last, used = time.time(), {}, set(self.store.events)
        for s in steps:
            wait = t0 + s["at"] / speed - time.time()
            if wait > 0 and self.stop_flag.wait(wait):
                break
            k = s["kind"]
            if k == "status":
                self.store.ingest_status(STATUS_PREFIX + s["device"], s["value"])
            elif k == "raw":
                self.store.ingest_alert(s["topic"], json.dumps(s["payload"], ensure_ascii=False))
            elif k == "resend_previous_vision" and "vision" in last:
                self.store.ingest_alert(*last["vision"])
            elif k == "alert":
                src = s["source"]
                if s.get("same_second_as_previous") and src in last:
                    prev = json.loads(last[src][1])
                    occ, iid = prev["occurred_at"], prev["incident_id"] + "_2"
                else:
                    t = now() - timedelta(seconds=s.get("ago", 1.5 if src == "audio" else 0.5))
                    occ = t.isoformat(timespec="seconds")
                    iid = ("A_" if src == "audio" else "V_") + t.strftime("%y%m%d%H%M%S")  # V_는 임시
                    base, n = iid, 2
                    while iid in used:          # 같은 초에 겹치면 _2, _3 (음성 팀 규칙과 동일)
                        iid, n = f"{base}_{n}", n + 1
                used.add(iid)
                payload = json.dumps({"incident_id": iid, "confidence": s["confidence"],
                                      "occurred_at": occ, "clip_url": self._clip(s["clip"])}, ensure_ascii=False)
                last[src] = (ALERT_PREFIX + src, payload)
                self.store.ingest_alert(ALERT_PREFIX + src, payload)
        self.store.broadcast("demo", {"running": False})


# ───────────────────────── 웹 서버 ─────────────────────────
def lan_ip(target=None):
    for t in [target, "8.8.8.8"]:
        if not t:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((t, 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            continue
    return "127.0.0.1"


def create_app(store, demo, info):
    app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")

    @app.get("/")
    def index():
        return send_from_directory(BASE / "static", "index.html")

    @app.get("/samples/<path:name>")
    def samples(name):
        return send_from_directory(BASE / "samples", name)

    @app.get("/api/info")
    def api_info():
        return jsonify({**info, "demo_running": demo.running() if demo else False})

    @app.get("/api/state")
    def api_state():
        return jsonify({**store.snapshot(), "info": {**info, "demo_running": demo.running() if demo else False}})

    @app.get("/api/stream")
    def api_stream():
        q = store.subscribe()

        def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        yield f"data: {q.get(timeout=15)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            finally:
                store.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/label")
    def api_label():
        body = request.get_json(silent=True) or {}
        out = store.set_label(str(body.get("key", "")), str(body.get("label", "")))
        return (jsonify(out), 200) if out else (jsonify({"error": "이벤트 또는 표시 값이 올바르지 않음"}), 400)

    @app.get("/api/clipcheck/<path:key>")
    def api_clipcheck(key):
        r = fetch_clip(store, key)
        r.pop("path", None)
        return jsonify(r)

    @app.get("/api/clip/<path:key>")
    def api_clip(key):
        r = fetch_clip(store, key)
        if not r.get("ok"):
            abort(404)
        return send_file(r["path"], conditional=True)   # 구간 재생(Range) 지원

    @app.get("/api/export.<fmt>")
    def api_export(fmt):
        evs = store.snapshot()["events"]
        if fmt == "json":
            return Response(json.dumps(evs, ensure_ascii=False, indent=2), mimetype="application/json",
                            headers={"Content-Disposition": "attachment; filename=care_events.json"})
        if fmt == "csv":
            cols = ["source", "incident_id", "confidence", "occurred_at", "received_at", "delay_sec",
                    "clip_url", "label", "labeled_at", "response_sec", "dup_count", "same_second", "contract_level"]

            def cell(v):
                v = "" if v is None else str(v)
                return '"' + v.replace('"', '""') + '"'

            lines = [",".join(cols)]
            for e in evs:
                row = {**e, "contract_level": e["contract"]["level"]}
                lines.append(",".join(cell(row.get(c)) for c in cols))
            return Response("\ufeff" + "\n".join(lines), mimetype="text/csv",   # BOM: 엑셀 한글 깨짐 방지
                            headers={"Content-Disposition": "attachment; filename=care_events.csv"})
        abort(404)

    @app.get("/api/qr.svg")
    def api_qr():
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(info["lan_url"], image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
        return Response(img.to_string(), mimetype="image/svg+xml")

    @app.post("/api/session/new")
    def api_session_new():
        if demo:
            demo.stop()
        return jsonify({"archived_to": store.archive()})

    @app.get("/api/export.raw")
    def api_export_raw():
        if not store.events_file.exists():
            return Response("", mimetype="text/plain")
        return send_file(store.events_file, as_attachment=True, download_name="events_raw.jsonl", mimetype="application/json")

    @app.post("/api/demo/<action>")
    def api_demo(action):
        if not demo:
            return jsonify({"error": "데모 모드에서만 사용"}), 400
        if action == "start":   # 이전 데모 기록과 섞이지 않게 처음부터 재생
            if demo.running():
                return jsonify({"started": False, "reason": "이미 재생 중"})
            store.reset()
            return jsonify({"started": demo.start((request.get_json(silent=True) or {}).get("speed", 1))})
        if action == "stop":
            demo.stop()
            return jsonify({"stopped": True})
        if action == "reset":
            demo.stop()
            store.reset()
            return jsonify({"reset": True})
        abort(404)

    return app


def main():
    p = argparse.ArgumentParser(description="독거노인 안심 모니터")
    p.add_argument("--demo", action="store_true", help="파이 없이 데모 시나리오로 실행")
    p.add_argument("--host", default=os.getenv("MQTT_HOST"), help="R1 브로커 IP (auto = 같은 네트워크에서 자동으로 찾기)")
    p.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--user", default=os.getenv("MQTT_USERNAME", "app"))
    p.add_argument("--web-port", type=int, default=int(os.getenv("WEB_PORT", "5000")))
    a = p.parse_args()

    if a.demo:
        store = Store(BASE / "data" / "demo", live=False)
        demo = Demo(store, a.web_port)
        mode = "demo"
    else:
        if a.host in (None, "", "auto"):   # 핫스팟에서는 IP가 바뀌므로 자동으로 찾기
            from r1_finder import find_brokers
            hits = find_brokers(a.mqtt_port)
            if not hits:
                raise SystemExit("R1(1883 포트)을 찾지 못했습니다. R1 전원·핫스팟 연결을 확인하거나 --host로 IP를 지정하세요.")
            if len(hits) > 1:
                print(f"[R1 찾기] 후보가 여러 개입니다: {hits} → 첫 번째를 사용합니다 (다르면 --host로 지정)")
            a.host = hits[0]
            print(f"[R1 찾기] R1 = {a.host}", flush=True)
        password = os.getenv("MQTT_PASSWORD") or getpass.getpass(f"{a.user} MQTT 비밀번호: ")
        store = Store(BASE / "data" / "live", live=True)
        demo = None
        mode = "live"
        start_mqtt(store, a.host, a.mqtt_port, a.user, password)

    ip = lan_ip(a.host)
    info = {"mode": mode, "broker": f"{a.host}:{a.mqtt_port}" if a.host else None, "user": a.user if mode == "live" else None,
            "lan_url": f"http://{ip}:{a.web_port}", "local_url": f"http://127.0.0.1:{a.web_port}"}
    print(f"[안심 모니터] 모드={mode}  이 PC: {info['local_url']}  휴대폰(같은 Wi-Fi): {info['lan_url']}", flush=True)
    from werkzeug.serving import WSGIRequestHandler
    warned = set()

    class Handler(WSGIRequestHandler):
        def _tls(self):
            return getattr(self, "raw_requestline", b"")[:2] == b"\x16\x03"

        def log_error(self, fmt, *args):
            if self._tls():
                ip = self.client_address[0]
                if ip not in warned:
                    warned.add(ip)
                    print(f"[안내] {ip} 기기가 https로 접속을 시도했습니다 → 주소를 {info['lan_url']} 처럼 http:// 로 입력하세요", flush=True)
                return
            super().log_error(fmt, *args)

        def log_request(self, code="-", size="-"):
            if self._tls():
                return
            super().log_request(code, size)

    create_app(store, demo, info).run(host="0.0.0.0", port=a.web_port, threaded=True, request_handler=Handler)


if __name__ == "__main__":
    main()
