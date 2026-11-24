#!/bin/bash
# 음성 파이 키트 설치: MQTT 브로커(mosquitto) + 계정·권한(v1.1.0) + 파이썬 환경
# 사용: bash install.sh            처음 한 번 (인터넷 필요, sudo 비밀번호를 물을 수 있음)
#       bash install.sh --mqtt-only  MQTT 설정만 다시 (프로그램 설치 건너뜀)
set -e
cd "$(dirname "$0")"
PW=12345678
MQTT_ONLY=0; [ "$1" = "--mqtt-only" ] && MQTT_ONLY=1
ok()   { echo -e "\033[32m  ✓ $*\033[0m"; }
warn() { echo -e "\033[33m  ! $*\033[0m"; }

echo "== 1/5 기기 확인"
if [ "$(uname -m)" != "aarch64" ]; then
  echo "  64비트 Raspberry Pi OS(aarch64)가 필요해요. 지금: $(uname -m)"; exit 1
fi
ok "64비트 OS"
others=$(ls /etc/mosquitto/conf.d/*.conf 2>/dev/null || true)
if [ -n "$others" ] && [ -z "$KIT_YES" ]; then
  warn "이 파이에 이미 MQTT 설정이 있어요:"; echo "$others" | sed 's/^/      /'
  warn "다른 팀 파이(영상 파이 등)라면 여기서 멈추세요. 이 파이를 음성 파이(브로커)로 쓸 때만 진행합니다."
  read -p "  기존 설정을 백업하고 음성 파이 설정으로 바꿀까요? (y/N) " yn
  [ "$yn" = "y" ] || { echo "  설치를 멈췄어요."; exit 1; }
fi

if [ $MQTT_ONLY = 0 ]; then
  echo "== 2/5 프로그램 설치 (mosquitto, 녹음 도구, 파이썬 가상환경)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq mosquitto mosquitto-clients alsa-utils python3-venv >/dev/null
  ok "mosquitto $(dpkg-query -W -f='${Version}' mosquitto)"
  echo "== 3/5 파이썬 환경 (.venv: numpy, onnxruntime, paho-mqtt)"
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q numpy onnxruntime paho-mqtt
  .venv/bin/python -c "import numpy, onnxruntime, paho.mqtt; print('  ✓ onnxruntime', onnxruntime.__version__)"
else
  echo "== 2~3/5 프로그램 설치 건너뜀 (--mqtt-only)"
fi

echo "== 4/5 MQTT 브로커 설정 (계정·권한, 포트 1883)"
CONF=$(ls mqtt/conf.d/*.conf 2>/dev/null || echo mqtt/voice_kit.conf)
ACL_PATH=$(grep -h '^acl_file' $CONF | awk '{print $2}' | head -1); ACL_PATH=${ACL_PATH:-/etc/mosquitto/acl}
PW_PATH=$(grep -h '^password_file' $CONF | awk '{print $2}' | head -1); PW_PATH=${PW_PATH:-/etc/mosquitto/passwd}
USERS=$(grep -E '^user ' mqtt/acl | awk '{print $2}' | sort -u)
BK=/etc/mosquitto/backup_$(date +%Y%m%d_%H%M%S)
sudo mkdir -p "$BK"
sudo cp -a /etc/mosquitto/mosquitto.conf "$BK/" 2>/dev/null || true
sudo cp -a "$PW_PATH" "$ACL_PATH" "$BK/" 2>/dev/null || true
for f in /etc/mosquitto/conf.d/*.conf; do [ -e "$f" ] && sudo mv "$f" "$BK/"; done
sudo cp mqtt/mosquitto.conf /etc/mosquitto/mosquitto.conf
for f in $CONF; do sudo cp "$f" /etc/mosquitto/conf.d/; done
sudo cp mqtt/acl "$ACL_PATH"
sudo rm -f "$PW_PATH" && sudo touch "$PW_PATH"
for u in $USERS; do sudo mosquitto_passwd -b "$PW_PATH" "$u" "$PW" 2>/dev/null; done
sudo chown mosquitto:mosquitto "$PW_PATH" "$ACL_PATH"
sudo chmod 0700 "$PW_PATH"
sudo systemctl enable mosquitto >/dev/null 2>&1
sudo systemctl restart mosquitto
sleep 1
systemctl is-active --quiet mosquitto && ok "mosquitto 실행 중 | 계정: $(echo $USERS | tr '\n' ' ')| 비밀번호 모두 $PW | 예전 설정 백업: $BK" \
  || { echo "  mosquitto가 켜지지 않았어요:"; sudo journalctl -u mosquitto -n 15 --no-pager; exit 1; }

echo "== 5/5 보내기·받기 시험 (audio → app)"
rm -f /tmp/kit_sub.txt
mosquitto_sub -h localhost -u app -P "$PW" -t home/alert/audio -C 1 -W 6 > /tmp/kit_sub.txt 2>/dev/null &
sleep 1
mosquitto_pub -h localhost -u audio -P "$PW" -q 1 -t home/alert/audio -m '{"incident_id":"A_install_test"}'
wait || true
if grep -q A_install_test /tmp/kit_sub.txt; then ok "MQTT 보내기·받기 성공"; else echo "  MQTT 시험 실패 — README의 '문제 해결'을 보세요"; exit 1; fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "설치 끝. 이 파이 주소: $IP  (MQTT $IP:1883, 계정 비밀번호 모두 $PW)"
echo "다음:  bash check_mic.sh  →  bash run_mic.sh   (다른 창에서 bash watch.sh)"
