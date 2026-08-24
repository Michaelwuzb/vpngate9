#!/usr/bin/env python3
# vpngate9 通道守护脚本
# 功能: 定时检查 9 个通道, 发现 error/假连接 -> 自动换节点重连, 保持长期稳定
# 部署: /opt/michaelvpn/vpngate9_guard.py  (109 上)

import urllib.request, urllib.parse, json, time, secrets, random, sys, os

PANEL = "http://127.0.0.1:8787"
USER, PASS = "admin", "admin"
NUM_CHANNELS = 9
PROXY_BASE_PORT = 47928
CHECK_EVERY = 60          # 每 60 秒检查一轮
ROTATE_EVERY = 30        # 每 30 轮(默认30分钟)主动换节点切IP(0=不主动切)
SOCKS_TEST_URL = "http://api.ipify.org"
SOCKS_TEST_TIMEOUT = 10

cookie = ""

def login():
    global cookie
    data = urllib.parse.urlencode({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(PANEL + "/api/login", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        # 登录返回 302 重定向, 禁止跟随, 直接从响应头取 token
        opener = urllib.request.build_opener(NoRedirect)
        resp = opener.open(req, timeout=10)
        _parse_cookie(resp)
    except urllib.error.HTTPError as e:
        _parse_cookie(e)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 不跟随重定向

def _parse_cookie(resp):
    global cookie
    for h, v in resp.getheaders():
        if h.lower() == "set-cookie":
            for part in v.split(";"):
                if part.strip().startswith("token="):
                    cookie = part.strip()


def api(path, data=None, method="GET"):
    global cookie
    if not cookie:
        login()
        if not cookie:
            return None
    headers = {"Cookie": cookie}
    if data is not None:
        data = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(PANEL + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            cookie = ""
        return None
    except Exception:
        return None

def get_status():
    return api("/api/status")

def get_nodes():
    d = api("/api/nodes?country=Japan&ip_type=residential") or {}
    return d.get("nodes", [])

def reconnect(idx, node_id=None):
    path = f"/api/channel/{idx}/connect?country=Japan&ip_type=residential"
    if node_id:
        path += f"&node_id={urllib.parse.quote(node_id)}"
    return api(path, method="POST")

def test_socks(port):
    """实测 socks5 端口能否拿到出口IP, 返回 IP 或 None"""
    import socket
    try:
        import socks  # pysocks
    except ImportError:
        return _test_socks_curl(port)
    return _test_socks_curl(port)

def _test_socks_curl(port):
    import subprocess
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", str(SOCKS_TEST_TIMEOUT),
             "-x", f"socks5://127.0.0.1:{port}", SOCKS_TEST_URL],
            capture_output=True, text=True, timeout=SOCKS_TEST_TIMEOUT + 5)
        ip = out.stdout.strip()
        return ip if ip and "." in ip else None
    except Exception:
        return None

def main():
    print(f"vpngate9 guard start: {NUM_CHANNELS} channels, check every {CHECK_EVERY}s", flush=True)
    round_n = 0
    while True:
        round_n += 1
        T = time.strftime("%H:%M:%S")
        st = get_status()
        if not st:
            print(f"[{T}] status null, re-login", flush=True)
            cookie = ""
            time.sleep(CHECK_EVERY)
            continue
        channels = st.get("channels", [])
        do_rotate = (ROTATE_EVERY > 0 and round_n % ROTATE_EVERY == 0)
        nodes = get_nodes() if (do_rotate or True) else []
        for ch in channels:
            if not ch.get("enabled", True):
                print(f"[{T}] CH{idx} SKIP (manually disabled)", flush=True)
                continue
            idx = ch["index"]
            state = ch.get("state")
            port = PROXY_BASE_PORT + idx
            need = False
            reason = ""
            if state not in ("connected",):
                need = True
                reason = f"state={state}"
            else:
                ip = test_socks(port)
                if not ip:
                    need = True
                    reason = "fake-connected (no traffic)"
                else:
                    print(f"[{T}] CH{idx} OK {ip}", flush=True)
            if do_rotate and state == "connected":
                need = True
                reason = "scheduled rotate"
            if need:
                node = random.choice(nodes)["id"] if nodes else None
                print(f"[{T}] CH{idx} FIX ({reason}) -> reconnect node={node}", flush=True)
                reconnect(idx, node)
                time.sleep(3)
        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    T = time.strftime("%H:%M:%S")
    main()
