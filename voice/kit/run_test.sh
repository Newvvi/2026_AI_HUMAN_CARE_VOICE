#!/bin/bash
# 마이크 없이 시험: 엔터를 누를 때마다 녹음해 둔 위급 말을 흘려 보내 실제와 똑같이 감지·클립 저장·MQTT 전송
# 사용: bash run_test.sh              (엔터로 하나씩)
#       bash run_test.sh --auto 60    (60초마다 자동)
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "먼저 bash install.sh 를 실행하세요."; exit 1; }
exec .venv/bin/python kws_service.py --cfg kws_service.json --replay samples "$@"
