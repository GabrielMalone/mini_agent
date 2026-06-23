#!/usr/bin/env python3
"""Windows SSE relay: pulls LiveLink data from Mac via SSE,
forwards raw UDP bytes to localhost so Unreal Engine can consume it.

Run this on the Windows machine:
    python windows_relay.py

Requirements: Python 3 (no extra dependencies)

Flow:
    iPhone --UDP--> Mac (iphone_proxy.py) --SSE--> Windows (this script) --UDP--> Unreal
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
import urllib.request
import urllib.error

# Primary: direct Tailscale connection (both machines on tailnet)
MAC_TAILSCALE_IP = "100.95.22.96"
MAC_HTTP_PORT = 11112
DIRECT_STREAM = f"http://{MAC_TAILSCALE_IP}:{MAC_HTTP_PORT}/stream"

# Fallback: Tailscale Funnel (HTTPS, works from anywhere)
FUNNEL_STREAM = "https://gabriels-macbook-air.tailaf9f7c.ts.net/stream"

LOCAL_UDP_HOST = "127.0.0.1"
LOCAL_UDP_PORT = 11111  # default Unreal LiveLink port


def try_connect(url: str, timeout: int = 10) -> bool:
    """Test if a URL is reachable."""
    try:
        req = urllib.request.Request(url + "/health" if "/stream" in url else url)
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def stream_and_forward(udp_host: str, udp_port: int, stream_url: str) -> None:
    """Connect to SSE, parse events, forward raw bytes as UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"UDP target: {udp_host}:{udp_port}", flush=True)
    print(f"SSE source: {stream_url}", flush=True)
    print("Waiting for LiveLink data... (Ctrl+C to stop)\n", flush=True)

    packet_count = 0
    last_log = time.time()

    while True:
        try:
            req = urllib.request.Request(stream_url)
            req.add_header("Accept", "text/event-stream")
            with urllib.request.urlopen(req, timeout=300) as resp:
                print(f"[SSE] Connected (HTTP {resp.status})", flush=True)
                buf = ""
                for chunk in iter(lambda: resp.read(4096), b""):
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n\n" in buf:
                        event, buf = buf.split("\n\n", 1)
                        for line in event.split("\n"):
                            if line.startswith("data: "):
                                data_str = line[6:]
                                try:
                                    record = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue

                                inner = record.get("data", record)
                                src = record.get("source", "?")

                                if isinstance(inner, dict) and "_raw_base64" in inner:
                                    raw = base64.b64decode(inner["_raw_base64"])
                                    try:
                                        sock.sendto(raw, (udp_host, udp_port))
                                    except OSError:
                                        pass
                                    packet_count += 1
                                    now = time.time()
                                    if now - last_log >= 2.0:
                                        print(
                                            f"[FWD] {packet_count} pkts, "
                                            f"latest {len(raw)}B v{raw[0]} "
                                            f"from {src}",
                                            flush=True,
                                        )
                                        last_log = now
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            print(f"[ERR] HTTP {e.code}: {body}", flush=True)
        except urllib.error.URLError as e:
            print(f"[ERR] Connection failed: {e.reason}", flush=True)
        except KeyboardInterrupt:
            print(f"\nStopped. ({packet_count} packets forwarded)", flush=True)
            break
        except Exception as e:
            print(f"[ERR] {e}", flush=True)

        print("Reconnecting in 3s...", flush=True)
        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SSE->UDP relay for LiveLink Bridge"
    )
    parser.add_argument(
        "--host",
        default=LOCAL_UDP_HOST,
        help=f"UDP target host (default: {LOCAL_UDP_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=LOCAL_UDP_PORT,
        help=f"UDP target port (default: {LOCAL_UDP_PORT})",
    )
    parser.add_argument(
        "--stream",
        default=None,
        help="SSE stream URL (auto-detected if not set)",
    )
    args = parser.parse_args()

    # Auto-detect best stream URL
    stream_url = args.stream
    if not stream_url:
        print("Testing connectivity...", flush=True)
        # Try direct Tailscale first (faster, no DERP relay)
        health_url = f"http://{MAC_TAILSCALE_IP}:{MAC_HTTP_PORT}/health"
        print(f"  Trying direct: {health_url} ...", end=" ", flush=True)
        if try_connect(health_url, timeout=5):
            print("OK!")
            stream_url = DIRECT_STREAM
        else:
            print("FAILED")
            # Try funnel
            health_url = f"{FUNNEL_STREAM.replace('/stream', '/health')}"
            print(f"  Trying funnel: {health_url} ...", end=" ", flush=True)
            if try_connect(health_url, timeout=10):
                print("OK!")
                stream_url = FUNNEL_STREAM
            else:
                print("FAILED")
                print("\n[FATAL] Cannot reach Mac. Check Tailscale connection.", flush=True)
                sys.exit(1)

    print(f"\nUsing: {stream_url}\n", flush=True)
    stream_and_forward(args.host, args.port, stream_url)


if __name__ == "__main__":
    main()
