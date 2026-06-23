#!/usr/bin/env python3
"""UDP/TCP bridge for iPhone LiveLink -> Tailscale Funnel + SSE reverse tunnel."""
from __future__ import annotations

import base64
import http.server
import json
import os
import queue
import socket
import socketserver
import sys
import threading
import time
import datetime
from datetime import UTC

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from livelink_converter import convert

# ── config ──────────────────────────────────────────────────
UDP_PORTS = [11111, 6666]  # iPhone LiveLink UDP + alt
TCP_PORT = 11113  # tailscale serve -> here (tailnet-only TLS)
HTTP_PORT = 11112  # local HTTP, funnel proxies here
IPHONE_TAILSCALE_IP = "100.88.37.82"
FUNNEL_HOST = "gabriels-macbook-air.tailaf9f7c.ts.net"
MAC_TAILSCALE_IP = "100.95.22.96"
KEEPALIVE_INTERVAL = 2.0
MAX_PACKETS = 200

# ── global state ────────────────────────────────────────────
_state_lock = threading.Lock()
_packets: list[dict] = []
_packet_count = 0
_start_time = time.time()
_sse_clients: list[queue.Queue] = []

# UDP socket for forwarding converted v6 packets to local Unreal
_unreal_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
UNREAL_HOST = "127.0.0.1"
UNREAL_PORT = 11111


def _broadcast(record: dict) -> None:
    payload = f"data: {json.dumps(record)}\n\n"
    dead = []
    with _state_lock:
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ── network listeners ───────────────────────────────────────

