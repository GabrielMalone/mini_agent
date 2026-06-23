#!/usr/bin/env python3
"""Convert iPhone LiveLink Face v1 packets -> UE5 v6 packets.

Uses the exact format from PyLiveLinkFace.encode() which is proven to work
with Unreal Engine 5.6.
"""

import struct
import time
import base64


def parse_iphone_packet(raw: bytes) -> dict | None:
    """Parse iPhone LiveLink Face UDP packet.

    The iPhone app sends a format with:
        uint8   version (1)
        uint8   flags (0)
        uint16  device_id_length (LE)
        char[]  device_id (e.g. GUID like "1FE45684-A04D-45B3-86FC-E28AD24B2AA4")
        uint16  subject_name_length (LE)
        char[]  subject_name
        int32   frame_number (LE)
        float   fps (LE)
        int32   frame (LE)
        int32   subframe (LE)
        uint8   blend_count
        float[] blends (LE, count * 4 bytes)
    """
    if len(raw) < 8:
        return None

    ver = raw[0]
    if ver != 1:
        return None

    try:
        _flags = raw[1]
        dev_len = struct.unpack_from('<H', raw, 2)[0]
        if dev_len > 256 or 4 + dev_len > len(raw):
            return None
        off = 4
        device_id = raw[off:off + dev_len].decode('utf-8', errors='replace')
        off += dev_len

        if off + 2 > len(raw):
            return None
        subj_len = struct.unpack_from('<H', raw, off)[0]
        off += 2
        if subj_len > 256 or off + subj_len > len(raw):
            null_pos = raw.find(b'\x00', off)
            if null_pos == -1 or null_pos > off + 256:
                subject_name = "iPhone"
            else:
                subject_name = raw[off:null_pos].decode('utf-8', errors='replace')
                off = null_pos + 1
        else:
            subject_name = raw[off:off + subj_len].decode('utf-8', errors='replace')
            off += subj_len

        if off + 16 > len(raw):
            return None
        frame_num = struct.unpack_from('<i', raw, off)[0]
        fps = struct.unpack_from('<f', raw, off + 4)[0]
        _frame = struct.unpack_from('<i', raw, off + 8)[0]
        _subframe = struct.unpack_from('<i', raw, off + 12)[0]
        off += 16

        if off >= len(raw):
            return None
        blend_count = raw[off]
        off += 1

        if blend_count > 61 or blend_count < 0:
            blend_count = min(blend_count, 61) if blend_count > 0 else 0

        blends = []
        if blend_count > 0 and off + blend_count * 4 <= len(raw):
            for i in range(blend_count):
                b = struct.unpack_from('<f', raw, off + i * 4)[0]
                blends.append(b)

        return {
            "device_id": device_id,
            "subject_name": subject_name,
            "blends": blends,
            "version": ver,
            "frame_number": frame_num,
            "fps": fps,
        }
    except (struct.error, UnicodeDecodeError, IndexError):
        return None


def build_arkit_packet(
    device_id: str = "iPhone",
    subject_name: str = "iPhoneFace",
    blends: list[float] | None = None,
    frame_number: int = 1,
    fps: float = 60.0,
) -> bytes:
    """Build a UE5 LiveLink packet matching PyLiveLinkFace/Unreal format.

    Format (reverse-engineered from working PyLiveLinkFace library):
        uint32 LE  version = 6
        char[37]   uuid (with '$' prefix, no length prefix)
        int32 BE   name length
        char[]     name
        uint32 BE  frame number
        uint32 BE  sub_frame
        uint32 BE  fps numerator
        uint32 BE  fps denominator
        uint8  BE  blend count = 61
        float[61]  blend values (big-endian)
    """
    NUM_BLENDS = 61

    if blends is None:
        blends = [0.0] * NUM_BLENDS

    # Pad/truncate to exactly 61 blends, clamp to valid ranges
    # ARKit blends: [0,1], head rotation (yaw/pitch/roll at 52-54): [-1,1]
    clamped = []
    for i, b in enumerate(blends):
        if 52 <= i <= 54:
            clamped.append(max(-1.0, min(1.0, float(b))))
        else:
            clamped.append(max(0.0, min(1.0, float(b))))
    while len(clamped) < NUM_BLENDS:
        clamped.append(0.0)
    clamped = clamped[:NUM_BLENDS]

    # UUID: ensure $ prefix, 37 chars total
    uuid_str = device_id
    if not uuid_str.startswith('$'):
        uuid_str = '$' + uuid_str
    uuid_bytes = uuid_str.encode('utf-8', errors='replace')[:37].ljust(37, b'\x00')

    name_bytes = subject_name.encode('utf-8', errors='replace')[:255]

    sub_frame = 0  # PyLiveLinkFace uses uint32 not float

    packet = struct.pack('<I', 6)                              # version (LE uint32)
    packet += uuid_bytes                                        # uuid (37 bytes, no prefix)
    packet += struct.pack('!i', len(name_bytes))               # name length (BE int32)
    packet += name_bytes                                        # name
    packet += struct.pack('!II', frame_number, sub_frame)      # frames (BE uint32 x2)
    packet += struct.pack('!II', int(fps), 1)                  # frame rate (BE uint32 x2)
    packet += struct.pack('!B', NUM_BLENDS)                    # count (BE uint8)
    packet += struct.pack('!' + 'f' * NUM_BLENDS, *clamped)    # blends (BE float x61)

    return packet


def convert(raw_iphone: bytes) -> bytes:
    """Convert iPhone v1 packet -> UE5 v6 packet."""
    parsed = parse_iphone_packet(raw_iphone)

    # Sensible defaults when parsing fails or produces garbage
    device_id = "iPhone"
    subject_name = "iPhoneFace"
    blends: list[float] = []
    frame_number = int(time.time() * 60) % 100000

    if parsed is not None:
        device_id = parsed.get("device_id", device_id)
        subject_name = parsed.get("subject_name", subject_name)
        blends = parsed.get("blends", blends)
        parsed_frame = parsed.get("frame_number", 0)
        # Only use parsed frame if it looks reasonable
        if 0 <= parsed_frame < 1000000:
            frame_number = parsed_frame

    # Ensure device_id and subject_name are reasonable
    if len(device_id) < 3:
        device_id = "iPhone"
    if len(subject_name) < 2:
        subject_name = "iPhoneFace"

    return build_arkit_packet(
        device_id=device_id,
        subject_name=subject_name,
        blends=blends,
        frame_number=frame_number,
        fps=60.0,
    )
def convert_to_base64(raw_iphone: bytes) -> str:
    """Convert and return base64-encoded result."""
    return base64.b64encode(convert(raw_iphone)).decode('ascii')
