"""가짜 영상 파이(R2) - 집 리허설 전용 시험 도구
영상 팀 파이가 없을 때 노트북에서 R2 역할을 대신한다. (vision 계정, 김우석 문서 v1.1.0 규약 그대로)
 - 접속하면 home/status/vision = online, 종료하면 offline, 강제 종료하면 브로커가 LWT로 offline
 - Enter를 누를 때마다 home/alert/vision 으로 4필드 JSON 1건 발행
 - clip_url은 이 노트북의 안심 모니터가 제공하는 데모 영상 (실제 영상 팀 클립 아님)
실행: python tools_fake_r2.py [--host R1_IP]   (비밀번호: MQTT_PASSWORD 또는 입력)
"""
import argparse, getpass, json, os, random, time
from datetime import datetime, timedelta, timezone
import paho.mqtt.client as paho
from r1_finder import find_brokers, my_ipv4s

KST = timezone(timedelta(hours=9))
p = argparse.ArgumentParser()
p.add_argument("--host", default=os.getenv("MQTT_HOST", "auto"))
p.add_argument("--web-port", type=int, default=5000)
a = p.parse_args()
host = a.host
if host in (None, "", "auto"):
    hits = find_brokers()
    if not hits:
        raise SystemExit("R1을 찾지 못했습니다. --host 로 IP를 직접 지정하세요.")
    host = hits[0]
    print(f"[가짜 R2] R1 = {host}")
pw = os.getenv("MQTT_PASSWORD") or getpass.getpass("vision 비밀번호: ")
me = (my_ipv4s() or ["127.0.0.1"])[0]

c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2, client_id="fake-r2-vision", protocol=paho.MQTTv311)
c.username_pw_set("vision", pw)
c.will_set("home/status/vision", "offline", qos=1, retain=True)
c.on_connect = lambda cl, u, f, rc, pr: (print(f"[가짜 R2] 연결 거부 {rc}") if rc.is_failure
                                         else (cl.publish("home/status/vision", "online", qos=1, retain=True), print("[가짜 R2] 연결됨 → online")))
c.reconnect_delay_set(1, 10)
c.connect_async(host, 1883, 30)
c.loop_start()
used, n = set(), 0
try:
    while True:
        cmd = input("Enter = 영상 감지 1건 발행, q = 종료 > ").strip().lower()
        if cmd == "q":
            break
        if not c.is_connected():
            print("아직 연결 안 됨")
            continue
        t = datetime.now(KST)
        iid = "V_" + t.strftime("%y%m%d%H%M%S")
        base, k = iid, 2
        while iid in used:
            iid, k = f"{base}_{k}", k + 1
        used.add(iid)
        n += 1
        msg = {"incident_id": iid, "confidence": round(random.uniform(0.6, 0.97), 2),
               "occurred_at": t.isoformat(timespec="seconds"),
               "clip_url": f"http://{me}:{a.web_port}/samples/clips/demo_vision_{1 + n % 2}.mp4"}
        info = c.publish("home/alert/vision", json.dumps(msg, ensure_ascii=False), qos=1)
        info.wait_for_publish(5)
        print("[가짜 R2] 발행", "PUBACK 확인" if info.is_published() else "확인 없음", msg)
except (KeyboardInterrupt, EOFError):
    pass
finally:
    if c.is_connected():
        c.publish("home/status/vision", "offline", qos=1, retain=True).wait_for_publish(3)
    c.disconnect(); c.loop_stop()
    print("[가짜 R2] 종료 → offline")
