# code/voice_pi/ — 파이용 음성(단어 인식) 서비스와 시험 도구 (11/08)

설명과 결과는 [../../docs/11_VOICE_ASR_TRACK.md](../../docs/11_VOICE_ASR_TRACK.md)를 보세요.
파이 주소, 계정, MQTT 비밀번호는 코드에 넣지 않습니다 (옵션과 환경변수로 받음).

| 파일 | 어디서 | 하는 일 |
|---|---|---|
| `voice_service.py` | 라즈베리파이 | 소리 크기 기준 이상 구간을 Whisper tiny(int8)로 글자 인식 → 위급 단어 규칙 → 이벤트(JSON 줄, MQTT, 앞뒤 10초 클립). 파일을 소리처럼 흘려 보내는 시험 모드와 마이크 모드(`--mic`, **아직 시험하지 못함**) |
| `bench_asr_pi.py` | 라즈베리파이 | 짧은 wav 구간 24개로 Whisper 크기별 처리 시간, 온도, 성능 제한을 잼 (마이크 불필요) |
| `rules_eval.py` | 라즈베리파이 | 이미 받아 적은 글자 파일(`*_windows.jsonl`)에 규칙 보강안을 적용해 재현율과 오경보를 계산 |

## 설치 (파이4, 64비트 OS, Python 3.13에서 확인)

```
python3 -m venv ~/asr && source ~/asr/bin/activate
pip install faster-whisper numpy            # MQTT: paho-mqtt, 마이크: sounddevice 도 필요
```

- 처음 실행할 때 모델(tiny, base, small)을 내려받으므로 인터넷이 필요합니다.
- 새 접속 창에서는 `source ~/asr/bin/activate`를 다시 해야 합니다 (프롬프트 앞에 `(asr)`가 보여야 함).
- faster-whisper 1.2.1과 PyAV 19가 맞지 않아 m4a를 읽을 때 `metadata_errors` 오류가 났습니다. `voice_service.py`는 이 경우 PyAV로 직접 읽도록 되어 있습니다 ([09 #28](../../docs/09_CORRECTIONS_AND_LESSONS.md)).

## 자주 쓰는 명령

```
python3 voice_service.py --wav <파일> --out <결과 이름>          # 확정한 기본 설정으로 실행
python3 voice_service.py --wav <파일> --dry-run                  # 기준 음량별 구간 수 (인식 안 함)
python3 voice_service.py --wav <파일> --mqtt-host <주소> --mqtt-user <계정>   # 비밀번호는 환경변수 MQTT_PASSWORD
python3 bench_asr_pi.py <구간 폴더> --sizes tiny,base --threads 2
```

- 결과 `*_windows.jsonl`에는 **인식한 글자(개인 대화 포함)** 가 들어 있으니 저장소에 올리지 않습니다 (`.gitignore`가 막음).
- `--prompt`(미리 알려 주는 단어)는 쓰지 마세요. 응급 녹음 재현율은 올랐지만 3시간 일상 소리에서 오경보가 48번 났습니다 ([11 §6](../../docs/11_VOICE_ASR_TRACK.md)).
