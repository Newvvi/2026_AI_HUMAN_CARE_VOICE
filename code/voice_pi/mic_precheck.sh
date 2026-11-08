#!/bin/bash
# 마이크(reSpeaker 2-Mics Pi HAT V2.0) 설치 전 파이 점검: 읽기 전용, sudo 필요 없음. 가상환경(asr)을 켜고 실행하면 파이썬 패키지도 확인한다.
# 사용법: bash mic_precheck.sh        (출력에는 IP·계정이 나오지 않게 했으니 그대로 붙여넣어도 된다)
echo "== 장비/OS"
[ -f /proc/device-tree/model ] && tr -d '\0' < /proc/device-tree/model; echo
grep PRETTY_NAME /etc/os-release; echo "커널 $(uname -r) / $(uname -m) / $(python3 --version 2>&1)"
echo "== 부트 설정 위치 (Bookworm 이후는 /boot/firmware/config.txt)"
ls -d /boot/firmware/config.txt /boot/config.txt 2>/dev/null || echo "config.txt를 못 찾음"
echo "== 메모리/디스크"; free -h | head -2; df -h / | tail -1
echo "== 온도/전원"; vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null
echo "== 부트 설정의 오디오·I2C 줄"; grep -E "^(dtparam=i2c|dtparam=audio|dtoverlay)" /boot/firmware/config.txt 2>/dev/null || echo "(해당 줄 없음)"
echo "== 녹음 장치 (HAT 설치 전에는 HDMI만 보이거나 없어야 정상)"; arecord -l 2>&1 | head -8
echo "== 코덱 드라이버 모듈 (TLV320AIC3x)"; modinfo snd-soc-tlv320aic3x-i2c 2>/dev/null | head -2 || true
modinfo snd-soc-tlv320aic3x-i2c >/dev/null 2>&1 && echo "모듈 있음" || echo "모듈 정보 없음 (설치 후 dmesg로 확인)"
echo "== 설치에 필요한 도구"
for c in git make dtc gcc arecord; do command -v $c >/dev/null && echo "$c: 있음" || echo "$c: 없음"; done
echo "== 이미 설치된 respeaker 오버레이"; ls /boot/firmware/overlays 2>/dev/null | grep -i respeaker || echo "(없음)"
echo "== 시간/네트워크 (IP는 표시하지 않음)"; echo "NTP 동기화: $(timedatectl show -p NTPSynchronized --value 2>/dev/null)"; hostname -I >/dev/null 2>&1 && echo "네트워크 주소: 있음" || echo "네트워크 주소: 없음"
echo "== 마이크 입력용 라이브러리"; ldconfig -p 2>/dev/null | grep -q libportaudio && echo "libportaudio: 있음" || echo "libportaudio: 없음 → sudo apt install -y libportaudio2"
echo "== 파이썬 패키지 (현재 python3 기준)"
python3 - <<'PY'
import importlib
for m in ("faster_whisper", "numpy", "paho.mqtt.client", "sounddevice"):
    try:
        importlib.import_module(m); print(f"{m}: 있음")
    except Exception as e:
        print(f"{m}: 없음 ({type(e).__name__})")
PY
