# 마이크 장치 고르기 (다른 스크립트가 source 해서 씀)
# 순서: MIC_DEVICE 환경변수 → reSpeaker(seeed) → 첫 번째 녹음 장치
pick_mic() {
  if [ -n "$MIC_DEVICE" ]; then echo "$MIC_DEVICE"; return 0; fi
  local list; list=$(arecord -l 2>/dev/null)
  if echo "$list" | grep -qi seeed2micvoicec; then echo "plughw:CARD=seeed2micvoicec,DEV=0"; return 0; fi
  local card dev
  card=$(echo "$list" | grep -E '^(card|카드) [0-9]+' | head -1 | sed -E 's/^(card|카드) ([0-9]+).*/\2/')
  dev=$(echo "$list" | grep -E '^(card|카드) [0-9]+' | head -1 | sed -E 's/.*(device|장치) ([0-9]+).*/\2/')
  if [ -z "$card" ]; then echo "녹음 장치를 찾지 못했어요 (arecord -l)" >&2; return 1; fi
  echo "plughw:${card},${dev:-0}"
}
