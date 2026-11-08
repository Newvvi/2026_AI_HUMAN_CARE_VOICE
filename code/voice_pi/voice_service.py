"""
voice_service.py v0.2: 파이용 음성 위급 단어 감지 (파일 시뮬레이션 + 마이크 + MQTT)

확정한 기본 설정 (2026-11-08, 파이4 실측, docs/11 참고):
  소리 크기 -28dBFS 이상 구간만 Whisper tiny(int8, 탐색 폭 5)로 글자 인식 -> 위급 단어 규칙(KEY)
  첫 인식에 "주세/와줘/도와/살려/구해/119/일일구" 조각이 있지만 단어는 없을 때만 small로 한 번 더 인식 (미리 알려 주는 단어는 쓰지 않음)
  같은 말이 3초 안에 반복되면 하나로 치고, 직전 30초 안의 횟수를 repeat_in_30s로 함께 보냄
흐름: 구간 -> 글자 -> 규칙 -> 이벤트(help_request)를 JSON 줄로 기록, MQTT 전송(선택), 앞뒤 10초 클립 저장

사용법 (파이, 가상환경을 켠 상태, pip install faster-whisper numpy [paho-mqtt sounddevice]):
  python3 voice_service.py --wav <파일> --out <결과 이름>      # 파일을 소리처럼 흘려 보내 시험
  python3 voice_service.py --wav <파일> --dry-run              # 기준 음량별 구간 수만 계산
  python3 voice_service.py --list-devices                      # 마이크 목록 (reSpeaker 도착 후)
  python3 voice_service.py --mic --device <번호> --channels 2 --out <결과 이름>   # 실제 마이크 (아직 시험하지 못함)
  MQTT: --mqtt-host <주소> --mqtt-user <계정>  (비밀번호는 환경변수 MQTT_PASSWORD, 저장소에 쓰지 않음)
결과: <out>_events.jsonl, <out>_windows.jsonl (인식한 글자: 개인 대화가 들어 있으니 올리지 않음), <out>_clips/
알려진 한계: 재시도(small, 약 20초)가 구간 처리를 막는다. 실제 서비스에서는 별도 작업 줄로 분리해야 한다 (docs/11 미해결).
"""
import argparse
import json
import math
import os
import queue
import re
import subprocess
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

ap = argparse.ArgumentParser()
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
ap.add_argument("--prompt", default="", help="Whisper에 미리 알려 줄 단어 (예: 도와주세요. 살려주세요.)")
ap.add_argument("--norm-peak", type=float, default=0.0, help="Whisper에 보내기 전 구간의 최대 크기를 이 값으로 키움 (0이면 안 함, 예: 0.9)")
ap.add_argument("--norm-max-gain-db", type=float, default=24.0, help="키우는 한도(dB)")
ap.add_argument("--retry-mode", choices=["all", "cond", "off"], default="cond",
                help="위급 단어가 없을 때 온도 0.4로 한 번 더 인식: all=모든 구간, cond=비슷한 조각이 있을 때만, off=안 함")
ap.add_argument("--retry", action="store_true", help="(예전 옵션, 무시됨)")
ap.add_argument("--retry-all", action="store_true", help="(예전 옵션, 무시됨)")
ap.add_argument("--retry-temp", type=float, default=0.0, help="재시도 온도 (0이면 무작위 없이 같은 결과)")
ap.add_argument("--retry-prompt", default="", help="재시도할 때만 미리 알려 줄 단어")
ap.add_argument("--retry-model", default="small", help="재시도에 쓸 더 큰 모델 (비워 두면 같은 모델)")
ap.add_argument("--retry-if", default=r"주세|와줘|도와|살려|구해|119|일일구")
ap.add_argument("--device-id", default="pi-voice")
ap.add_argument("--mqtt-host")
ap.add_argument("--mqtt-port", type=int, default=1883)
ap.add_argument("--mqtt-topic", default="home/alert/audio")
ap.add_argument("--mqtt-user")
ap.add_argument("--dry-run", action="store_true", help="구간 개수만 세고 끝냄 (--wav 필요)")
args = ap.parse_args()
RETRY_IF = re.compile(args.retry_if)


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def temp_c():
    m = re.search(r"([\d.]+)", sh(["vcgencmd", "measure_temp"]))
    return float(m.group(1)) if m else None


