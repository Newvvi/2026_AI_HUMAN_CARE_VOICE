"""
voice_service.py v0.3: 파이용 음성 위급 단어 감지 (파일 시뮬레이션 + 실시간 시뮬레이션 + 마이크 + MQTT)

확정한 기본 설정 (2026-11-08, 파이4 실측, docs/11 참고):
  소리 크기 -28dBFS 이상 구간만 Whisper tiny(int8, 탐색 폭 5)로 글자 인식 -> 위급 단어 규칙(KEY)
  첫 인식에 "주세/와줘/도와/살려/구해/119/일일구" 조각이 있지만 단어는 없을 때만 small로 한 번 더 인식 (미리 알려 주는 단어는 쓰지 않음)
  같은 말이 3초 안에 반복되면 하나로 치고, 직전 30초 안의 횟수를 repeat_in_30s로 함께 보냄
흐름: 구간 -> 글자 -> 규칙 -> 이벤트(help_request)를 JSON 줄로 기록, MQTT 전송(선택), 앞뒤 10초 클립 저장

v0.3에서 추가: small 재시도를 별도 작업 줄(스레드)로 분리 (실시간·마이크 모드), 클립 개수·디스크 상한, 설정 파일(--config),
  실시간 속도 시뮬레이션(--realtime, --loop, --speed)과 이벤트 지연 측정, 상태 파일(--status-file), 종료 신호(SIGTERM) 처리, 마이크 재연결

사용법 (파이, 가상환경을 켠 상태, pip install faster-whisper numpy [paho-mqtt sounddevice]):
  python3 voice_service.py --wav <파일> --out <결과 이름>            # 파일을 빠르게 흘려 보내 시험 (구간별 글자 기록 포함)
  python3 voice_service.py --wav <파일> --realtime --out <이름>      # 실제 시간 속도로 흘려 보내 지연·밀림 시험
  python3 voice_service.py --wav <파일> --realtime --loop 20 --status-file status.json   # 장시간 안정 시험
  python3 voice_service.py --wav <파일> --dry-run                    # 기준 음량별 구간 수만 계산
  python3 voice_service.py --list-devices                            # 마이크 목록 (reSpeaker 도착 후)
  python3 voice_service.py --mic --device <번호> --channels 2        # 실제 마이크 (아직 시험하지 못함)
  python3 voice_service.py --config voice_service.json               # 설정 파일 (비밀번호는 넣지 않음)
  MQTT: --mqtt-host <주소> --mqtt-user <계정>  (비밀번호는 환경변수 MQTT_PASSWORD, 저장소에 쓰지 않음)
결과: <out>_events.jsonl, <out>_clips/, (파일 시험에서만) <out>_windows.jsonl: 인식한 글자라서 개인 대화가 들어 있으니 올리지 않음
"""
import argparse
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
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


SR, FR, FSEC = 16000, 8000, 0.5            # 16kHz, 분석 단위 0.5초
END = r"(?:주세요?|주십시오|주십쇼|줘요|줘(?!서)|주라|달라|주셔요)"
KEY = re.compile(r"(?:^|[\s,.!?])(?:(?:도와|살려|구해)\s*%s|사람\s*살려|119\s*(?:불러|신고|에\s*전화)|일일구\s*(?:불러|신고)|불러\s*(?:주세요?|줘요?|주십시오)|살려\s*[!.?]*\s*$)" % END)

pre = argparse.ArgumentParser(add_help=False)
pre.add_argument("--config", help="설정 파일(JSON, 키는 옵션 이름에서 -를 _로). 비밀번호는 넣지 않고 환경변수 MQTT_PASSWORD 사용")
pre_args, _ = pre.parse_known_args()

