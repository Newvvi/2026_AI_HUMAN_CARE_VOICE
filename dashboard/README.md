# 독거노인 안심 모니터

AI 휴먼케어 학생연구회 · 독거노인 위험 감지 및 알림 시스템

음성(R1)과 영상(R2)이 보내는 감지 JSON을 보호자 관점에서 한 화면에 보여 주는 노트북용 웹 대시보드다.
김우석 MQTT 통신 모듈 배포 문서 v1.1.0의 **앱 자리**(`app` 계정, 구독 전용)에 들어간다.

## 지키는 규칙
- JSON 4필드(`incident_id`, `confidence`, `occurred_at`, `clip_url`), 토픽, 계정·ACL을 바꾸지 않는다.
- `home/alert/#`, `home/status/#`를 **구독만** 한다. 파이로 아무것도 보내지 않는다.
- 확인됨/오경보 표시는 이 노트북(`data/`)에만 저장한다.
- 긴급/주의 같은 판단 등급을 만들지 않는다. 10초 연결선은 표시용 참고 정보다.

## 구조
```
care_dashboard/
├── server.py              # MQTT 구독 + 기록 + 규약 검사 + 실시간 전달 + 데모 재생
├── static/index.html      # 화면 골격
├── static/app.css         # 디자인 (라이트·다크·큰 글씨·휴대폰)
├── static/app.js          # 화면 동작
├── static/vendor/         # 외부 JS·글꼴 (인터넷 없이 동작)
├── samples/               # 데모 시나리오, 합성 클립
├── data/live|demo/        # 실행하면 생김: events.jsonl(원문), labels.jsonl(표시), clips/(보관 클립)
├── r1_finder.py           # R1 자동 찾기 (같은 네트워크에서 1883 포트가 열린 기기)
├── tools_fake_r2.py       # 집 리허설용 가짜 영상 파이 (vision 계정, 규약 그대로)
├── setup.bat / run_demo.bat / run_live.bat / run_fake_r2.bat
├── requirements.txt
└── THIRD_PARTY.md         # 외부 소스 목록
```

## 설치 (Windows, 처음 한 번 · 인터넷 필요)
`setup.bat` 실행. 또는:
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 실행
- 데모(파이 없이): `run_demo.bat` → 브라우저에서 `http://127.0.0.1:5000`
- 실시간(R1 접속): `run_live.bat` → 같은 네트워크에서 R1을 자동으로 찾아 접속, `app` 비밀번호 입력
- 가짜 영상 파이(리허설): `run_fake_r2.bat` → Enter마다 영상 감지 1건 발행, q로 종료
- 휴대폰: 노트북과 같은 Wi-Fi에서 실행 창에 찍힌 `http://<노트북IP>:5000` 접속. 처음 실행 때 Windows 방화벽 허용 필요.

## 서버가 하는 일
| 기능 | 내용 |
|---|---|
| 원문 기록 | 받은 메시지를 고치지 않고 받은 시각과 함께 `events.jsonl`에 저장. 다시 켜면 복원 |
| 규약 검사 | 4필드 유무, 시각 형식, 0~1 범위, http 주소 검사 → ok / warn / error |
| 중복 수신 | QoS 1로 같은 `incident_id`가 다시 오면 한 건으로 두고 횟수만 셈 |
| 같은 초 감지 | `_2`처럼 끝에 번호가 붙은 `incident_id` 구분 |
| 전달 지연 | 받은 시각 − `occurred_at` |
| 상태 | `home/status/audio`·`vision`의 online/offline. 브로커와 끊기면 둘 다 unknown (문서 규칙) |
| 클립 | 서버가 `clip_url`을 대신 가져와 보관 후 재생 (휴대폰 접근·CORS 문제 회피, 원본이 지워져도 기록 유지) |
| 확인 표시 | 확인됨/오경보 저장, 보호자 반응 시간(받은 시각 → 표시 시각) 계산 |
| 내보내기 | CSV(엑셀 한글 대응), JSON |

## API
| 주소 | 내용 |
|---|---|
| `GET /api/state` | 전체 상태 |
| `GET /api/stream` | 실시간 전달 (Server-Sent Events) |
| `POST /api/label` | `{"key": "...", "label": "confirmed" \| "false_alarm" \| "none"}` |
| `GET /api/clipcheck/<key>` | 클립 접근 점검 (용량, wav 길이 등) |
| `GET /api/clip/<key>` | 보관 클립 재생 |
| `GET /api/export.csv`, `/api/export.json` | 기록 내보내기 |
| `GET /api/qr.svg` | 휴대폰 접속 QR |
| `POST /api/demo/start\|stop\|reset` | 데모 제어 (데모 모드 전용) |
| `POST /api/session/new` | 지금까지 기록을 data/archive로 옮기고 새로 시작 |
| `GET /api/export.raw` | 받은 원문 로그(events.jsonl) 내려받기 |

## 진행 현황
- [x] 1단계: 폴더 구성, 외부 라이브러리, 데모 시나리오·합성 클립
- [x] 2단계: 서버 (데모·실시간 모드 시험 완료)
- [x] 3단계: 실시간 탭 화면 (상태 띠, 확인 대기 카드, 확인됨/오경보, 최근 감지, 상세 시트, 클립 재생, 알림음·진동, 큰 글씨, 다크 모드, 휴대폰 화면)
- [x] 4단계: 타임라인 + 10초 연결선 (음성·영상 2줄, 신뢰도=점 크기, 확인 상태=테두리 색, 연결 끊김 구간 빗금, 10분/1시간/오늘/전체, 함께 감지된 쌍 목록)
- [x] 5단계: 상세 패널 (음성 파형·영상 플레이어, 클립 점검·보관·내려받기, 함께 감지 이동, JSON 4필드 풀이·규약 검사, 전달 정보, 원문)
- [x] 6단계: 기록·통계 탭 (음성·영상 비교표: 실측 정밀도·신뢰도·전달 지연·중복·규약, 시간대별·신뢰도 분포 그래프, CSV/JSON 내려받기, 보고서용 요약 복사)
- [x] 7단계: 시스템 탭 (R1 주소·영상 팀 설정 줄·휴대폰 QR, 기기 연결 기록·가동률, 통신 로그: 한 상황당 JSON 수·시계 확인·통신 품질, 자동 점검, 원문 로그 내려받기, 새 기록 시작(보관)), https 접속 안내
- [x] 8단계: R1 실제 접속 시험 (집 Wi-Fi·휴대폰 핫스팟 모두 통과)

## 영상 팀 확인 후 바꿀 것
- 영상 클립 형식(브라우저 재생 가능 여부), `V_` 접두어, 실제 JSON 샘플 → `samples/` 교체
