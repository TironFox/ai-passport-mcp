"""Firmware release discovery, SHA256 verification, and ESP32-C3 flashing for AI Passport."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_FIRMWARE_REPO = os.environ.get("PASSPORT_FIRMWARE_REPO", "TironFox/ai-passport-mcp")
DEFAULT_FIRMWARE_ASSET = "FoloToy-AI-Passport-full.bin"
ESP32_VIDS = {0x303A}  # Espressif native USB-Serial/JTAG
COMMON_USB_UART_VIDS = {0x1A86, 0x10C4, 0x0403}  # CH340, CP210x, FTDI


def get_cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "passport-hub" / "firmware"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "passport-hub" / "firmware"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-passport-mcp/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_get_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers=_github_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_download(url: str, dest: Path, timeout: float = 120.0) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=_github_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fp:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            fp.write(chunk)
    tmp.replace(dest)


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def list_serial_devices() -> list[dict[str, Any]]:
    """List candidate macOS/Linux serial ports for FoloToy AI Passport (ESP32-C3)."""
    devices: list[dict[str, Any]] = []
    try:
        from serial.tools import list_ports  # type: ignore

        for p in list_ports.comports():
            port = p.device
            if sys.platform == "darwin" and port.startswith("/dev/tty."):
                # Prefer /dev/cu.* on macOS to avoid DTR hang
                continue
            vid = p.vid
            pid = p.pid
            is_espressif = vid in ESP32_VIDS
            is_uart = vid in COMMON_USB_UART_VIDS
            looks_usb = any(
                k in port.lower()
                for k in ("usbmodem", "usbserial", "wchusbserial", "slab_usbtouart", "ttyacm", "ttyusb")
            )
            if not (is_espressif or is_uart or looks_usb):
                continue
            devices.append(
                {
                    "port": port,
                    "description": p.description or "",
                    "manufacturer": p.manufacturer or "",
                    "vid": f"0x{vid:04X}" if vid is not None else None,
                    "pid": f"0x{pid:04X}" if pid is not None else None,
                    "recommended": is_espressif or "usbmodem" in port.lower(),
                }
            )
    except Exception:
        patterns = [
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.SLAB_USBtoUART*",
            "/dev/ttyACM*",
            "/dev/ttyUSB*",
        ]
        seen: set[str] = set()
        for pat in patterns:
            for port in sorted(glob.glob(pat)):
                if port in seen:
                    continue
                seen.add(port)
                devices.append(
                    {
                        "port": port,
                        "description": "USB Serial Device",
                        "manufacturer": "",
                        "vid": None,
                        "pid": None,
                        "recommended": "usbmodem" in port.lower(),
                    }
                )

    devices.sort(key=lambda d: (not d["recommended"], d["port"]))
    return devices


def _pick_firmware_assets(assets: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    bin_asset = None
    sha_asset = None
    for a in assets:
        name = str(a.get("name") or "")
        if name == DEFAULT_FIRMWARE_ASSET:
            bin_asset = a
        elif name == f"{DEFAULT_FIRMWARE_ASSET}.sha256":
            sha_asset = a
    if bin_asset is None:
        for a in assets:
            name = str(a.get("name") or "")
            if name.endswith(".bin"):
                bin_asset = a
                break
    if sha_asset is None and bin_asset is not None:
        target = f"{bin_asset['name']}.sha256"
        for a in assets:
            if str(a.get("name") or "") == target:
                sha_asset = a
                break
    return bin_asset, sha_asset


def list_firmware_releases(repo: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Fetch available firmware releases from GitHub Releases."""
    target_repo = (repo or DEFAULT_FIRMWARE_REPO).strip()
    url = f"https://api.github.com/repos/{target_repo}/releases?per_page={max(1, min(30, int(limit)))}"
    raw_releases = _http_get_json(url)
    releases: list[dict[str, Any]] = []
    for rel in raw_releases:
        if rel.get("draft"):
            continue
        bin_asset, sha_asset = _pick_firmware_assets(rel.get("assets") or [])
        if bin_asset is None:
            continue
        releases.append(
            {
                "tag": rel.get("tag_name"),
                "name": rel.get("name") or rel.get("tag_name"),
                "published_at": rel.get("published_at"),
                "prerelease": bool(rel.get("prerelease")),
                "notes": (rel.get("body") or "").strip()[:1000],
                "asset_name": bin_asset.get("name"),
                "asset_size_bytes": bin_asset.get("size"),
                "download_url": bin_asset.get("browser_download_url"),
                "sha256_url": sha_asset.get("browser_download_url") if sha_asset else None,
            }
        )
    return {
        "repo": target_repo,
        "count": len(releases),
        "releases": releases,
    }