ap = argparse.ArgumentParser(parents=[pre])
src = ap.add_mutually_exclusive_group()
src.add_argument("--wav", help="오디오 파일 (m4a, wav 등)")
src.add_argument("--mic", action="store_true", help="마이크에서 실시간으로 받기")
ap.add_argument("--list-devices", action="store_true")
ap.add_argument("--device", help="마이크 번호나 이름 (--list-devices로 확인)")
ap.add_argument("--channels", type=int, default=1)
ap.add_argument("--mic-channel", type=int, default=0, help="채널이 2개일 때 쓸 채널 (0 또는 1)")
ap.add_argument("--max-minutes", type=float, default=0, help="마이크 모드에서 이 시간(분) 뒤 종료 (0이면 계속)")
ap.add_argument("--out", default="run", help="결과 파일 이름 앞부분")
ap.add_argument("--model", default="tiny")
ap.add_argument("--threads", type=int, default=2)
ap.add_argument("--gate-db", type=float, default=-28.0, help="이 크기(dBFS) 이상인 구간만 Whisper로 보냄")
ap.add_argument("--gap", type=float, default=1.0, help="이 시간(초) 이상 조용하면 구간 끝")
ap.add_argument("--pad", type=float, default=0.5)
ap.add_argument("--max-win", type=float, default=10.0)
ap.add_argument("--min-win", type=float, default=0.8)
ap.add_argument("--same-gap", type=float, default=3.0, help="이 시간(초) 안의 중복 인식은 하나로 침")
ap.add_argument("--repeat-within", type=float, default=30.0, help="반복 횟수를 셀 시간(초)")
ap.add_argument("--max-per-min", type=int, default=20, help="CPU 보호: 오디오 1분에 처리할 구간 상한")
ap.add_argument("--beam", type=int, default=5, help="Whisper 탐색 폭 (1=빠름, 5=더 꼼꼼하지만 느림)")
ap.add_argument("--prompt", default="", help="Whisper에 미리 알려 줄 단어 (쓰지 않음: 오경보 폭증)")
ap.add_argument("--norm-peak", type=float, default=0.0, help="Whisper에 보내기 전 구간의 최대 크기를 이 값으로 키움 (0이면 안 함)")
ap.add_argument("--norm-max-gain-db", type=float, default=24.0, help="키우는 한도(dB)")
ap.add_argument("--retry-mode", choices=["all", "cond", "off"], default="cond",
                help="위급 단어가 없을 때 더 큰 모델로 한 번 더 인식: all=모든 구간, cond=비슷한 조각이 있을 때만, off=안 함")
ap.add_argument("--retry", action="store_true", help="(예전 옵션, 무시됨)")
ap.add_argument("--retry-all", action="store_true", help="(예전 옵션, 무시됨)")
ap.add_argument("--retry-temp", type=float, default=0.0, help="재시도 온도 (0이면 무작위 없이 같은 결과)")
ap.add_argument("--retry-prompt", default="", help="재시도할 때만 미리 알려 줄 단어 (쓰지 않음)")
ap.add_argument("--retry-model", default="small", help="재시도에 쓸 더 큰 모델 (비워 두면 같은 모델)")
ap.add_argument("--retry-if", default=r"주세|와줘|도와|살려|구해|119|일일구")
ap.add_argument("--sync-retry", action="store_true", help="재시도를 같은 줄에서 처리 (파일 시험의 기본값)")
ap.add_argument("--async-retry", action="store_true", help="재시도를 별도 작업 줄로 처리 (실시간·마이크의 기본값)")
ap.add_argument("--retry-queue", type=int, default=3, help="재시도 대기 줄 길이 (가득 차면 건너뜀)")
ap.add_argument("--max-clips", type=int, default=200, help="저장할 클립 최대 개수 (넘으면 오래된 것부터 삭제)")
ap.add_argument("--min-free-gb", type=float, default=1.0, help="디스크 여유가 이보다 적으면 클립을 저장하지 않음")
ap.add_argument("--log-windows", action="store_true", help="구간별 인식 글자를 파일로 기록 (개인 대화 포함, 파일 시험에서는 기본으로 켜짐)")
ap.add_argument("--no-log-windows", action="store_true")
ap.add_argument("--realtime", action="store_true", help="--wav를 실제 시간 속도로 흘려 보냄 (지연·밀림 시험)")
ap.add_argument("--speed", type=float, default=1.0, help="--realtime의 배속 (시험용, 1이면 실제 속도)")
ap.add_argument("--loop", type=int, default=1, help="--wav를 이 횟수만큼 반복 (장시간 시험)")
ap.add_argument("--status-file", help="30초마다 상태(JSON)를 이 파일에 씀")
ap.add_argument("--device-id", default="pi-voice")
ap.add_argument("--mqtt-host")
ap.add_argument("--mqtt-port", type=int, default=1883)
ap.add_argument("--mqtt-topic", default="home/alert/audio")
ap.add_argument("--mqtt-user")
ap.add_argument("--dry-run", action="store_true", help="구간 개수만 세고 끝냄 (--wav 필요)")
if pre_args.config:
    cfg = json.load(open(pre_args.config, encoding="utf-8"))
    bad_keys = [k for k in cfg if "pass" in k.lower() or "secret" in k.lower() or "token" in k.lower()]
    if bad_keys:
        raise SystemExit(f"설정 파일에 비밀번호·토큰을 넣지 마세요 ({bad_keys}). 환경변수 MQTT_PASSWORD를 쓰세요.")
    ap.set_defaults(**{k.replace("-", "_"): v for k, v in cfg.items()})
