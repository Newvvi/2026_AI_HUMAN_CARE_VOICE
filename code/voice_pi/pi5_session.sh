#!/bin/bash
# pi5_session.sh: 파이4의 SD카드를 파이5에 잠깐 꽂았을 때, 음성 쪽 실측을 한 번에 돌려 ~/pi5_report.txt에 남긴다.
# 사용법: source ~/asr/bin/activate && bash ~/voice/pi5_session.sh [--quick] [--dry-run]
#   --quick   : tiny/base만, 부하 모의와 열 시험을 짧게 (약 25분)
#   --dry-run : 할 일만 출력하고 아무것도 돌리지 않음
#   기본(전체): small까지 포함 (약 60~75분)
# 전제: ~/pi_bench(구간 파일), ~/e1.m4a ~/e2.m4a ~/new3/n1~n3.m4a, ~/voice/(v0.3 도구와 soak.wav), 모델 캐시(tiny, base, small), 선택: stress-ng
# 결과 파일에는 IP·계정이 들어가지 않는다 (그대로 붙여넣어도 됨). 상세 계획은 docs/15 참고.
QUICK=0; DRY=0
for a in "$@"; do [ "$a" = "--quick" ] && QUICK=1; [ "$a" = "--dry-run" ] && DRY=1; done
V=$HOME/voice; B=$HOME/pi_bench; R=$HOME/pi5_report.txt
[ $DRY -eq 0 ] && : > "$R"
log() { echo "$@" | tee -a "$R"; }
sec() { log ""; log "=== $* ($(date +%H:%M:%S)) ==="; }
doit() { if [ $DRY -eq 1 ]; then echo "+ $*"; else eval "$*" 2>&1 | tee -a "$R"; fi; }
SIZES="tiny,base"; [ $QUICK -eq 0 ] && SIZES="tiny,base,small"
LOADSEC=420; HEATMIN=15; [ $QUICK -eq 1 ] && LOADSEC=200 && HEATMIN=5

sec "0. 장비와 환경"
doit 'tr -d "\0" < /proc/device-tree/model; echo'
doit 'echo "커널 $(uname -r) | 페이지 크기 $(getconf PAGESIZE) | $(grep PRETTY_NAME /etc/os-release | cut -d= -f2)"'
doit 'vcgencmd measure_temp; vcgencmd get_throttled; vcgencmd measure_clock arm; free -h | head -2'
doit 'for f in /sys/class/thermal/cooling_device*/type /sys/class/thermal/cooling_device*/cur_state; do [ -f $f ] && echo "$f: $(cat $f)"; done'
doit 'python3 -c "import faster_whisper, numpy, ctranslate2; print(\"패키지 정상, ctranslate2\", ctranslate2.__version__)"'

for T in 2 4; do
  sec "1. 구간당 처리 시간: 크기 $SIZES, 스레드 $T (파이4 기준: tiny 2.7초, base 5.5초, small 18.8초 / 스레드 2)"
  doit "python3 $V/bench_asr_pi.py $B --sizes $SIZES --threads $T | grep -E '^===|^평균|^온도'"
done

sec "2. 서비스 실시간 지연 (기본 설정: tiny + 조건부 small 재시도)"
for f in e1 e2; do
  log "[$f]"
  doit "python3 -u $V/voice_service.py --wav $HOME/$f.m4a --realtime --out $HOME/p5_$f | grep -E '^처리|^위급|^재시도|^이벤트 지연|^온도'"
done

sec "3. 새 문장 재현율과 시간: base를 주 모델로 (small은 재시도), 파이4 기준 합 31/39"
for f in $HOME/new3/n1 $HOME/new3/n2 $HOME/new3/n3 $HOME/e2; do
  log "[base 주 모델 $(basename $f)]"
  doit "python3 -u $V/voice_service.py --wav $f.m4a --model base --out $HOME/p5_b_$(basename $f) | grep -E '^처리|^위급'"
done
if [ $QUICK -eq 0 ]; then
  sec "3-2. small을 주 모델로 (재시도 없음), 파이4 기준 합 35/39, 구간당 18.8초"
  for f in $HOME/new3/n1 $HOME/new3/n2 $HOME/new3/n3 $HOME/e2; do
    log "[small 주 모델 $(basename $f)]"
    doit "python3 -u $V/voice_service.py --wav $f.m4a --model small --retry-mode off --out $HOME/p5_s_$(basename $f) | grep -E '^처리|^위급'"
  done
fi

sec "4. CPU 부하 모의 (영상 프로그램이 코어 3개를 쓰는 상황을 stress-ng로 흉내)"
if command -v stress-ng >/dev/null 2>&1; then
  if [ $DRY -eq 1 ]; then echo "+ stress-ng --cpu 3 --timeout ${LOADSEC}s &  (그동안 아래 두 시험)"; else stress-ng --cpu 3 --timeout ${LOADSEC}s >/dev/null 2>&1 & fi
  doit "python3 $V/bench_asr_pi.py $B --sizes tiny,base --threads 2 | grep -E '^===|^평균|^온도'"
  log "[부하 중 e2 실시간]"
  doit "python3 -u $V/voice_service.py --wav $HOME/e2.m4a --realtime --out $HOME/p5_load_e2 | grep -E '^처리|^위급|^재시도|^이벤트 지연|^온도'"
  [ $DRY -eq 1 ] || wait
else
  log "stress-ng가 없어 건너뜁니다 (미리 파이4에서 sudo apt install -y stress-ng)"
fi

sec "5. 열과 안정성: soak 파일을 ${HEATMIN}분 동안 실시간으로 (기본 설정)"
doit "timeout -s TERM $((HEATMIN * 60)) python3 -u $V/voice_service.py --wav $V/soak.wav --realtime --out $HOME/p5_soak --status-file $HOME/p5_status.json | grep -E '^처리|^위급|^재시도|^온도|^이벤트 지연'"
doit "cat $HOME/p5_status.json 2>/dev/null; echo"

sec "끝"
doit 'vcgencmd measure_temp; vcgencmd get_throttled'
log "보고서: $R  (이 파일을 그대로 붙여넣으세요)"
