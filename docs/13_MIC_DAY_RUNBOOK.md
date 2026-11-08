# 13. 마이크(reSpeaker) 도착 당일 런북과 재평가 계획

> 마이크가 도착하면 이 순서대로 합니다. **이 문서의 절차는 Seeed 위키(V2 문서)와 코드를 따라 정리한 것이고, 실제 장비에서는 아직 실행하지 못했습니다** (`(미시험)`). 도착 후 실제 결과로 이 문서를 고칩니다.
> 위키: [Getting Started with Raspberry Pi (V2)](https://wiki.seeedstudio.com/respeaker_2_mics_pi_hat_raspberry_v2/) (2026-01-29 갱신본을 2026-11-08에 확인). V1과 V2는 드라이버가 다르니 [하드웨어 버전 구분 문서](https://wiki.seeedstudio.com/how-to-distinguish-respeaker_2-mics_pi_hat-hardware-revisions/)로 **V2.0이 맞는지 먼저 확인**합니다.

## 1. 장착 전 (파이 전원을 켠 상태에서, 5분)

1. 파이에서 `bash mic_precheck.sh` ([code/voice_pi/](../code/voice_pi/README.md)). 출력에 IP·계정은 나오지 않으니 그대로 공유해도 됩니다. 확인할 것: OS/커널, `/boot/firmware/config.txt` 위치, 도구(git, make, dtc), 녹음 장치(HDMI만 보여야 정상), 시간 동기화, 파이썬 패키지.
2. 없는 것은 미리 설치: `sudo apt install -y git make device-tree-compiler i2c-tools libportaudio2`, 가상환경에 `pip install sounddevice paho-mqtt`.
3. 방열판이나 팬을 붙이는 것을 권합니다 (방열판 없는 파이4에서 온도 제한이 걸렸음, [11 §6-4](11_VOICE_ASR_TRACK.md)).

## 2. 장착과 드라이버 (미시험)

1. **파이 전원을 끄고** HAT을 40핀에 끼웁니다 (핀 정렬 확인). 전원을 켭니다.
2. `bash install_respeaker_v2.sh --dry-run`으로 할 일을 먼저 보고, 문제가 없으면 `bash install_respeaker_v2.sh`. (위키 절차: 오버레이 소스를 받아 컴파일 → `/boot/firmware/overlays/respeaker-2mic-v2_0.dtbo`로 복사 → `config.txt`에 `dtoverlay=respeaker-2mic-v2_0` 추가. 스크립트는 `config.txt`를 먼저 백업합니다.)
3. `sudo reboot` 후 `arecord -l`에 `seeed2micvoicec` 카드가 보이면 성공입니다. **카드 번호(`card N`)를 기록**합니다 (환경마다 다름). `dmesg | grep -i tlv320`, `lsmod | grep tlv320`로 드라이버 로그도 확인.
4. 안 보일 때: ① `ls /boot/firmware/overlays | grep respeaker` ② `grep respeaker /boot/firmware/config.txt` ③ `sudo i2cdetect -y 1`에서 `18`(코덱 주소 0x18)이 보이는지 ④ HAT이 V2.0이 맞는지 ⑤ 핀이 제대로 꽂혔는지. 막히면 Seeed 포럼에 위 결과와 OS 버전을 붙여 문의.
5. **파이4에서 이 오버레이가 동작하는지는 위키가 명시하지 않았습니다** (위키 예시 출력은 파이5 방식의 장치 이름). 안 되면 [03](03_DATA.md) 같은 문서가 아니라 이 문서에 결과를 기록하고 대안(USB 마이크)을 검토합니다.

## 3. 소리 점검 (30분)

1. `alsamixer` → F6으로 `seeed-2mic-voicecard` 선택 → Capture 이득 조정 (너무 작거나 찌그러지지 않게).
2. 5초 시험 녹음: `arecord -D plughw:<카드번호>,0 -f S16_LE -r 16000 -c 2 -d 5 test.wav` → PC로 복사해서 듣고 `check_recordings.py`로 찌그러짐·잡음 확인. 두 채널 중 어느 쪽이 큰지 기록 (`--mic-channel`에 사용).

## 4. 기준 음량 보정 (15분)

1. 조용한 방 20초와 1m 거리 "도와주세요" 15번(5초 간격) 90초를 위 방식으로 녹음 (`quiet.wav`, `speech.wav`).
2. `python3 calibrate_mic.py --quiet quiet.wav --speech speech.wav --channel 0` → 추천 `--gate-db`와 경고(너무 작음, 찌그러짐, 채널 차이)를 확인. 기준은 "발화 하위 10% 크기 - 3dB"이고 잡음 95% + 8dB 이상이어야 합니다 (S23 녹음에서 -28이 나온 것과 같은 방식).
3. 값을 `voice_service.json`의 `gate_db`에 적습니다.

## 5. 서비스 실행

1. 포그라운드로 먼저: `python3 voice_service.py --config voice_service.json` (설정 파일 예시: `voice_service.example.json`, 비밀번호는 환경변수 `MQTT_PASSWORD`). "도와주세요"라고 말해서 이벤트(`*_events.jsonl`), 클립(`*_clips/`), MQTT(구독 창)가 나오는지 확인.
2. 이상이 없으면 systemd로 전환 (`voice_service.service`의 설치 방법이 파일 첫머리 주석에 있음): 부팅 때 자동 시작과 죽으면 재시작.

## 6. 시험 순서와 합격 기준 (제안, 팀 합의 필요)

| # | 시험 | 방법 | 기준 (제안) |
|---|---|---|---|
| S1 | 1m "도와주세요" | 5초 간격 17번 | 15/17 이상 (파이4·휴대폰 녹음에서는 34/34) |
| S2 | 3m "도와주세요" | 5초 간격 17번 | 기록 (참고: 12/17 이상) |
| S3 | 옆방 (문 열림/닫힘) | 각 10번 | 기록 |
| S4 | 새 문장 3종 | "살려주세요", "사람 살려", "119 불러주세요" 각 10~13번, 목소리를 바꿔서 | 기록 (이전 값: 79%) |
| S5 | 일상 소리 3시간 | 말소리(대화·통화·TV) 1시간 이상, 위급한 말 없이 | 오경보 1회 이하 (하루 허용 횟수는 팀 합의 필요) |
| S6 | TV 20분 | 뉴스·드라마(위급 대사 장면 포함) | 오경보 횟수 기록, 반복 규칙(30초 2회 → 긴급)에 걸리는지 |
| S7 | 반복 호출 | 5초 간격 6번 | 이벤트 지연: `detected_at - occurred_at` (이벤트 기록에서 계산) |
| S8 | 24시간 연속 | systemd로 실행 | 온도, 메모리(`status.json`의 `rss_mb`), 디스크, 와이파이 재접속, 크래시 없음 |

- **보정용과 최종 시험용을 나눕니다** ([02 D28](02_DECISIONS.md)). 게이트와 재시도 조건을 정하는 데 쓴 녹음(S1, 4번의 보정 녹음)은 최종 시험에 쓰지 않고, 최종 시험(S5, S6)은 **다른 날** 새로 녹음합니다.
- 다른 사람의 목소리를 녹음할 때는 [14 동의서 양식](14_RECORDING_CONSENT_TEMPLATE.md)을 받습니다. 위급 연기에서 **실제로 넘어지지 않습니다**.

## 7. 도착 후 기록할 것

- [ ] 카드 번호, 채널 음량 차이, 추천 `--gate-db`, 설치 중 막힌 점 → [05 §5](05_RASPBERRY_PI.md)와 이 문서
- [ ] S1~S8 결과 표 → [11](11_VOICE_ASR_TRACK.md)에 "reSpeaker 실측" 절 추가, [EXPERIMENT_LOG](EXPERIMENT_LOG.md)에 행 추가
- [ ] 협약안의 반복 규칙 횟수·시간 조정 ([12 §5](12_TEAM_AGREEMENT_V05.md)), 오경보 허용 수준 합의
- [ ] 파이5 도착 후 같은 시험(S1, S5, S7, S8)을 영상과 함께 반복

## 8. 자주 막히는 것

| 증상 | 확인 |
|---|---|
| `arecord -l`에 카드가 없음 | §2의 4번 (오버레이 파일, config 줄, I2C 0x18, 버전 V2.0) |
| 소리가 너무 작거나 찌그러짐 | `alsamixer`(F6으로 카드 선택)의 Capture 이득, `calibrate_mic.py` 경고 |
| 서비스가 마이크를 못 엶 | `--device`에 `arecord -l`의 카드 번호나 이름, `python3 voice_service.py --list-devices`로 번호 확인, 다른 프로그램이 마이크를 쓰는지 |
| 카드 번호가 재부팅마다 바뀜 | 카드 이름(`seeed2micvoicec`)으로 지정 |
| 서비스가 자꾸 재시작 | `journalctl -u voice_service -n 50`, 비밀번호 환경 파일(`/etc/voice_service.env`) 확인 |
| 이벤트는 나오는데 MQTT가 안 감 | 브로커 상태(`systemctl status mosquitto`), 계정·비밀번호, `--mqtt-host` |
