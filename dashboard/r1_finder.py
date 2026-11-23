"""R1(브로커) 자동 찾기
핫스팟에서는 IP가 접속할 때마다 바뀔 수 있으므로, 노트북과 같은 네트워크(/24)에서
MQTT 포트(1883)가 열린 기기를 찾아 R1 후보로 돌려준다. 이름(.local) 방식은 쓰지 않는다.
"""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor


def my_ipv4s():
    """이 노트북의 내부 IPv4 주소 (인터넷으로 나가는 주소를 맨 앞에)"""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # 실제로 보내지는 않음, 나가는 인터페이스만 확인
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if ipaddress.ip_address(ip).is_private and not ip.startswith("127.")]


def _open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return ip
    except OSError:
        return None


def find_brokers(port=1883, timeout=0.4, log=print):
    mine = my_ipv4s()
    if not mine:
        log("[R1 찾기] 이 노트북의 내부 IP가 없습니다. Wi-Fi(핫스팟) 연결을 확인하세요.")
        return []
    hits = []
    for ip in mine:
        net = ipaddress.ip_network(ip + "/24", strict=False)
        log(f"[R1 찾기] {net} 에서 MQTT({port}) 포트를 찾는 중... (이 노트북 {ip})")
        hosts = [str(h) for h in net.hosts() if str(h) != ip]
        with ThreadPoolExecutor(max_workers=64) as ex:
            for r in ex.map(lambda h: _open(h, port, timeout), hosts):
                if r and r not in hits:
                    hits.append(r)
        if hits:
            break
    return hits