def _udp_listener(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Bind to Tailscale IP only so Unreal can use 127.0.0.1:11111
    bind_ip = MAC_TAILSCALE_IP if port == 11111 else "0.0.0.0"
    sock.bind((bind_ip, port))
    print(f"[UDP:{port}] Listening on {bind_ip}", flush=True)
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except Exception:
            continue
        # Ignore loopback packets (our own forwards to Unreal)
        if addr[0] == "127.0.0.1" or addr[0] == "::1":
            continue
        ts = datetime.datetime.now(UTC).isoformat().replace("+00:00", "Z")
        # Convert iPhone v1 packet -> UE5 v6 packet
        try:
            converted = convert(data)
        except Exception:
            converted = data  # fallback: send raw
        record = {
            "time": ts,
            "source": f"udp:{addr[0]}:{addr[1]}",
            "port": port,
            "data": {"_raw_base64": base64.b64encode(converted).decode("ascii")},
        }
        with _state_lock:
            _packets.append(record)
            global _packet_count
            _packet_count += 1
            if len(_packets) > MAX_PACKETS:
                _packets[:] = _packets[-MAX_PACKETS:]
        sz = len(converted)
        print(f"[UDP:{port}] {addr[0]}:{addr[1]} -> {len(data)}B=>{sz}B v{converted[0]}", flush=True)
        if sz < 100:
            print(f"[WARN] tiny pkt! data[0]={data[0]} hex={converted[:60].hex()}", flush=True)
        # Forward converted v6 packet to local Unreal Engine
        try:
            _unreal_sock.sendto(converted, (UNREAL_HOST, UNREAL_PORT))
        except Exception:
            pass
        _broadcast(record)


def _tcp_listener(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(5)
    print(f"[TCP:{port}] Listening (tailscale serve -> here)", flush=True)
    while True:
        conn, addr = sock.accept()
        threading.Thread(target=_handle_tcp, args=(conn, addr), daemon=True).start()


def _handle_tcp(conn: socket.socket, addr: tuple) -> None:
    buf = b""
    try:
        while True:
            chunk = conn.recv(65535)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                ts = datetime.datetime.now(UTC).isoformat().replace("+00:00", "Z")
                record = {
                    "time": ts,
                    "source": f"tcp:{addr[0]}:{addr[1]}",
                    "data": {"_raw_base64": base64.b64encode(line).decode("ascii")},
                }
                with _state_lock:
                    _packets.append(record)
                    global _packet_count
                    _packet_count += 1
                    if len(_packets) > MAX_PACKETS:
                        _packets[:] = _packets[-MAX_PACKETS:]
                print(f"[TCP] {addr[0]}:{addr[1]} -> {len(line)}B", flush=True)
                _broadcast(record)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _keepalive_pinger() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ping = b"KEEPALIVE"
    while True:
        try:
            sock.sendto(ping, (IPHONE_TAILSCALE_IP, 11111))
        except Exception:
            pass
        time.sleep(KEEPALIVE_INTERVAL)


# ── HTTP handler ────────────────────────────────────────────

class BridgeHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if self.path in ("/api", "/api/"):
            self._serve_api()
            return
        if self.path in ("/stream", "/stream/"):
            self._serve_sse()
            return
        self._serve_html()

    def do_POST(self) -> None:
        if self.path in ("/ingest", "/ingest/"):
            length = int(self.headers.get("Content-Length", 0))
            if length > 1_000_000:
                self._json(413, {"error": "payload too large"})
                return
            raw = self.rfile.read(length)
            ts = datetime.datetime.now(UTC).isoformat().replace("+00:00", "Z")
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {"_raw_base64": base64.b64encode(raw).decode("ascii")}
            record = {"time": ts, "source": "remote", "data": data}
            with _state_lock:
                _packets.append(record)
                global _packet_count
                _packet_count += 1
                if len(_packets) > MAX_PACKETS:
                    _packets[:] = _packets[-MAX_PACKETS:]
            print(f"[INGEST] {json.dumps(data)[:120]}", flush=True)
            _broadcast(record)
            self._json(200, {"ok": True, "time": ts})
        else:
            self._json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_sse(self) -> None:
        q: queue.Queue = queue.Queue()
        with _state_lock:
            _sse_clients.append(q)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            with _state_lock:
                for record in _packets[-50:]:
                    payload = f"data: {json.dumps(record)}\n\n"
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=15)
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _state_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    def _serve_api(self) -> None:
        with _state_lock:
            latest = _packets[-1] if _packets else None
            count = _packet_count
            uptime = time.time() - _start_time
        age = 999
        if latest:
            try:
                pkt_time = datetime.datetime.fromisoformat(latest["time"])
                age = time.time() - pkt_time.timestamp()
            except Exception:
                pass
        latest_safe = None
        if latest:
            latest_safe = dict(latest)
            d = latest_safe.get("data", {})
            if isinstance(d, dict) and "_raw_base64" in d:
                d = dict(d)
                b64 = d["_raw_base64"]
                d["_raw_base64"] = b64[:80] + ("..." if len(b64) > 80 else "")
                latest_safe["data"] = d
        self._json(200, {
            "uptime_s": round(uptime, 1),
            "packets_received": count,
            "latest": latest_safe,
            "active": latest is not None and age < 30,
            "sse_clients": len(_sse_clients),
        })

    def _serve_html(self) -> None:
        local_ip = "unknown"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        with _state_lock:
            count = _packet_count
            latest = _packets[-1] if _packets else None
            uptime = round(time.time() - _start_time, 1)
            sse_n = len(_sse_clients)
        if latest:
            try:
                pkt_time = datetime.datetime.fromisoformat(latest["time"])
                age = time.time() - pkt_time.timestamp()
                stc = "active" if age < 30 else "inactive"
                stt = "ACTIVE" if age < 30 else "STALE"
            except Exception:
                stc = "inactive"
                stt = "ERROR"
        else:
            stc = "inactive"
            stt = "WAITING"
        lj = json.dumps(latest, indent=2) if latest else ""
        ts_ip = MAC_TAILSCALE_IP

        page = (
            "<!DOCTYPE html><html><head>"
            "<meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>LiveLink Bridge</title>"
            "<style>"
            "*{margin:0;padding:0;box-sizing:border-box}"
            "body{font-family:-apple-system,sans-serif;"
            "background:#0d1117;color:#c9d1d9;max-width:640px;"
            "margin:2em auto;padding:0 1em}"
            ".card{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;padding:1.25em;margin:1em 0}"
            ".stat{font-size:2.25em;font-weight:700;color:#58a6ff}"
            ".label{color:#8b949e;font-size:.75em;"
            "text-transform:uppercase;letter-spacing:.05em}"
            ".active{color:#3fb950}.inactive{color:#f85149}"
            "pre{background:#0d1117;padding:1em;border-radius:6px;"
            "overflow-x:auto;font-size:.78em;white-space:pre-wrap;"
            "border:1px solid #30363d;max-height:300px;overflow-y:auto}"
            "#status{font-size:1.5em;font-weight:700}"
            "h1{font-size:1.5em;margin-bottom:.5em}"
            "code{background:#21262d;padding:.15em .4em;border-radius:4px;"
            "font-size:.9em}"
            ".info{color:#8b949e;font-size:.75em;text-align:center;margin-top:1em}"
            "a{color:#58a6ff}"
            "</style></head><body>"
            "<h1>LiveLink Bridge</h1>"
            "<div class=card>"
            "<span class=label>iPhone Target (pick one)</span><br><br>"
            "<strong>Same WiFi:</strong> "
            f"<code>{local_ip}:11111</code><br>"
            "<strong>Tailscale:</strong> "
            f"<code>{ts_ip}:11111</code><br>"
            "<strong>Tailscale TCP:</strong> "
            f"<code>{FUNNEL_HOST}:11115</code>"
            "</div>"
            "<div class=card>"
            "<span class=label>Reverse Tunnel</span><br><br>"
            "<strong>POST</strong> <code>/ingest</code> &mdash; push JSON from anywhere<br>"
            "<strong>SSE</strong> <code>/stream</code> &mdash; "
            f"<span id=sse_clients>{sse_n}</span> relay(s) connected"
            "</div>"
            f"<div class=card>"
            f"<span class=label>Status</span><br>"
            f"<span id=status class={stc}>{stt}</span></div>"
            f"<div class=card>"
            f"<span class=label>Packets</span><br>"
            f"<span id=count class=stat>{count}</span></div>"
            f"<div class=card>"
            f"<span class=label>Uptime</span><br>"
            f"<span id=uptime class=stat>{uptime}s</span></div>"
            f"<div class=card>"
            f"<span class=label>Latest</span>"
            f"<pre id=latest>{lj or 'No packets yet.'}</pre></div>"
            "<p class=info>Auto-refresh 3s | "
            "<a href=/api>JSON API</a> | "
            "<a href=/stream>SSE Stream</a></p>"
            "<script>"
            "setInterval(async()=>{"
            "try{let r=await fetch('/api');let d=await r.json();"
            "document.getElementById('count').textContent=d.packets_received;"
            "document.getElementById('uptime').textContent=d.uptime_s+'s';"
            "document.getElementById('latest').textContent="
            "d.latest?JSON.stringify(d.latest,null,2):'No packets yet.';"
            "document.getElementById('sse_clients').textContent=d.sse_clients||0;"
            "let s=document.getElementById('status');"
            "let a=d.active;"
            "s.textContent=a?'ACTIVE':'WAITING';"
            "s.className=a?'active':'inactive'}"
            "catch(e){}},3000)"
            "</script></body></html>"
        )
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[HTTP] {self.client_address[0]} - {fmt % args}", flush=True)


def main() -> None:
    print(f"PID: {os.getpid()}", flush=True)
    for port in UDP_PORTS:
        t = threading.Thread(target=_udp_listener, args=(port,), daemon=True)
        t.start()
    tcp = threading.Thread(target=_tcp_listener, args=(TCP_PORT,), daemon=True)
    tcp.start()
    ping = threading.Thread(target=_keepalive_pinger, daemon=True)
    ping.start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(
        ("0.0.0.0", HTTP_PORT), BridgeHandler
    ) as httpd:
        print(f"[HTTP] Listening on 0.0.0.0:{HTTP_PORT}", flush=True)
        print("[READY] UDP:11111  UDP:6666  TCP:11113", flush=True)
        print(f"[READY] iPhone -> {MAC_TAILSCALE_IP}:11111", flush=True)
        print(f"[READY] Funnel  -> :{HTTP_PORT}", flush=True)
        print("[READY] SSE     -> /stream", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.", flush=True)
            httpd.shutdown()


if __name__ == "__main__":
    main()
