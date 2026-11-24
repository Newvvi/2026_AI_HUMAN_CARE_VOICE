#!/bin/bash
# 음성 파이(R1)에서 키트 zip 만들기:  bash ~/AI_HUMAN_CARE/voice/kit/build_kit.sh
# - 감지 서비스·모델·실행 스크립트·안내서 + R1의 실제 MQTT 설정(계정 권한)을 그대로 담는다
set -e
VOICE="$(cd "$(dirname "$0")/.." && pwd)"
NAME=voice_kit
OUT=~/$NAME
rm -rf "$OUT" && mkdir -p "$OUT/model" "$OUT/samples" "$OUT/mqtt/conf.d"
cp "$VOICE/pi/kws_service.py" "$OUT/"
cp "$VOICE"/pi/model/* "$OUT/model/" 2>/dev/null || cp ~/voice/model/* "$OUT/model/"
for f in install.sh check_mic.sh mic_device.sh run_mic.sh run_test.sh watch.sh README.md; do cp "$VOICE/kit/$f" "$OUT/"; done
cp "$VOICE/kit/mqtt/mosquitto.conf" "$VOICE/kit/mqtt/acl" "$OUT/mqtt/"

echo "== MQTT 설정: R1에서 지금 쓰는 설정을 그대로 담기 (sudo 비밀번호를 물을 수 있음)"
ACL_PATH=$(grep -h '^acl_file' /etc/mosquitto/conf.d/*.conf 2>/dev/null | awk '{print $2}' | head -1)
if [ -n "$ACL_PATH" ] && sudo test -r "$ACL_PATH"; then
  sudo cat "$ACL_PATH" > "$OUT/mqtt/acl"
  for f in /etc/mosquitto/conf.d/*.conf; do cp "$f" "$OUT/mqtt/conf.d/"; done
  echo "  ✓ R1 설정 사용: $(ls /etc/mosquitto/conf.d/*.conf | xargs -n1 basename | tr '\n' ' ')+ $ACL_PATH"
else
  cp "$VOICE/kit/mqtt/voice_kit.conf" "$OUT/mqtt/conf.d/"
  echo "  ! R1 설정을 읽지 못해 키트 기본 설정(v1.1.0 형식) 사용"
fi
echo "  계정: $(grep -E '^user ' "$OUT/mqtt/acl" | awk '{print $2}' | tr '\n' ' ')"

cat > "$OUT/kws_service.json" <<'JSON'
{
 "model_dir": "model", "model": "r3_tcresnet.onnx", "config": "r3_tcresnet_config.json",
 "thr": 0.95, "gate_rel_db": 15.0, "gate_abs_dbfs": -65.0,
 "device": "plughw:CARD=seeed2micvoicec,DEV=0",
 "http_port": 8080, "public_host": "",
 "pre_s": 10.0, "post_s": 1.0, "max_clips": 300,
 "mqtt": {"host": "localhost", "port": 1883, "topic": "home/alert/audio", "username": "audio", "password": "12345678",
          "tls": false, "client_id": "voice-pi", "status_topic": "home/status/audio"},
 "data_dir": "data"
}
JSON
echo "== 마이크 없는 시험용 샘플 (실제 모델로 감지되는 위급 말만)"
~/asr/bin/python "$VOICE/kit/make_samples.py" "$OUT/samples" "$OUT"
rm -rf "$OUT/data" "$OUT/__pycache__"
chmod +x "$OUT"/*.sh
cd ~ && rm -f $NAME.zip
~/asr/bin/python -m zipfile -c $NAME.zip $NAME
echo
echo "✓ ~/$NAME.zip ($(du -h ~/$NAME.zip | cut -f1))"
echo "PC(PowerShell)에서 받기:  scp user01@$(hostname -I | awk '{print $1}'):~/$NAME.zip \"\$HOME\\Desktop\\\""
