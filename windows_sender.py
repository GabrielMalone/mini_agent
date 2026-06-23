#!/usr/bin/env python3
"""Reverse-tunnel sender for Windows → Mac LiveLink Bridge.

Run this on any machine (Windows/macOS/Linux) to push JSON data
through the public funnel to the LiveLink dashboard.

Usage:
    python windows_sender.py                          # sends test data every 2s
    python windows_sender.py --once                   # send one test packet then exit
    python windows_sender.py --file data.json         # send contents of a JSON file
    python windows_sender.py --stdin                  # read JSON lines from stdin

The funnel URL is:
    https://gabriels-macbook-air.tailaf9f7c.ts.net/ingest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, UTC

FUNNEL_INGEST = "https://gabriels-macbook-air.tailaf9f7c.ts.net/ingest"


def send_packet(data: dict) -> bool:
    """POST JSON to the funnel ingest endpoint. Returns True on success."""
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        FUNNEL_INGEST,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"[OK] {result.get('time', '?')} - sent: {json.dumps(data)[:80]}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"[ERR] HTTP {e.code}: {body}")
        return False
    except urllib.error.URLError as e:
        print(f"[ERR] Connection failed: {e.reason}")
        return False
    except Exception as e:
        print(f"[ERR] {e}")
        return False


def stream_test(interval: float = 2.0) -> None:
    """Send incrementing test packets forever."""
    count = 0
    print(f"Sending test packets every {interval}s to {FUNNEL_INGEST}")
    print("Ctrl+C to stop.\n")
    while True:
        count += 1
        data = {
            "sender": "windows-tunnel",
            "seq": count,
            "timestamp": datetime.now(UTC).isoformat(),
            "test": True,
            "message": f"Hello from reverse tunnel #{count}",
        }
        send_packet(data)
        time.sleep(interval)


def send_once(data: dict | None = None) -> None:
    """Send a single packet."""
    if data is None:
        data = {
            "sender": "windows-tunnel",
            "timestamp": datetime.now(UTC).isoformat(),
            "test": True,
        }
    send_packet(data)


def send_file(path: str) -> None:
    """Read a JSON file and send it."""
    with open(path) as f:
        data = json.load(f)
    send_packet(data)


def send_stdin() -> None:
    """Read JSON lines from stdin and send each."""
    print(f"Reading JSON lines from stdin, sending to {FUNNEL_INGEST}...")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[WARN] Skipping invalid JSON: {e}")
            continue
        send_packet(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse tunnel sender")
    parser.add_argument(
        "--once", action="store_true", help="Send one packet and exit"
    )
    parser.add_argument("--file", type=str, help="Send JSON from file")
    parser.add_argument(
        "--stdin", action="store_true", help="Read JSON lines from stdin"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between test packets (default: 2)",
    )
    args = parser.parse_args()

    if args.once:
        send_once()
    elif args.file:
        send_file(args.file)
    elif args.stdin:
        send_stdin()
    else:
        stream_test(args.interval)


if __name__ == "__main__":
    main()