args = ap.parse_args()
RETRY_IF = re.compile(args.retry_if)
LIVE = bool(args.realtime or (args.mic and not args.wav))
LOG_WINDOWS = bool(args.log_windows or (not args.no_log_windows and args.wav and not args.realtime))
ASYNC_RETRY = bool(args.async_retry or (LIVE and not args.sync_retry))
STOP = threading.Event()


def on_signal(signum, frame):
    STOP.set()


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def temp_c():
    m = re.search(r"([\d.]+)", sh(["vcgencmd", "measure_temp"]))
    return float(m.group(1)) if m else None


def rss_mb():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS"):
                return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    return None


def thr_text(raw):
    try:
        v = int(raw.split("=")[-1], 16)
    except ValueError:
        return raw or "?"
    now = [n for b, n in ((1, "저전압"), (2, "클럭 제한"), (4, "스로틀링"), (8, "온도 소프트제한")) if v & b]
    past = [n for b, n in ((0x10000, "저전압"), (0x20000, "클럭 제한"), (0x40000, "스로틀링"), (0x80000, "온도 소프트제한")) if v & b]
    return f"{raw} (지금: {', '.join(now) or '없음'} / 부팅 후 발생: {', '.join(past) or '없음'})"


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else 0.0


class Engine:
    def __init__(self, gate_db, out=None, dry=False):
        self.gate, self.dry = gate_db, dry
        self.t0 = datetime.now().astimezone()
        self.base_wall = None               # 실시간 모드에서 스트림 시작 시각(time.time())
        self.n = 0                          # 지금까지 받은 프레임 수
        self.frames, self.start, self.last = {}, None, None
        self.core = args.max_win - 2 * args.pad
        self.windows = []                   # dry-run용
        self.lock = threading.RLock()
        self.pending, self.recent, self.counted = [], [], []
        self.times, self.retry_times, self.latencies = [], [], []
        self.skipped = self.tried = self.retried = self.retry_dropped = self.clips_skipped = self.n_ev = 0
        self.t_max, self.mq, self.rq, self.worker = temp_c(), None, None, None
        self.worker_stop = threading.Event()
        self.wf = None
        self.started = time.time()
        if dry:
            return
        self.out = Path(out)
        self.clips = Path(str(self.out) + "_clips"); self.clips.mkdir(parents=True, exist_ok=True)
        if LOG_WINDOWS:
            self.wf = open(str(self.out) + "_windows.jsonl", "w", encoding="utf-8")
        self.ef = open(str(self.out) + "_events.jsonl", "a" if LIVE else "w", encoding="utf-8")
        self.model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
        self.retry_model = (WhisperModel(args.retry_model, device="cpu", compute_type="int8", cpu_threads=args.threads)
                            if (args.retry_mode != "off" and args.retry_model and args.retry_model != args.model) else None)
        if args.retry_mode != "off" and ASYNC_RETRY:
            self.rq = queue.Queue(maxsize=max(1, args.retry_queue))
            self.worker = threading.Thread(target=self.retry_loop, daemon=True)
            self.worker.start()
        if args.mqtt_host:
            import paho.mqtt.client as mqtt
            try:
                self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                self.mq = mqtt.Client()
            if args.mqtt_user:
                self.mq.username_pw_set(args.mqtt_user, os.environ.get("MQTT_PASSWORD", ""))
            self.mq.connect(args.mqtt_host, args.mqtt_port, 60)
            self.mq.loop_start()
        if args.status_file:
            threading.Thread(target=self.status_loop, daemon=True).start()

    def start_clock(self, base=None):
        self.base_wall = base if base is not None else time.time()
        self.t0 = datetime.now().astimezone()

    # ---- 오디오 받기 ----
    def push(self, block):
        k = self.n
        self.n += 1
        self.frames[k] = block
        for old in [i for i in self.frames if i < k - 120]:        # 최근 60초만 보관
            del self.frames[old]
        db = 10 * math.log10(float((block.astype(np.float64) ** 2).mean()) + 1e-12)
        if db >= self.gate:
            if self.start is None:
                self.start = k
            self.last = k
            if (k - self.start + 1) * FSEC >= self.core:           # 계속 시끄러우면 중간에서 끊음(1초 겹침)
                self.emit(self.start, k)
                self.start = k - 1
        elif self.start is not None and (k - self.last) * FSEC >= args.gap:
            self.emit(self.start, self.last)
            self.start = None
        if not self.dry:
            self.save_clips()

    def flush(self):
        if self.start is not None:
            self.emit(self.start, self.last)
            self.start = None
        if not self.dry:
            self.save_clips(final=True)

    def get(self, a, b):
        i0, i1 = int(a / FSEC), int(math.ceil(b / FSEC))
        parts = [self.frames[i] for i in range(i0, i1) if i in self.frames]
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    # ---- 구간 -> 인식 -> 이벤트 ----
    def transcribe(self, arr, temperature, prompt=None, model=None):
        if args.norm_peak > 0 and len(arr):
            pk = float(np.abs(arr).max())
            if pk > 1e-4:
                arr = arr * np.float32(min(args.norm_peak / pk, 10 ** (args.norm_max_gain_db / 20)))
        segs, _ = (model or self.model).transcribe(arr, language="ko", beam_size=args.beam, initial_prompt=(prompt or args.prompt or None),
                                                   vad_filter=False, condition_on_previous_text=False,
                                                   temperature=temperature, without_timestamps=True, max_new_tokens=32)
        segs = list(segs)
        text = " ".join(s.text.strip() for s in segs)
        return text, (float(np.exp(np.mean([s.avg_logprob for s in segs]))) if segs else 0.0)

    def emit(self, s, e):
        a, b = max(0.0, s * FSEC - args.pad), min(self.n * FSEC, (e + 1) * FSEC + args.pad)
        if b - a < args.min_win:
            return
        if self.dry:
            self.windows.append((a, b)); return
        with self.lock:
            self.recent = [t for t in self.recent if a - t < 60.0]
            if len(self.recent) >= args.max_per_min:
                self.skipped += 1; return
            self.recent.append(a)
        arr = self.get(a, b)
        t0 = time.time()
        text, conf = self.transcribe(arr, 0.0)
        hit = bool(KEY.search(text))
        if (not hit) and args.retry_mode != "off" and (args.retry_mode == "all" or RETRY_IF.search(text)):
            if self.rq is not None:                                   # 별도 작업 줄로 보냄 (이 줄은 막히지 않음)
                try:
                    self.rq.put_nowait((arr.copy(), a, b))
                    with self.lock:
                        self.tried += 1
                except queue.Full:
                    with self.lock:
                        self.retry_dropped += 1
            else:                                                     # 같은 줄에서 처리 (파일 시험)
                with self.lock:
                    self.tried += 1
                t2, c2 = self.transcribe(arr, args.retry_temp, prompt=(args.retry_prompt or None), model=self.retry_model)
                if KEY.search(t2):
                    text, conf, hit = t2, c2, True
                    with self.lock:
                        self.retried += 1
        dt = time.time() - t0
        with self.lock:
            self.times.append(dt)
            n_done = len(self.times)
            if self.wf is not None:
                self.wf.write(json.dumps({"a": round(a, 2), "b": round(b, 2), "sec": round(dt, 2), "text": text, "hit": hit}, ensure_ascii=False) + "\n")
        if n_done % 25 == 0:
            tc = temp_c()
            self.t_max = max(v for v in (self.t_max, tc) if v is not None) if tc is not None else self.t_max
            print(f"  {n_done}개 구간 처리 (오디오 {self.n * FSEC / 60:.0f}분 지점, 이벤트 {self.n_ev}개)", flush=True)
        if hit:
            self.handle_hit(a, b, conf, text, via_retry=False)

    def retry_loop(self):
        while not (self.worker_stop.is_set() and self.rq.empty()):
            try:
                arr, a, b = self.rq.get(timeout=0.5)
            except queue.Empty:
                continue
            t0 = time.time()
            try:
                text, conf = self.transcribe(arr, args.retry_temp, prompt=(args.retry_prompt or None), model=self.retry_model)
            except Exception as ex:                                   # 한 번의 실패로 작업 줄이 죽지 않게
                print("재시도 인식 오류:", repr(ex)[:80], flush=True)
                continue
            with self.lock:
                self.retry_times.append(time.time() - t0)
            if KEY.search(text):
                with self.lock:
                    self.retried += 1
                self.handle_hit(a, b, conf, text, via_retry=True)

    def handle_hit(self, a, b, conf, text, via_retry):
        with self.lock:
            if any(abs(a - t) < args.same_gap for t in self.counted):   # 같은 발화의 중복
                return
            repeat = sum(1 for t in self.counted if a - args.repeat_within <= t < a)
            self.counted = (self.counted + [a])[-200:]
            self.n_ev += 1
            incident = f"evt_{self.t0.strftime('%Y%m%d_%H%M%S')}_{self.n_ev:03d}"
            latency = (time.time() - (self.base_wall + b / max(args.speed, 1e-6))) if self.base_wall is not None else None
            if latency is not None:
                self.latencies.append(latency)
            ev = {"incident_id": incident, "source": "audio", "event_type": "help_request", "confidence": round(conf, 2),
                  "occurred_at": (self.t0 + timedelta(seconds=a / max(args.speed, 1e-6))).isoformat(timespec="milliseconds"),
                  "detected_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "device_id": args.device_id, "model": f"whisper-{args.retry_model if via_retry else args.model}",
                  "clip_file": f"{incident}.wav", "repeat_in_30s": repeat, "text": text[:60], "audio_sec": round(a, 1),
                  "latency_sec": None if latency is None else round(latency, 2), "via": "retry" if via_retry else "first"}
            self.ef.write(json.dumps(ev, ensure_ascii=False) + "\n"); self.ef.flush()
            self.pending.append((incident, a))
            payload = {k: v for k, v in ev.items() if k not in ("text", "audio_sec", "latency_sec", "via")}
        if self.mq is not None:
            try:
                self.mq.publish(args.mqtt_topic, json.dumps(payload, ensure_ascii=False), qos=1)
            except Exception as ex:
                print("MQTT 전송 오류:", repr(ex)[:80], flush=True)

    def save_clips(self, final=False):
        with self.lock:
            ready = [(i, a) for i, a in self.pending if final or self.n * FSEC >= a + 10]   # 뒤 10초가 다 모였을 때
            self.pending = [(i, a) for i, a in self.pending if (i, a) not in ready]
        for incident, a in ready:
            try:
                if shutil.disk_usage(self.clips).free / 1e9 < args.min_free_gb:
                    self.clips_skipped += 1; continue
                x = self.get(max(0.0, a - 10), a + 10)
                with wave.open(str(self.clips / f"{incident}.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                    w.writeframes(np.clip(x * 32768, -32768, 32767).astype(np.int16).tobytes())
                files = sorted(self.clips.glob("*.wav"), key=lambda p: p.stat().st_mtime)
                for old in files[:max(0, len(files) - args.max_clips)]:
                    old.unlink()
            except Exception as ex:
                print("클립 저장 오류:", repr(ex)[:80], flush=True)

    def status(self):
        with self.lock:
            lat = list(self.latencies)
            d = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "uptime_sec": round(time.time() - self.started),
                 "audio_min": round(self.n * FSEC / 60, 1), "windows": len(self.times), "events": self.n_ev,
                 "retry_tried": self.tried, "retry_done": len(self.retry_times), "retry_hits": self.retried,
                 "retry_dropped": self.retry_dropped, "retry_queue": self.rq.qsize() if self.rq is not None else 0,
                 "skipped_by_limit": self.skipped, "clips_skipped": self.clips_skipped}
        d.update({"rss_mb": rss_mb(), "temp_c": temp_c(), "free_gb": round(shutil.disk_usage(self.clips).free / 1e9, 1),
                  "latency_p50": round(pct(lat, 50), 2) if lat else None, "latency_max": round(max(lat), 2) if lat else None})
        return d

    def write_status(self):
        tmp = args.status_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.status(), f, ensure_ascii=False)
        os.replace(tmp, args.status_file)

    def status_loop(self):
        while not STOP.wait(30):
            try:
                self.write_status()
            except Exception:
                pass

    def close(self):
        self.flush()
        if self.worker is not None:                                    # 대기 중인 재시도는 최대 2분만 기다림
            deadline = time.time() + 120
            while not self.rq.empty() and time.time() < deadline:
                time.sleep(0.5)
            self.worker_stop.set()
            self.worker.join(timeout=60)
            self.save_clips(final=True)
        if args.status_file:
            try:
                self.write_status()
            except Exception:
                pass
        if self.wf is not None:
            self.wf.close()
        self.ef.close()
        if self.mq is not None:
            self.mq.loop_stop(); self.mq.disconnect()


