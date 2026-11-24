#!/bin/bash
# 마이크로 실제 감지: 위급 말 → 클립 저장 → JSON을 MQTT(home/alert/audio)로 전송
# 사용: bash run_mic.sh     (멈추기: Ctrl+C) / 장치 지정: MIC_DEVICE=plughw:1,0 bash run_mic.sh
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "먼저 bash install.sh 를 실행하세요."; exit 1; }
source ./mic_device.sh
DEV=$(pick_mic) || { echo "마이크 없이 시험하려면: bash run_test.sh"; exit 1; }
echo "마이크: $DEV"
exec .venv/bin/python kws_service.py --cfg kws_service.json --device "$DEV" "$@"
