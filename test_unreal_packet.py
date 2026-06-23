#!/usr/bin/env python3
"""Send test ARKit packets to Unreal in multiple formats.

Run on Windows: python test_unreal_packet.py
Check: Window -> LiveLink -> for any new subject appearing.
"""
import struct
import socket
import time

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
TARGET = ('127.0.0.1', 11111)

def make_blends():
    b = [0.25] * 61
    b[0] = 0.8   # EyeBlinkLeft
    b[17] = 0.9  # JawOpen
    return b

def send_packets(label, pkt, count=5):
    print(f"  Size: {len(pkt)}B")
    for i in range(count):
        sock.sendto(pkt, TARGET)
        time.sleep(0.3)
    print(f"  Sent {count}. Check for '{label}'.")

# ============================================================
print("=== Format 1: PyLiveLinkFace ===")
pkt = struct.pack('<I', 5)  # version uint32 LE
pkt += b"TEST-DEVICE-ID-1234567890\x00\x00"
pkt += struct.pack('>I', 8)  # name_len
pkt += b"PyLLFace"
pkt += struct.pack('>IIII', 42, 0, 60, 1)  # frame, sub, fps, denom
pkt += struct.pack('<B', 61)
pkt += struct.pack('<61f', *make_blends())
send_packets("PyLLFace", pkt)

# ============================================================
print("\n=== Format 2: ARKit v6 strings-first BE-length ===")
pkt = struct.pack('<B', 6)
pkt += struct.pack('>I', 11) + b"ARKIT-DEVICE"
pkt += struct.pack('>I', 9) + b"ARKitFace"
pkt += struct.pack('<ifii', 100, 60.0, 0, 0)
pkt += struct.pack('<B', 61)
pkt += struct.pack('<61f', *make_blends())
send_packets("ARKitFace", pkt)

# ============================================================
print("\n=== Format 3: ARKit v6 blends-first null-term ===")
pkt = struct.pack('<B', 6)
pkt += struct.pack('<ifii', 200, 60.0, 0, 0)
pkt += struct.pack('<B', 61)
pkt += struct.pack('<61f', *make_blends())
pkt += b"NullTerm\x00"
pkt += b"NULL-DEVICE\x00"
send_packets("NullTerm", pkt)

# ============================================================
print("\n=== Format 4: iPhone raw v1 (584B) ===")
iphone_hex = (
    "0100240031464534353638342d413034442d343542332d383646432d4532"
    "38414432344232414134"  # ...truncated for readability...
)
# Use the full hex we captured
iphone_hex = (
    "0100240031464534353638342d413034442d343542332d383646432d4532"
    "384144323442324141346fd344005045683f3c00000001000000fb00f059"
    "90603016901a50025001e002c0000000000000000000000000000047804b"
    "3001d007c0000005d0006005000000000000000000000000000000000000"
    "0000a03f4040001e001d0000000000000000000000000000000000000000"
    "0000000000000000000000000000000ffffffff000de00300000000800d6"
    "009c000f000000000000003000e000000000000400200000000401690263"
    "002e003c00020041008300300000000801e900dd005c0090000000000000"
    "000801c5023500de00c3003900a0003f00aa02a802ce000a0000a0d0a0d6"
    "80f0a0d501c2011704e3049300b400b200b800bb00dc012c000d00030015"
    "001e000e001f0003003100570022001300100000000000000000000000000"
    "44404e4042804f000000000000000040080009c0064007000000000000000"
    "000000000000000000000000000000000000000000000000000000010e015"
    "400a00090000000000000000c001000040020000c0794076000000004031c"
    "02a000000008004c004000000000000000000000000600d0000802ac00100"
    "0000000000c063b0620000000000000000b00000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000dc7acfbf911491c0867220c17bc86ac048a64fc2"
    "03f3713e"
)
pkt = bytes.fromhex(iphone_hex)
send_packets("iPhoneRaw", pkt)

sock.close()
print("\n" + "=" * 60)
print("DONE! Check Unreal LiveLink for:")
print("  PyLLFace / ARKitFace / NullTerm / iPhoneRaw")
print("If ANY appear, tell me which format worked!")