def summary(eng, dur, name):
    tc = temp_c()
    mmss = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d}"
    ts = eng.times
    print(f"\n=== 요약 ({name}) ===")
    print(f"처리한 구간 {len(ts)}개 (건너뜀 {eng.skipped}개), 구간당 평균 {np.mean(ts) if ts else 0:.2f}초 / 최대 {max(ts) if ts else 0:.2f}초, "
          f"총 {sum(ts) / 60:.1f}분 (오디오의 {sum(ts) / max(dur, 1):.1%})")
    print(f"위급 단어 인식 {eng.n_ev}회" + (f" | 재시도 {eng.tried}번 시도, {eng.retried}개 건짐" if args.retry_mode != "off" else "")
          + (f", 대기 줄이 가득 차 건너뜀 {eng.retry_dropped}개" if eng.retry_dropped else "")
          + (f" ({'별도 작업 줄' if eng.rq is not None else '같은 줄'})" if args.retry_mode != "off" else ""))
    if eng.retry_times:
        print(f"재시도 처리: 평균 {np.mean(eng.retry_times):.1f}초 / 최대 {max(eng.retry_times):.1f}초")
    if eng.latencies:
        print(f"이벤트 지연(구간 끝 → 이벤트, 실시간 시험): 중앙 {pct(eng.latencies, 50):.1f}초, 95% {pct(eng.latencies, 95):.1f}초, 최대 {max(eng.latencies):.1f}초")
    if eng.clips_skipped:
        print(f"디스크 여유 부족으로 저장하지 못한 클립 {eng.clips_skipped}개")
    print(f"온도: 최고 {eng.t_max} -> 끝 {tc} | 성능제한: {thr_text(sh(['vcgencmd', 'get_throttled']))} | 메모리 {rss_mb()}MB")
    for line in open(str(eng.out) + "_events.jsonl", encoding="utf-8"):
        e = json.loads(line)
        lat = "" if e.get("latency_sec") is None else f" 지연 {e['latency_sec']}초"
        print(f"  {mmss(e['audio_sec'])} 직전 30초 안에 {e['repeat_in_30s']}회{lat} ({e.get('via', '')}) | {e['text'][:30]}")


