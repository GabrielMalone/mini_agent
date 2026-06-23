#!/usr/bin/env python3
"""Windows test: sends proper UE5 v6 LiveLink packets to 127.0.0.1:11111.
Run this AFTER you've added the Apple ARKit source in Unreal.
"""
import socket
import struct
import random
import time

UDP_IP = "127.0.0.1"
UDP_PORT = 11111

def build_v6(device_id="TestDevice", subject="TestFace", blends=None, frame=0):
    """Build a UE5-compatible v6 packet (aelzeiny format)."""
    if blends is None:
        blends = [random.random() * 0.5 for _ in range(61)]
    blends = list(blends[:61]) + [0.0] * max(0, 61 - len(blends))
    
    dev_bytes = device_id.encode('utf-8')
    subj_bytes = subject.encode('utf-8')
    
    buf = struct.pack('>B', 6)                      # version
    buf += struct.pack('>i', len(dev_bytes))         # device_id length (BE int32)
    buf += dev_bytes                                 # device_id
    buf += struct.pack('>i', len(subj_bytes))        # subject name length (BE int32)
    buf += subj_bytes                                # subject name
    buf += struct.pack('>ifii', frame, 0.0, 60, 1)  # frametime
    buf += struct.pack('>B', 61)                     # blend count
    buf += struct.pack('>' + 'f' * 61, *blends)      # 61 blends (BE float)
    return buf

print(f"Sending UE5 LiveLink v6 packets to {UDP_IP}:{UDP_PORT}")
print("PREREQUISITES:")
print("  1. In Unreal: Window -> Virtual Production -> LiveLink")
print("  2. Click '+ Source' -> Apple ARKit -> set Port to 11111")
print("  3. Make sure Edit -> Plugins -> 'Apple ARKit Face Support' is ENABLED")
print()
print("If you see 'TestDevice' appear in the LiveLink subjects list, it works!")
print("Ctrl+C to stop\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
frame = 0

while True:
    blends = [random.random() * 0.4 + 0.1 for _ in range(61)]  # subtle animation
    packet = build_v6(frame=frame, blends=blends)
    sock.sendto(packet, (UDP_IP, UDP_PORT))
    print(f"[{frame:04d}] Sent {len(packet)}B to {UDP_IP}:{UDP_PORT}", end='\r')
    frame += 1
    time.sleep(0.05)
