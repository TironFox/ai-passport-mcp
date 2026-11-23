#!/usr/bin/env python3
"""BLE Pairing & Wi-Fi Provisioning utility for FoloToy AI Passport.

Put AI Passport into BLE mode (Hold DN -> Click OK -> BLE DIRECT), then run:
  python3 pair_ble.py --ssid "YourWiFi" --password "YourPassword"
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys

from passport_hub.ble_protocol import (
    PASSPORT_BLE_CTRL_CHAR_UUID,
    build_wifi_provision_payload,
)


def detect_lan_ip() -> str:
    # Prefer macOS physical Wi-Fi/Ethernet interface over VPN/proxy TUN (e.g. 10.x / 198.18.x).
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if ip:
                return ip
        except (OSError, subprocess.CalledProcessError):
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


async def pair(ssid: str, password: str, hub_host: str, hub_port: int, name: str) -> None:
    try:
        from bleak import BleakClient, BleakScanner  # type: ignore
    except ImportError:
        print("Missing 'bleak'. Install with: pip3 install bleak", file=sys.stderr)
        sys.exit(1)

    print(f"[pair] Scanning for BLE device '{name}' (10s)...")
    devices = await BleakScanner.discover(timeout=10.0)
    device = None
    for d in devices:
        if d.name and name.lower() in d.name.lower():
            device = d
            break
    if device is None:
        print(f"[pair] Device '{name}' not found among {len(devices)} BLE devices.")
        if len(devices) == 0:
            print("[pair] Hint: Grant Bluetooth permission to Terminal in macOS System Settings -> Privacy & Security -> Bluetooth.", file=sys.stderr)
        else:
            print("[pair] Hint: On AI Passport, open SETTINGS -> BLE Pairing Mode -> press OK (shows BLE ADV), then retry.", file=sys.stderr)
        sys.exit(2)

    payload = build_wifi_provision_payload(ssid, password, hub_host, hub_port)
    print(f"[pair] Found {device.name} ({device.address}), sending Wi-Fi + Hub ({hub_host}:{hub_port})...")
    async with BleakClient(device) as client:
        await client.write_gatt_char(PASSPORT_BLE_CTRL_CHAR_UUID, payload, response=True)
    print("[pair] Provisioning sent! AI Passport will save to NVS and reconnect over Wi-Fi.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pair FoloToy AI Passport over BLE")
    parser.add_argument("--ssid", required=True, help="2.4 GHz Wi-Fi SSID")
    parser.add_argument("--password", default="", help="Wi-Fi password")
    parser.add_argument("--host", default=detect_lan_ip(), help="Mac Passport Hub IP")
    parser.add_argument("--port", type=int, default=8765, help="Mac Passport Hub port")
    parser.add_argument("--name", default="AI-Passport", help="BLE advertising name")
    args = parser.parse_args()
    asyncio.run(pair(args.ssid, args.password, args.host, args.port, args.name))


if __name__ == "__main__":
    main()