def download_firmware(
    version: str = "latest",
    repo: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Download and verify a firmware .bin from GitHub Releases into local cache."""
    target_repo = (repo or DEFAULT_FIRMWARE_REPO).strip()
    ver = (version or "latest").strip()
    if ver == "latest":
        rel = _http_get_json(f"https://api.github.com/repos/{target_repo}/releases/latest")
    else:
        rel = _http_get_json(f"https://api.github.com/repos/{target_repo}/releases/tags/{ver}")

    tag = str(rel.get("tag_name") or ver)
    bin_asset, sha_asset = _pick_firmware_assets(rel.get("assets") or [])
    if bin_asset is None:
        raise RuntimeError(f"Release {tag} in {target_repo} has no .bin firmware asset.")

    asset_name = str(bin_asset["name"])
    download_url = str(bin_asset["browser_download_url"])
    cache_dir = get_cache_dir() / target_repo.replace("/", "__") / tag
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_path = cache_dir / asset_name

    expected_sha256: str | None = None
    if sha_asset and sha_asset.get("browser_download_url"):
        sha_path = cache_dir / f"{asset_name}.sha256"
        if force or not sha_path.exists():
            _http_download(str(sha_asset["browser_download_url"]), sha_path, timeout=30.0)
        sha_text = sha_path.read_text(encoding="utf-8").strip()
        if sha_text:
            expected_sha256 = sha_text.split()[0].lower()

    cached = False
    if bin_path.exists() and not force:
        actual = compute_sha256(bin_path)
        if expected_sha256 is None or actual == expected_sha256:
            cached = True

    if not cached:
        _http_download(download_url, bin_path, timeout=180.0)
        actual = compute_sha256(bin_path)
        if expected_sha256 and actual != expected_sha256:
            bin_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch for {asset_name}: expected {expected_sha256}, got {actual}"
            )
    else:
        actual = compute_sha256(bin_path)

    return {
        "repo": target_repo,
        "version": tag,
        "asset_name": asset_name,
        "path": str(bin_path),
        "size_bytes": bin_path.stat().st_size,
        "sha256": actual,
        "sha256_verified": expected_sha256 is not None,
        "cached": cached,
    }


def flash_firmware(
    port: str,
    version: str = "latest",
    repo: str | None = None,
    baud: int = 460800,
    confirm: bool = False,
    local_bin_path: str | None = None,
) -> dict[str, Any]:
    """Flash a verified merged ESP32-C3 firmware image to address 0x0 using esptool."""
    port = (port or "").strip()
    if not port:
        raise ValueError("port is required (call list_passport_devices first to find the serial port).")

    if local_bin_path:
        fw_path = Path(local_bin_path).expanduser().resolve()
        if not fw_path.is_file():
            raise FileNotFoundError(f"Local firmware file not found: {fw_path}")
        fw_info = {
            "repo": "local",
            "version": "local",
            "asset_name": fw_path.name,
            "path": str(fw_path),
            "size_bytes": fw_path.stat().st_size,
            "sha256": compute_sha256(fw_path),
            "sha256_verified": False,
            "cached": True,
        }
    else:
        fw_info = download_firmware(version=version, repo=repo)
        fw_path = Path(fw_info["path"])

    if not confirm:
        return {
            "status": "confirmation_required",
            "message": (
                f"Ready to flash {fw_info['asset_name']} ({fw_info['version']}, "
                f"{fw_info['size_bytes']} bytes) to {port} at 0x0. "
                "Please confirm with the user and call flash_passport_firmware again with confirm=true."
            ),
            "port": port,
            "baud": baud,
            "firmware": fw_info,
        }

    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32c3",
        "--port",
        port,
        "--baud",
        str(int(baud)),
        "write_flash",
        "0x0",
        str(fw_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output_tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-2000:]
    if proc.returncode != 0:
        return {
            "status": "error",
            "returncode": proc.returncode,
            "port": port,
            "firmware": fw_info,
            "message": (
                "esptool failed. If the device did not enter download mode, hold the BOOT button "
                "while reconnecting USB, or verify no other serial monitor holds the port."
            ),
            "output": output_tail,
        }

    return {
        "status": "ok",
        "port": port,
        "firmware": fw_info,
        "message": f"Successfully flashed {fw_info['asset_name']} ({fw_info['version']}) to {port}.",
        "output": output_tail,
    }


def provision_wifi_ble(
    ssid: str,
    password: str = "",
    hub_host: str | None = None,
    hub_port: int = 8765,
    device_name: str = "AI-Passport",
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    """Write 2.4 GHz Wi-Fi credentials and Mac Hub address to AI Passport over BLE."""
    import asyncio
    from passport_hub.ble_protocol import (
        PASSPORT_BLE_CTRL_CHAR_UUID,
        build_wifi_provision_payload,
    )
    from passport_hub.cli import detect_lan_ip

    ssid = (ssid or "").strip()
    if not ssid:
        raise ValueError("ssid is required.")

    host = (hub_host or "").strip() or detect_lan_ip()
    port = int(hub_port or 8765)

    try:
        from bleak import BleakClient, BleakScanner  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Missing 'bleak'. Install with: pip install bleak") from exc

    async def _run() -> dict[str, Any]:
        devices = await BleakScanner.discover(timeout=max(3.0, min(30.0, float(timeout_sec))))
        target = None
        for d in devices:
            if d.name and device_name.lower() in d.name.lower():
                target = d
                break
        if target is None:
            return {
                "status": "not_found",
                "scanned_count": len(devices),
                "message": (
                    f"BLE device '{device_name}' not found (scanned {len(devices)} devices). "
                    "On AI Passport, open SETTINGS -> BLE Pairing Mode and press OK (shows BLE ADV), then retry."
                ),
            }

        payload = build_wifi_provision_payload(ssid, password, host, port)
        async with BleakClient(target) as client:
            await client.write_gatt_char(PASSPORT_BLE_CTRL_CHAR_UUID, payload, response=True)
        return {
            "status": "ok",
            "device_name": target.name,
            "device_address": target.address,
            "ssid": ssid,
            "hub": f"{host}:{port}",
            "message": f"Wi-Fi credentials for '{ssid}' sent to {target.name}. Device will save to NVS and switch to Wi-Fi.",
        }

    return asyncio.run(_run())
