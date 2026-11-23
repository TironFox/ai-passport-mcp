"""Unified CLI entrypoints for passport-mcp and passport-hub."""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from passport_hub.discovery import DISCOVERY_UDP_PORT, HubDiscoveryResponder
from passport_hub.server import PassportHubServer
from passport_hub.watchers import MacClientWatcher

DEFAULT_HUB_URL = os.environ.get("PASSPORT_HUB_URL", "http://127.0.0.1:8765").rstrip("/")


def detect_lan_ip() -> str:
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


def is_hub_alive(hub_url: str = DEFAULT_HUB_URL, timeout: float = 0.8) -> bool:
    try:
        req = urllib.request.Request(f"{hub_url}/api/state", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_hub_running(hub_url: str = DEFAULT_HUB_URL, wait_sec: float = 4.0) -> bool:
    """Start the local Passport Hub daemon in the background if it is not already running."""
    if is_hub_alive(hub_url):
        return True

    log_dir = Path.home() / "Library" / "Logs" / "passport-hub" if sys.platform == "darwin" else Path.home() / ".cache" / "passport-hub"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "hub.log"

    with log_file.open("ab") as fp:
        subprocess.Popen(
            [sys.executable, "-m", "passport_hub.cli", "hub"],
            stdin=subprocess.DEVNULL,
            stdout=fp,
            stderr=fp,
            start_new_session=True,
        )

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if is_hub_alive(hub_url):
            return True
        time.sleep(0.25)
    return False


def run_hub_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="FoloToy AI Passport macOS Local Hub")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--mdns", action="store_true", help="Advertise _passport-hub._tcp via macOS dns-sd")
    args = parser.parse_args(argv)

    mdns_proc = None
    if args.mdns and shutil.which("dns-sd"):
        mdns_proc = subprocess.Popen(
            ["dns-sd", "-R", "PassportHub", "_passport-hub._tcp", "local", str(args.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    server = PassportHubServer((args.host, args.port))
    discovery = HubDiscoveryResponder(http_port=args.port, udp_port=DISCOVERY_UDP_PORT)
    discovery.start()
    watcher = MacClientWatcher(server.store, interval_sec=3.0)
    watcher.sync_once()
    watcher.start()

    lan_ip = detect_lan_ip()
    print(f"[passport-hub] Listening on http://{lan_ip}:{args.port} (SSE push: /api/events)", flush=True)
    print(f"[passport-hub] LAN UDP auto-discovery active on udp/{DISCOVERY_UDP_PORT}", flush=True)
    print(f"[passport-hub] Offline ASR: {server.asr.describe_backend()}", flush=True)
    print(f"[passport-hub] Online clients: {server.store.get_online_clients()}", flush=True)
    print(f"[passport-hub] Discovered tasks: {len(server.store.list_tasks())}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[passport-hub] Shutting down...", file=sys.stderr)
    finally:
        watcher.stop()
        discovery.stop()
        server.server_close()
        if mdns_proc is not None:
            mdns_proc.terminate()


def run_mcp_main() -> None:
    """Entrypoint for `passport-mcp` (auto-starts local Hub daemon unless disabled)."""
    if os.environ.get("PASSPORT_AUTO_START_HUB", "1") != "0":
        try:
            ensure_hub_running(DEFAULT_HUB_URL)
        except Exception:
            # Never block MCP stdio startup if Hub auto-start fails
            pass
    from passport_hub.mcp_server import main as mcp_main

    mcp_main()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "hub":
        run_hub_main(sys.argv[2:])
    else:
        run_mcp_main()


if __name__ == "__main__":
    main()