def blocks_of(x):
    for i in range(0, len(x) - FR + 1, FR):
        yield x[i:i + FR]


def feed(eng, x, loops, realtime):
    base = time.time()
    if realtime:
        eng.start_clock(base)
    k = 0
    for _ in range(max(1, loops)):
        for blk in blocks_of(x):
            if STOP.is_set():
                return
            if realtime:                                   # 마이크처럼 0.5초 분량이 다 녹음된 뒤에 도착. 처리가 밀리면 쌓였다가 한꺼번에 들어옴
                delay = base + (k + 1) * FSEC / max(args.speed, 1e-6) - time.time()
                if delay > 0:
                    time.sleep(delay)
            eng.push(blk)
            k += 1


if args.list_devices:
    import sounddevice as sd
    print(sd.query_devices())
elif args.wav:
    x = np.asarray(decode_audio(args.wav, sampling_rate=SR), dtype=np.float32)
    dur = len(x) / SR * max(1, args.loop)
    if args.dry_run:
        print(f"오디오 {dur / 3600:.2f}시간 | 기준 음량(dBFS)별 Whisper로 보낼 구간 수")
        for g in (-32, -30, -28, -25, -22, -20):
            e = Engine(g, dry=True)
            for blk in blocks_of(x):
                e.push(blk)
            e.flush()
            print(f"  {g}dBFS 이상: {len(e.windows)}개 (시간당 {len(e.windows) / (dur / 3600):.0f}개)")
    else:
        eng = Engine(args.gate_db, out=args.out)
        print(f"오디오 {dur / 60:.1f}분 ({dur / 3600:.2f}시간) | 기준 {args.gate_db}dBFS | 모델 {args.model}, 스레드 {args.threads}, "
              f"재시도 {args.retry_mode}({'별도 작업 줄' if eng.rq is not None else '같은 줄'}), MQTT {'켬' if eng.mq else '끔'}, "
              f"{'실시간 속도' if args.realtime else '빠르게'}", flush=True)
        feed(eng, x, args.loop, args.realtime)
        eng.close()
        summary(eng, dur, Path(args.wav).name)
