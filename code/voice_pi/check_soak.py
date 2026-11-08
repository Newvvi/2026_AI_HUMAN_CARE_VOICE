"""
check_soak.py: 장시간(soak) 시험 결과 확인. 끼워 넣은 위급 발화(카나리아)를 감지했는지, 그 밖의 오경보는 몇 번인지, 상태 파일의 최댓값을 본다.
사용법:  python3 check_soak.py --events <out>_events.jsonl --canaries soak_canaries.json [--status status.json] [--history status_history.jsonl]
"""
import argparse
import json

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--events", required=True)
ap.add_argument("--canaries", required=True)
ap.add_argument("--status")
ap.add_argument("--history", help="상태 파일을 1분마다 모아 둔 jsonl (메모리·온도 추세 확인)")
args = ap.parse_args()
ev = [json.loads(l) for l in open(args.events, encoding="utf-8")]
cn = json.load(open(args.canaries, encoding="utf-8"))
used, det = set(), 0
for s, e, name in cn:
    hit = [i for i, x in enumerate(ev) if s - 1 <= x["audio_sec"] <= e + 2]
    used.update(hit)
    det += bool(hit)
fa = [x for i, x in enumerate(ev) if i not in used]
mm = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d}"
print(f"끼워 넣은 위급 발화 감지: {det}/{len(cn)}")
print(f"그 밖의 이벤트(오경보): {len(fa)}회" + ("" if not fa else " → " + ", ".join(mm(x['audio_sec']) for x in fa[:10])))
lat = [x["latency_sec"] for x in ev if x.get("latency_sec") is not None]
if lat:
    print(f"이벤트 지연(구간 끝 → 이벤트): 중앙 {np.median(lat):.1f}초, 95% {np.percentile(lat, 95):.1f}초, 최대 {max(lat):.1f}초")
if args.status:
    st = json.load(open(args.status, encoding="utf-8"))
    print("마지막 상태:", {k: st.get(k) for k in ("audio_min", "windows", "events", "retry_tried", "retry_hits", "retry_dropped", "rss_mb", "temp_c", "free_gb")})
if args.history:
    rows = []
    for line in open(args.history, encoding="utf-8"):
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    if len(rows) >= 6:
        rss = np.array([r["rss_mb"] for r in rows if r.get("rss_mb")], dtype=float)
        hrs = np.arange(len(rss)) / 60.0                         # 1분에 한 줄
        late = slice(len(rss) // 3, None)                         # 처음 1/3(모델 읽기·워밍업)은 빼고 추세를 봄
        slope = float(np.polyfit(hrs[late], rss[late], 1)[0])
        print(f"메모리(RSS): 처음 {rss[0]:.0f} → 끝 {rss[-1]:.0f}MB, 최대 {rss.max():.0f}MB, 후반 증가 추세 시간당 {slope:+.1f}MB")
        temps = [r["temp_c"] for r in rows if r.get("temp_c") is not None]
        free = [r["free_gb"] for r in rows if r.get("free_gb") is not None]
        if temps:
            print(f"온도: 최고 {max(temps):.1f}℃, 마지막 {temps[-1]:.1f}℃ | 디스크 여유 최소 {min(free):.1f}GB")
        print(f"상태 기록 {len(rows)}줄 (약 {len(rows)}분), 처리한 구간 {rows[-1].get('windows')}개, 재시도 건너뜀 {rows[-1].get('retry_dropped')}개")
    else:
        print("상태 기록이 너무 적어 추세를 계산하지 못했습니다 (줄 수", len(rows), ")")
