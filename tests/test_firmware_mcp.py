from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from passport_hub import firmware, mcp_server


class FirmwareMcpTests(unittest.TestCase):
    def test_tools_list_includes_firmware_tools(self) -> None:
        resp = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp is not None
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertTrue(
            {
                "report_task_status",
                "ask_passport_user",
                "check_passport_reply",
                "list_passport_devices",
                "list_firmware_releases",
                "flash_passport_firmware",
            }.issubset(names)
        )

    def test_flash_requires_confirmation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "FoloToy-AI-Passport-full.bin"
            fake_bin.write_bytes(b"ESP32C3_TEST_IMAGE")
            resp = mcp_server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "flash_passport_firmware",
                        "arguments": {
                            "port": "/dev/cu.usbmodem1101",
                            "local_bin_path": str(fake_bin),
                        },
                    },
                }
            )
            assert resp is not None
            self.assertFalse(resp["result"]["isError"])
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(payload["status"], "confirmation_required")
            self.assertEqual(payload["port"], "/dev/cu.usbmodem1101")
            self.assertEqual(payload["firmware"]["sha256"], firmware.compute_sha256(fake_bin))

    def test_list_passport_devices_returns_list(self) -> None:
        with mock.patch("passport_hub.firmware.list_serial_devices", return_value=[{"port": "/dev/cu.usbmodem1", "recommended": True}]):
            resp = mcp_server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_passport_devices", "arguments": {}},
                }
            )
            assert resp is not None
            self.assertFalse(resp["result"]["isError"])
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(len(payload["devices"]), 1)


if __name__ == "__main__":
    unittest.main()
