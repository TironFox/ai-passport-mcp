from __future__ import annotations

import io
import json
import unittest

from passport_hub.asr import OfflineASR
from passport_hub.ble_protocol import (
    FRAME_TYPE_WIFI_PROV,
    build_wifi_provision_payload,
    decode_frame,
)
from passport_hub.dispatcher import ClientDispatcher
from passport_hub.mcp_server import handle_request
from passport_hub.store import TaskStore
from passport_hub.tts import OfflineTTS


class TestPassportHub(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TaskStore(max_tasks=8)
        self.dispatcher = ClientDispatcher(dry_run=True)
        self.asr = OfflineASR()
        self.tts = OfflineTTS()

    def test_multi_client_queue_and_badge(self) -> None:
        badge = self.store.update_badge({"name": "TIRON", "role": "AI Architect"})
        self.assertEqual(badge.name, "TIRON")

        t1 = self.store.upsert_task(
            source="claude",
            project="AIPassport",
            title="Audio pipeline ready",
            summary="Audio pipeline completed.",
        )
        t2 = self.store.upsert_task(
            source="antigravity",
            project="Hub",
            title="Need confirmation",
            summary="Waiting for user review.",
            status="waiting_input",
        )
        t3 = self.store.upsert_task(
            source="trae",
            project="UI",
            title="Badge layout done",
            summary="Badge desktop screen updated.",
        )
        t4 = self.store.upsert_task(
            source="codex",
            project="Tests",
            title="Host tests pass",
            summary="All host tests green.",
        )
        self.assertEqual(self.store.unread_count(), 4)
        listing = self.store.list_tasks(limit=8)
        self.assertEqual([t.source_tag for t in listing], ["CDX", "TRAE", "AGY", "CLD"])
        self.assertEqual(listing[0].to_device_dict()["id"], t4.task_id)

        self.store.mark_read(t1.task_id)
        self.assertEqual(self.store.unread_count(), 3)

    def test_offline_tts_pcm_and_voice_dispatch(self) -> None:
        task = self.store.upsert_task(
            source="codex",
            project="Demo",
            title="Tests green",
            summary="All tests passed.",
        )
        try:
            pcm = self.tts.synthesize_pcm16("Task completed.")
            self.assertGreater(len(pcm), 1000)
            self.assertEqual(len(pcm) % 2, 0)  # 16-bit PCM alignment
        except RuntimeError as exc:
            # macOS sandbox may block the Speech Synthesis Mach service.
            self.assertIn("empty audio", str(exc))

        silent_pcm = b"\x00\x00" * 8000
        silent_text, silent_engine = self.asr.transcribe_pcm16(silent_pcm)
        self.assertEqual(silent_text, "")
        self.assertEqual(silent_engine, "silence")

        fake_pcm = (b"\x00\x08\x00\xf8") * 4000
        transcript, _engine = self.asr.transcribe_pcm16(fake_pcm)
        self.assertTrue(len(transcript) > 0)

        res = self.dispatcher.dispatch(task, transcript)
        self.assertTrue(res.ok)
        self.store.mark_running(task.task_id, transcript)
        self.assertEqual(self.store.get_task(task.task_id).status, "running")
        self.assertEqual(self.store.unread_count(), 0)

    def test_ble_provision_frame_roundtrip(self) -> None:
        frame = build_wifi_provision_payload("MyWiFi", "secret123", "192.168.1.50", 8765)
        ftype, seq, payload = decode_frame(frame)
        self.assertEqual(ftype, FRAME_TYPE_WIFI_PROV)
        self.assertEqual(seq, 0)
        parsed = json.loads(payload.decode("utf-8"))
        self.assertEqual(parsed["ssid"], "MyWiFi")
        self.assertEqual(parsed["port"], 8765)

    def test_mcp_protocol_handlers(self) -> None:
        init = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "passport-hub-mcp")
        tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = [t["name"] for t in tools["result"]["tools"]]
        self.assertIn("report_task_status", tool_names)
        self.assertIn("ask_passport_user", tool_names)
        self.assertIn("check_passport_reply", tool_names)

    def test_mcp_bidirectional_voice_bridge(self) -> None:
        import threading
        import time

        item = self.store.upsert_task(
            source="antigravity",
            project="AIPassport",
            title="Need Voice",
            summary="请告诉我下一步做什么",
            status="waiting_input",
        )
        received: dict[str, object] = {}

        def waiter() -> None:
            received["reply"] = self.store.wait_for_reply(item.task_id, timeout_sec=3.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        delivered = self.store.submit_reply(
            item.task_id, "把按钮颜色改成蓝色", kind="voice", asr_engine="faster_whisper"
        )
        t.join(timeout=3.0)
        self.assertTrue(delivered)
        self.assertIsNotNone(received.get("reply"))
        reply = received["reply"]
        assert isinstance(reply, dict)
        self.assertEqual(reply["text"], "把按钮颜色改成蓝色")
        self.assertEqual(reply["kind"], "voice")

    def test_udp_lan_discovery(self) -> None:
        import json
        from passport_hub.discovery import DISCOVERY_MAGIC, handle_discovery_packet

        self.assertIsNone(handle_discovery_packet(b"OTHER", 8765))
        reply = handle_discovery_packet(DISCOVERY_MAGIC, 8765)
        self.assertIsNotNone(reply)
        assert reply is not None
        payload = json.loads(reply.decode("utf-8"))
        self.assertEqual(payload["service"], "passport-hub")
        self.assertEqual(payload["port"], 8765)


if __name__ == "__main__":
    unittest.main()
