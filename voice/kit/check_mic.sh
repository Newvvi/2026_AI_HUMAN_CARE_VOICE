#!/bin/bash
# 마이크 확인: 장치를 찾고 3초 녹음해서 소리 크기를 보여 준다
# 사용: bash check_mic.sh        (MIC_DEVICE=plughw:1,0 bash check_mic.sh 로 장치 지정 가능)
cd "$(dirname "$0")"
source ./mic_device.sh
echo "== 녹음 장치 목록"
arecord -l 2>/dev/null | grep -E '^(card|카드)' || { echo "  녹음 장치가 없어요. 마이크 연결을 확인하세요."; exit 1; }
DEV=$(pick_mic) || exit 1
echo "== 사용할 장치: $DEV"
echo "== 3초 녹음합니다. 지금 평소 목소리로 말해 보세요 (\"하나 둘 셋\")"
arecord -q -D "$DEV" -f S16_LE -r 16000 -c 2 -d 3 /tmp/kit_mic.wav || { echo "  녹음 실패: 다른 프로그램(run_mic.sh, 음성 서비스)이 마이크를 쓰고 있는지 확인하세요."; exit 1; }
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
$PY - <<'PYEOF'
import wave, numpy as np
w = wave.open("/tmp/kit_mic.wav"); x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())[:, 0] / 32768.0
f = x[: len(x) // 320 * 320].reshape(-1, 320)
db = 10 * np.log10((f ** 2).mean(1) + 1e-12)
floor, peak = np.percentile(db, 10), db.max()
print("  바닥 소리 %.1f dBFS | 말소리 최대 %.1f dBFS | 차이 %.0f dB" % (floor, peak, peak - floor))
if peak - floor < 15:
    print("  ! 말소리가 바닥보다 15dB 이상 커야 감지가 시작돼요. 마이크에 더 가까이 말하거나 입력 크기(alsamixer)를 올리세요.")
elif peak > -3:
    print("  ! 너무 커서 찌그러질 수 있어요. 조금 떨어져서 말하거나 입력 크기를 낮추세요.")
else:
    print("  ✓ 마이크 정상. 다음: bash run_mic.sh")
PYEOF
