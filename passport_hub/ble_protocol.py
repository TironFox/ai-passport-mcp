from __future__ import annotations

import json
import struct
from typing import Any

# Shared BLE GATT UUIDs (must match AI Passport firmware app_link.c)
PASSPORT_BLE_SERVICE_UUID = "7a4f0001-3b6c-4a91-9e22-8f1d4c5b0001"
PASSPORT_BLE_CTRL_CHAR_UUID = "7a4f0002-3b6c-4a91-9e22-8f1d4c5b0001"   # Write/Notify: JSON control + Wi-Fi provisioning
PASSPORT_BLE_AUDIO_CHAR_UUID = "7a4f0003-3b6c-4a91-9e22-8f1d4c5b0001"  # Notify/Write: Framed audio chunks

FRAME_MAGIC = b"AP"
FRAME_TYPE_WIFI_PROV = 0x01
FRAME_TYPE_TASK_SYNC = 0x02
FRAME_TYPE_VOICE_UP = 0x03
FRAME_TYPE_TTS_DOWN = 0x04


def encode_frame(frame_type: int, seq: int, payload: bytes) -> bytes:
    """Header: 2B magic ('AP') + 1B type + 2B seq (LE) + 2B len (LE) + payload."""
    if len(payload) > 0xFFFF:
        raise ValueError("Payload exceeds 65535 bytes")
    header = struct.pack("<2sBHH", FRAME_MAGIC, frame_type & 0xFF, seq & 0xFFFF, len(payload))
    return header + payload


def decode_frame(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < 7:
        raise ValueError("Frame too short")
    magic, frame_type, seq, length = struct.unpack("<2sBHH", data[:7])
    if magic != FRAME_MAGIC:
        raise ValueError(f"Invalid frame magic: {magic!r}")
    if len(data) < 7 + length:
        raise ValueError("Truncated frame payload")
    return frame_type, seq, data[7 : 7 + length]


def build_wifi_provision_payload(ssid: str, password: str, hub_host: str, hub_port: int) -> bytes:
    body: dict[str, Any] = {
        "ssid": ssid,
        "pass": password,
        "host": hub_host,
        "port": hub_port,
    }
    return encode_frame(
        FRAME_TYPE_WIFI_PROV,
        seq=0,
        payload=json.dumps(body, separators=(",", ":")).encode("utf-8"),
    )
