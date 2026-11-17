#!/bin/bash
# 음성 파이에서 시험 키트 zip 만들기:  bash ~/AI_HUMAN_CARE/voice/kit/build_kit.sh
set -e
VOICE="$(cd "$(dirname "$0")/.." && pwd)"
OUT=~/voice_test_kit
rm -rf "$OUT" && mkdir -p "$OUT/model" "$OUT/samples" "$OUT/mqtt"
cp "$VOICE/pi/kws_service.py" "$OUT/"
cp "$VOICE"/pi/model/* "$OUT/model/" 2>/dev/null || cp ~/voice/model/* "$OUT/model/"
cp "$VOICE"/kit/install.sh "$VOICE"/kit/run_test.sh "$VOICE"/kit/run_mic.sh "$VOICE"/kit/README.md "$OUT/"
cp "$VOICE"/kit/mqtt/* "$OUT/mqtt/"
cat > "$OUT/kws_service.json" <<'JSON'
{
 "model_dir": "model", "model": "r3_tcresnet.onnx", "config": "r3_tcresnet_config.json",
 "thr": 0.95, "gate_rel_db": 15.0, "gate_abs_dbfs": -65.0,
 "device": "plughw:CARD=seeed2micvoicec,DEV=0",
 "http_port": 8080, "public_host": "",
 "pre_s": 10.0, "post_s": 1.0, "max_clips": 300,
 "mqtt": {"host": "localhost", "port": 1883, "topic": "home/alert/audio", "username": "audio", "password": "12345678", "tls": false, "client_id": "voice-pi"},
 "data_dir": "data"
}
JSON
echo "== 샘플 만들기 (실제 모델로 감지되는 위급 말만)"
~/asr/bin/python "$VOICE/kit/make_samples.py" "$OUT/samples" "$OUT"
rm -rf "$OUT/data" "$OUT/__pycache__"
cd ~ && rm -f voice_test_kit.zip
~/asr/bin/python -m zipfile -c voice_test_kit.zip voice_test_kit
echo
echo "✓ ~/voice_test_kit.zip ($(du -h ~/voice_test_kit.zip | cut -f1))"
echo "PC(PowerShell)에서 받기:  scp user01@$(hostname -I | awk '{print $1}'):~/voice_test_kit.zip \$HOME\\Desktop\\"