elif args.mic:
    import sounddevice as sd
    eng = Engine(args.gate_db, out=args.out)
    eng.start_clock()
    print(f"마이크 시작 (기준 {args.gate_db}dBFS, 모델 {args.model}, 스레드 {args.threads}, 재시도 {args.retry_mode}, "
          f"MQTT {'켬' if eng.mq else '끔'}). 끝내려면 Ctrl+C 또는 SIGTERM", flush=True)
    t_end = time.time() + args.max_minutes * 60 if args.max_minutes else None
    dev = int(args.device) if args.device and str(args.device).isdigit() else args.device
    acc = np.zeros(0, np.float32)
    q = queue.Queue()

    def cb(indata, frames, t, status):
        q.put(indata[:, min(args.mic_channel, indata.shape[1] - 1)].copy())

    while not STOP.is_set() and (t_end is None or time.time() < t_end):
        try:
            with sd.InputStream(samplerate=SR, channels=args.channels, dtype="float32", blocksize=FR, device=dev, callback=cb):
                while not STOP.is_set() and (t_end is None or time.time() < t_end):
                    try:
                        acc = np.concatenate([acc, q.get(timeout=1.0)])
                    except queue.Empty:
                        continue
                    while len(acc) >= FR:
                        eng.push(acc[:FR]); acc = acc[FR:]
        except Exception as ex:                            # 마이크가 끊기면 5초 뒤 다시 연결
            print("마이크 오류, 5초 뒤 재연결:", repr(ex)[:100], flush=True)
            STOP.wait(5)
    eng.close()
    summary(eng, eng.n * FSEC, "마이크")
else:
    ap.print_help()
