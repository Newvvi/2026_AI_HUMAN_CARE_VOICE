#!/bin/bash
# reSpeaker 2-Mics Pi HAT V2.0 디바이스 트리 오버레이 설치. Seeed 위키(V2 문서, 2026-01-29 갱신본, 2026-11-08 확인)의 절차를 그대로 옮긴 것이다.
# ※ 실제 장비에서는 아직 실행하지 못했다 (마이크 도착 후 확인). 파이4에서 이 오버레이가 동작하는지도 도착 후 확인 필요.
# 사용법: bash install_respeaker_v2.sh --dry-run   (할 일만 출력, 아무것도 바꾸지 않음)
#         bash install_respeaker_v2.sh             (실행. config.txt를 먼저 백업. 끝나면 sudo reboot)
set -e
DRY=0; [ "$1" = "--dry-run" ] && DRY=1
CFG=${CFG:-/boot/firmware/config.txt}
OVL_DIR=${OVL_DIR:-/boot/firmware/overlays}
run() { echo "+ $*"; if [ $DRY -eq 0 ]; then "$@"; fi; }
[ -f "$CFG" ] || { echo "$CFG 가 없습니다. 중단합니다 (이 OS는 /boot/config.txt를 쓸 수 있음. 직접 확인하세요)."; exit 1; }
echo "1) 준비물"
run sudo apt install -y git make device-tree-compiler
echo "2) 소스 받기"
if [ -d "$HOME/seeed-linux-dtoverlays" ]; then run git -C "$HOME/seeed-linux-dtoverlays" pull; else run git clone https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git "$HOME/seeed-linux-dtoverlays"; fi
echo "3) 오버레이 컴파일"
run make -C "$HOME/seeed-linux-dtoverlays" overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo
echo "4) 오버레이 복사"
run sudo cp "$HOME/seeed-linux-dtoverlays/overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo" "$OVL_DIR/respeaker-2mic-v2_0.dtbo"
echo "5) 부트 설정 백업과 수정"
run sudo cp "$CFG" "$CFG.bak_$(date +%Y%m%d_%H%M%S)"
if grep -q "^dtoverlay=respeaker-2mic-v2_0" "$CFG"; then echo "(이미 dtoverlay 줄이 있어 추가하지 않음)"; else
  echo "+ echo dtoverlay=respeaker-2mic-v2_0 | sudo tee -a $CFG"; [ $DRY -eq 1 ] || echo "dtoverlay=respeaker-2mic-v2_0" | sudo tee -a "$CFG"; fi
echo
echo "다음: sudo reboot  →  arecord -l 에 seeed2micvoicec 카드가 보이는지 확인 (카드 번호는 환경마다 다름)"
echo "     alsamixer 에서 F6으로 seeed-2mic-voicecard 선택 후 Capture 이득 조정"
[ $DRY -eq 1 ] && echo "(dry-run: 실제로는 아무것도 바꾸지 않았습니다)"
exit 0
