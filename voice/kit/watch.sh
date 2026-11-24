#!/bin/bash
# 받는 쪽 화면: 브로커에 들어오는 알림(JSON)과 기기 상태를 보기 좋게 보여 준다 (계정 app, 읽기 전용)
# 사용: bash watch.sh                 이 파이의 브로커
#       bash watch.sh 10.221.81.23    다른 파이의 브로커
HOST=${1:-localhost}
echo "== $HOST:1883 구독 중 (home/alert/#, home/status/#) — 멈추기: Ctrl+C"
mosquitto_sub -h "$HOST" -u app -P 12345678 -t 'home/alert/#' -t 'home/status/#' -v | python3 -u -c '
import sys, json, datetime
for line in sys.stdin:
    topic, _, body = line.rstrip("\n").partition(" ")
    now = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        m = json.loads(body)
    except ValueError:
        print("[%s] %s = %s" % (now, topic, body), flush=True); continue
    print("=" * 60)
    print("[%s 받음] %s" % (now, topic))
    for k in ("incident_id", "confidence", "occurred_at", "clip_url"):
        print("  %-12s %s" % (k, m.get(k)))
    print("  원본 JSON   %s" % body, flush=True)
'
