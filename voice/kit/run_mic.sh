#!/bin/bash
# 마이크(reSpeaker 2-Mics HAT)가 달린 파이에서 실제 감지
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "먼저 bash install.sh 를 실행하세요."; exit 1; }
arecord -l 2>/dev/null | grep -qi seeed || { echo "reSpeaker 마이크가 보이지 않아요. 마이크 없이 시험하려면 bash run_test.sh"; exit 1; }
exec .venv/bin/python kws_service.py --cfg kws_service.json "$@"