def thr_text(raw):
    try:
        v = int(raw.split("=")[-1], 16)
    except ValueError:
        return raw or "?"
    now = [n for b, n in ((1, "저전압"), (2, "클럭 제한"), (4, "스로틀링"), (8, "온도 소프트제한")) if v & b]
    past = [n for b, n in ((0x10000, "저전압"), (0x20000, "클럭 제한"), (0x40000, "스로틀링"), (0x80000, "온도 소프트제한")) if v & b]
    return f"{raw} (지금: {', '.join(now) or '없음'} / 부팅 후 발생: {', '.join(past) or '없음'})"


class Engine:
    def __init__(self, gate_db, t0_wall, out=None, dry=False):
        self.gate, self.t0, self.dry = gate_db, t0_wall, dry
        self.n = 0                          # 지금까지 받은 프레임 수
        self.frames, self.start, self.last = {}, None, None
        self.core = args.max_win - 2 * args.pad
        self.windows = []                   # dry-run용
        self.pending, self.recent = [], []
        self.times, self.skipped, self.tried, self.retried = [], 0, 0, 0
        self.last_counted, self.n_ev, self.hits = None, 0, []
        self.t_max, self.mq = temp_c(), None
        if not dry:
            self.out = Path(out)
            self.clips = Path(str(self.out) + "_clips"); self.clips.mkdir(parents=True, exist_ok=True)
            self.wf = open(str(self.out) + "_windows.jsonl", "w", encoding="utf-8")
            self.ef = open(str(self.out) + "_events.jsonl", "w", encoding="utf-8")
            self.model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
            self.retry_model = (WhisperModel(args.retry_model, device="cpu", compute_type="int8", cpu_threads=args.threads)
                                if (args.retry_mode != "off" and args.retry_model and args.retry_model != args.model) else None)
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
        self.save_clips()

    def flush(self):
        if self.start is not None:
            self.emit(self.start, self.last)
            self.start = None
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
        segs, _ = (model or self.model).transcribe(arr, language="ko", beam_size=args.beam, initial_prompt=(prompt or args.prompt or None), vad_filter=False, condition_on_previous_text=False,
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
        self.recent = [t for t in self.recent if a - t < 60.0]
        if len(self.recent) >= args.max_per_min:
            self.skipped += 1; return
        self.recent.append(a)
        arr = self.get(a, b)
        t0 = time.time()
        text, conf = self.transcribe(arr, 0.0)
        if args.retry_mode != "off" and not KEY.search(text) and (args.retry_mode == "all" or RETRY_IF.search(text)):
            self.tried += 1
            t2, c2 = self.transcribe(arr, args.retry_temp, prompt=(args.retry_prompt or None), model=self.retry_model)
            if KEY.search(t2):
                text, conf, self.retried = t2, c2, self.retried + 1
        dt = time.time() - t0
        self.times.append(dt)
        hit = bool(KEY.search(text))
        self.wf.write(json.dumps({"a": round(a, 2), "b": round(b, 2), "sec": round(dt, 2), "text": text, "hit": hit}, ensure_ascii=False) + "\n")
        if len(self.times) % 25 == 0:
            tc = temp_c()
            self.t_max = max(v for v in (self.t_max, tc) if v is not None) if tc is not None else self.t_max
            print(f"  {len(self.times)}개 구간 처리 (오디오 {self.n * FSEC / 60:.0f}분 지점, 이벤트 {self.n_ev}개)", flush=True)
        if hit and not (self.last_counted is not None and a - self.last_counted < args.same_gap):
            self.hits = [t for t in self.hits if a - t <= args.repeat_within]
            repeat = len(self.hits)                                 # 직전 30초 안에 이미 인식된 횟수
            self.hits.append(a)
            self.last_counted = a
            self.n_ev += 1
            incident = f"evt_{self.t0.strftime('%Y%m%d_%H%M%S')}_{self.n_ev:03d}"
            ev = {"incident_id": incident, "source": "audio", "event_type": "help_request", "confidence": round(conf, 2),
                  "occurred_at": (self.t0 + timedelta(seconds=a)).isoformat(timespec="milliseconds"),
                  "detected_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "device_id": args.device_id, "model": f"whisper-{args.model}", "clip_file": f"{incident}.wav",
                  "repeat_in_30s": repeat, "text": text, "audio_sec": round(a, 1)}
            self.ef.write(json.dumps(ev, ensure_ascii=False) + "\n"); self.ef.flush()
            self.pending.append((incident, a))
            if self.mq is not None:
                self.mq.publish(args.mqtt_topic, json.dumps({k: v for k, v in ev.items() if k not in ("text", "audio_sec")}, ensure_ascii=False), qos=1)

    def save_clips(self, final=False):
        keep = []
        for incident, a in self.pending:
            if final or self.n * FSEC >= a + 10:                    # 뒤 10초가 다 모였을 때 저장
                x = self.get(max(0.0, a - 10), a + 10)
                with wave.open(str(self.clips / f"{incident}.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                    w.writeframes(np.clip(x * 32768, -32768, 32767).astype(np.int16).tobytes())
            else:
                keep.append((incident, a))
        self.pending = keep

    def close(self):
        self.flush()
        self.wf.close(); self.ef.close()
        if self.mq is not None:
            self.mq.loop_stop(); self.mq.disconnect()


def summary(eng, dur, name):
    tc = temp_c()
    mmss = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d}"
    ts = eng.times
    print(f"\n=== 요약 ({name}) ===")
    print(f"처리한 구간 {len(ts)}개 (건너뜀 {eng.skipped}개), 구간당 평균 {np.mean(ts) if ts else 0:.2f}초 / 최대 {max(ts) if ts else 0:.2f}초, "
          f"총 {sum(ts) / 60:.1f}분 (오디오의 {sum(ts) / max(dur, 1):.1%})")
    print(f"위급 단어 인식 {eng.n_ev}회" + (f" (재시도 {eng.tried}번 중 {eng.retried}개 건짐)" if args.retry_mode != "off" else ""))
    print(f"온도: 최고 {eng.t_max} -> 끝 {tc} | 성능제한: {thr_text(sh(['vcgencmd', 'get_throttled']))}")
    for line in open(str(eng.out) + "_events.jsonl", encoding="utf-8"):
        e = json.loads(line)
        print(f"  {mmss(e['audio_sec'])} 직전 30초 안에 {e['repeat_in_30s']}회 | {e['text'][:30]}")


def blocks_of(x):
    for i in range(0, len(x) - FR + 1, FR):
        yield x[i:i + FR]


if args.list_devices:
    import sounddevice as sd
    print(sd.query_devices())
elif args.wav:
    x = np.asarray(decode_audio(args.wav, sampling_rate=SR), dtype=np.float32)
    dur = len(x) / SR
    if args.dry_run:
        print(f"오디오 {dur / 3600:.2f}시간 | 기준 음량(dBFS)별 Whisper로 보낼 구간 수")
        for g in (-32, -30, -28, -25, -22, -20):
            e = Engine(g, datetime.now().astimezone(), dry=True)
            for blk in blocks_of(x):
                e.push(blk)
            e.flush()
            print(f"  {g}dBFS 이상: {len(e.windows)}개 (시간당 {len(e.windows) / (dur / 3600):.0f}개)")
    else:
        eng = Engine(args.gate_db, datetime.now().astimezone(), out=args.out)
        print(f"오디오 {dur / 60:.1f}분 ({dur / 3600:.2f}시간) | 기준 {args.gate_db}dBFS | 모델 {args.model}, 스레드 {args.threads}, "
              f"재시도 {args.retry_mode}, MQTT {'켬' if eng.mq else '끔'}", flush=True)
        for blk in blocks_of(x):
            eng.push(blk)
        eng.close()
        summary(eng, dur, Path(args.wav).name)
elif args.mic:
    import sounddevice as sd
    q = queue.Queue()
    acc = np.zeros(0, np.float32)

    def cb(indata, frames, t, status):
        q.put(indata[:, min(args.mic_channel, indata.shape[1] - 1)].copy())

    eng = Engine(args.gate_db, datetime.now().astimezone(), out=args.out)
    print(f"마이크 시작 (기준 {args.gate_db}dBFS, 모델 {args.model}, 스레드 {args.threads}, MQTT {'켬' if eng.mq else '끔'}). 끝내려면 Ctrl+C", flush=True)
    t_end = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        with sd.InputStream(samplerate=SR, channels=args.channels, dtype="float32", blocksize=FR,
                            device=(int(args.device) if args.device and args.device.isdigit() else args.device), callback=cb):
            while t_end is None or time.time() < t_end:
                try:
                    acc = np.concatenate([acc, q.get(timeout=1.0)])
                except queue.Empty:
                    continue
                while len(acc) >= FR:
                    eng.push(acc[:FR]); acc = acc[FR:]
    except KeyboardInterrupt:
        pass
    eng.close()
    summary(eng, eng.n * FSEC, "마이크")
else:
    ap.print_help()
