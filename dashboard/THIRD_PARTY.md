# 외부 소스 목록

안심 모니터는 아래 외부 소스를 그대로 가져와 사용한다. 직접 수정하지 않았다.
현장에 인터넷이 없어도 동작하도록 JS·글꼴은 `static/vendor/`에 넣어 두었다. 라이선스 원문은 `static/vendor/licenses/`에 있다.

| 이름 | 버전 | 용도 | 라이선스 | 위치 |
|---|---|---|---|---|
| Eclipse Paho MQTT Python | 2.2.0 | R1 브로커 구독 (김우석 배포 문서와 같은 버전) | EPL-2.0 / EDL-1.0 | pip (`requirements.txt`) |
| Flask | 3.1.3 | 노트북 웹 서버 | BSD-3-Clause | pip |
| qrcode (Python) | 8.2 | 휴대폰 접속 주소 QR | BSD | pip |
| wavesurfer.js | 7.8.16 | 음성 클립 파형 표시·재생 | BSD-3-Clause | `static/vendor/wavesurfer.min.js` |
| Chart.js | 4.4.7 | 통계 그래프 | MIT | `static/vendor/chart.umd.js` |
| Pretendard | 1.3.9 | 한글 글꼴 | SIL OFL 1.1 | `static/vendor/fonts/PretendardVariable.woff2` |

## 직접 만든 데모 자료
- `samples/clips/demo_audio_*.wav`: ffmpeg로 만든 합성음(잡음 + 톤). 실제 녹음이 아니다.
- `samples/clips/demo_vision_*.mp4`: ffmpeg 테스트 화면. 영상 팀 실제 클립을 받으면 교체한다.
- `samples/demo_scenario.json`: 화면 시험용 순서. 영상 항목과 `V_` 접두어는 영상 팀 확인 전 임시값이다.
