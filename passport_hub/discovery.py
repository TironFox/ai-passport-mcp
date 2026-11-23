"""Zero-config LAN UDP auto-discovery responder for FoloToy AI Passport.

AI Passport broadcasts 'PASSPORT_DISCOVER' to 255.255.255.255:8766 after
joining Wi-Fi. This responder replies directly to the device so the device
learns the Mac's real Wi-Fi LAN IP (immune to VPN TUN interfaces and DHCP changes).
"""
from __future__ import annotations

import json
import socket
import threading

DISCOVERY_UDP_PORT = 8766
DISCOVERY_MAGIC = b"PASSPORT_DISCOVER"


def handle_discovery_packet(data: bytes, http_port: int) -> bytes | None:
    if not data.startswith(DISCOVERY_MAGIC):
        return None
    return json.dumps(
        {"service": "passport-hub", "port": int(http_port)},
        separators=(",", ":"),
    ).encode("utf-8")


class HubDiscoveryResponder:
    def __init__(self, http_port: int = 8765, udp_port: int = DISCOVERY_UDP_PORT) -> None:
        self.http_port = int(http_port)
        self.udp_port = int(udp_port)
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)
        sock.bind(("", self.udp_port))
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="passport-udp-discovery", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            reply = handle_discovery_packet(data, self.http_port)
            if reply is not None:
                try:
                    self._sock.sendto(reply, addr)
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
