#!/bin/bash
# 음성 파이 시험 키트 설치: MQTT 서버(mosquitto) + 계정 5개·권한 + 파이썬 환경
# 사용: bash install.sh      (처음 한 번, 인터넷 필요, sudo 비밀번호를 물을 수 있음)
set -e
cd "$(dirname "$0")"
PW=12345678
ok()   { echo -e "\033[32m  ✓ $*\033[0m"; }
warn() { echo -e "\033[33m  ! $*\033[0m"; }

echo "== 1/5 기기 확인"
if [ "$(uname -m)" != "aarch64" ]; then
  echo "  64비트 Raspberry Pi OS(aarch64)가 필요해요. 지금: $(uname -m)"; exit 1
fi
ok "64비트 OS"
others=$(ls /etc/mosquitto/conf.d/*.conf 2>/dev/null | grep -v voice_kit.conf || true)
if [ -n "$others" ]; then
  warn "이 파이에 이미 MQTT 설정이 있어요:"; echo "$others" | sed 's/^/      /'
  warn "영상 파이처럼 다른 MQTT 설정(브리지 등)을 쓰는 파이라면 여기서 멈추세요."
  read -p "  기존 설정을 백업하고 음성 파이 설정으로 바꿀까요? (y/N) " yn
  [ "$yn" = "y" ] || { echo "  설치를 멈췄어요."; exit 1; }
fi

echo "== 2/5 프로그램 설치 (mosquitto, 파이썬 가상환경)"
sudo apt-get update -qq
sudo apt-get install -y -qq mosquitto mosquitto-clients python3-venv >/dev/null
ok "mosquitto $(dpkg-query -W -f='${Version}' mosquitto)"

echo "== 3/5 파이썬 환경 (.venv: numpy, onnxruntime, paho-mqtt)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q numpy onnxruntime paho-mqtt
.venv/bin/python -c "import numpy, onnxruntime, paho.mqtt; print('  ✓ onnxruntime', onnxruntime.__version__)"

echo "== 4/5 MQTT 서버 설정 (계정 5개, 권한, 포트 1883)"
BK=/etc/mosquitto/backup_$(date +%Y%m%d_%H%M%S)
sudo mkdir -p "$BK"
sudo cp -a /etc/mosquitto/mosquitto.conf "$BK/" 2>/dev/null || true
sudo cp -a /etc/mosquitto/passwd /etc/mosquitto/acl "$BK/" 2>/dev/null || true
for f in /etc/mosquitto/conf.d/*.conf; do [ -e "$f" ] && sudo mv "$f" "$BK/"; done
sudo cp mqtt/mosquitto.conf /etc/mosquitto/mosquitto.conf
sudo cp mqtt/voice_kit.conf /etc/mosquitto/conf.d/voice_kit.conf
sudo cp mqtt/acl /etc/mosquitto/acl
sudo rm -f /etc/mosquitto/passwd && sudo touch /etc/mosquitto/passwd
for u in audio fusion app bridge watchdog; do sudo mosquitto_passwd -b /etc/mosquitto/passwd "$u" "$PW" 2>/dev/null; done
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd /etc/mosquitto/acl
sudo chmod 0700 /etc/mosquitto/passwd
sudo systemctl enable mosquitto >/dev/null 2>&1
sudo systemctl restart mosquitto
sleep 1
systemctl is-active --quiet mosquitto && ok "mosquitto 실행 중 (예전 설정 백업: $BK)" || { echo "  mosquitto가 켜지지 않았어요:"; sudo journalctl -u mosquitto -n 15 --no-pager; exit 1; }

echo "== 5/5 보내기·받기 시험 (audio → fusion)"
rm -f /tmp/kit_sub.txt
mosquitto_sub -h localhost -u fusion -P "$PW" -t home/alert/audio -C 1 -W 6 > /tmp/kit_sub.txt 2>/dev/null &
sleep 1
mosquitto_pub -h localhost -u audio -P "$PW" -q 1 -t home/alert/audio -m '{"incident_id":"A_install_test"}'
wait || true
if grep -q A_install_test /tmp/kit_sub.txt; then ok "MQTT 보내기·받기 성공"; else echo "  MQTT 시험 실패 — 안내서의 '문제 해결'을 보세요"; exit 1; fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "설치 끝. 이 파이 주소: $IP  (앱·영상 브리지는 $IP:1883, 계정 비밀번호 모두 $PW)"
echo "다음:  bash run_test.sh"
