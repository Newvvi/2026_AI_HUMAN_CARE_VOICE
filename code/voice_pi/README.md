# code/voice_pi/ — 파이용 음성(단어 인식) 서비스와 시험 도구 (11/08)

설명과 결과는 [../../docs/11_VOICE_ASR_TRACK.md](../../docs/11_VOICE_ASR_TRACK.md)를 보세요.
파이 주소, 계정, MQTT 비밀번호는 코드에 넣지 않습니다 (옵션과 환경변수로 받음).

| 파일 | 어디서 | 하는 일 |
|---|---|---|
| `voice_service.py` | 라즈베리파이 | 소리 크기 기준 이상 구간을 Whisper tiny(int8)로 글자 인식 → 위급 단어 규칙 → 이벤트(JSON 줄, MQTT, 앞뒤 10초 클립). 파일을 소리처럼 흘려 보내는 시험 모드와 마이크 모드(`--mic`, **아직 시험하지 못함**) |
| `bench_asr_pi.py` | 라즈베리파이 | 짧은 wav 구간 24개로 Whisper 크기별 처리 시간, 온도, 성능 제한을 잼 (마이크 불필요) |
| `rules_eval.py` | 라즈베리파이 | 이미 받아 적은 글자 파일(`*_windows.jsonl`)에 규칙 보강안을 적용해 재현율과 오경보를 계산 |
| `make_soak_wav.py`, `check_soak.py` | 라즈베리파이 | **장시간(soak) 시험**: 연속 일상 소리에 위급 발화(카나리아)를 20분마다 끼워 넣은 파일을 만들고, 실행 후 감지 수·오경보·지연·메모리를 확인 |
| `pi5_session.sh` | 파이5 (파이4의 SD카드를 꽂았을 때) | 파이5에서 음성 쪽 실측을 자동으로 돌려 `~/pi5_report.txt`에 모음 (`--quick` 약 25분, 전체 60~75분). 계획은 [docs/15](../../docs/15_PI5_TEMP_SESSION.md), **아직 실행 전** |
| `calibrate_mic.py` | 라즈베리파이 | 마이크 도착 후 조용한 방 + "도와주세요" 녹음 두 개로 추천 `--gate-db`와 경고(너무 작음, 찌그러짐, 채널 차이) 계산 |
| `mic_precheck.sh`, `install_respeaker_v2.sh` | 라즈베리파이 | 마이크 설치 전 점검(읽기 전용), Seeed 위키 절차를 옮긴 설치(`--dry-run` 지원, **실장비 미시험**) |
| `voice_service.service`, `voice_service.example.json` | 라즈베리파이 | 부팅 자동 시작(systemd) 유닛과 설정 파일 예시 (비밀번호는 설정 파일이 아니라 `/etc/voice_service.env`의 환경변수) |

## 설치 (파이4, 64비트 OS, Python 3.13에서 확인)

```
python3 -m venv ~/asr && source ~/asr/bin/activate
pip install faster-whisper numpy            # MQTT: paho-mqtt, 마이크: sounddevice 도 필요
```

- 처음 실행할 때 모델(tiny, base, small)을 내려받으므로 인터넷이 필요합니다.
- 새 접속 창에서는 `source ~/asr/bin/activate`를 다시 해야 합니다 (프롬프트 앞에 `(asr)`가 보여야 함).
- faster-whisper 1.2.1과 PyAV 19가 맞지 않아 m4a를 읽을 때 `metadata_errors` 오류가 났습니다. `voice_service.py`는 이 경우 PyAV로 직접 읽도록 되어 있습니다 ([09 #28](../../docs/09_CORRECTIONS_AND_LESSONS.md)).

## v0.3에서 추가된 것 (11/08)

- small 재시도를 **별도 작업 줄(스레드)** 로 분리(실시간·마이크 모드): 재시도가 20초 걸려도 뒤따르는 구간 처리가 막히지 않음. 대기 줄이 가득 차면 재시도를 건너뜀(첫 인식 결과는 항상 즉시 처리)
- 클립 개수 상한(`--max-clips`)과 디스크 여유 점검(`--min-free-gb`), 설정 파일(`--config`, 비밀번호 키는 거부), 상태 파일(`--status-file`), 종료 신호(SIGTERM) 처리, 마이크 끊김 재연결
- **실시간 시뮬레이션**(`--realtime`, `--speed`, `--loop`): 파일을 실제 속도로 흘려 보내 이벤트 지연(구간 끝 → 이벤트)과 밀림을 측정
- 가짜 모델로 파일·실시간·느린 재시도·설정 파일·클립 상한·종료 신호·MQTT 항목을 시험했고, **파이 실측은 [07 K 항목](../../docs/07_ROADMAP.md) 순서로 진행**합니다.

## 자주 쓰는 명령

```
python3 voice_service.py --wav <파일> --out <결과 이름>          # 확정한 기본 설정으로 실행
python3 voice_service.py --wav <파일> --dry-run                  # 기준 음량별 구간 수 (인식 안 함)
python3 voice_service.py --wav <파일> --realtime --out <이름>    # 실제 속도로 흘려 보내 지연·밀림 시험
python3 voice_service.py --config voice_service.json              # 설정 파일로 실행 (마이크/MQTT)
python3 voice_service.py --wav <파일> --mqtt-host <주소> --mqtt-user <계정>   # 비밀번호는 환경변수 MQTT_PASSWORD
python3 bench_asr_pi.py <구간 폴더> --sizes tiny,base --threads 2
```

- 결과 `*_windows.jsonl`에는 **인식한 글자(개인 대화 포함)** 가 들어 있으니 저장소에 올리지 않습니다 (`.gitignore`가 막음).
- `--prompt`(미리 알려 주는 단어)는 쓰지 마세요. 응급 녹음 재현율은 올랐지만 3시간 일상 소리에서 오경보가 48번 났습니다 ([11 §6](../../docs/11_VOICE_ASR_TRACK.md)).
