#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys

from passport_hub.discovery import DISCOVERY_UDP_PORT, HubDiscoveryResponder
from passport_hub.server import PassportHubServer
from passport_hub.watchers import MacClientWatcher


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


def main() -> None:
    parser = argparse.ArgumentParser(description="FoloToy AI Passport macOS Local Hub")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--mdns", action="store_true", help="Advertise _passport-hub._tcp via macOS dns-sd")
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
