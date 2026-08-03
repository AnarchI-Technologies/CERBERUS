from __future__ import annotations

import json
import unittest
from pathlib import Path

import render_app
from spine_protocol import SpineProtocolError, normalize_reach, session_document, snapshot_document


ROOT = Path(__file__).resolve().parents[1]


class SpineProtocolTests(unittest.TestCase):
    def test_session_defaults_to_read_only_and_does_not_trust_surface_for_reach(self) -> None:
        session = session_document(surface="vigil", reach=())

        self.assertEqual(session["schema_version"], "cerberus.spine.v1")
        self.assertEqual(session["system"], "CERBERUS")
        self.assertEqual(session["surface"], "vigil")
        self.assertEqual(session["reach"], ["read-only"])
        self.assertNotIn("extensions.read", session["capabilities"])

    def test_standard_reach_adds_read_inventory_without_elevation(self) -> None:
        session = session_document(surface="helm", reach=("standard",))

        self.assertEqual(session["reach"], ["read-only", "standard"])
        self.assertIn("extensions.read", session["capabilities"])
        self.assertNotIn("elevated", session["reach"])

    def test_unknown_reach_is_rejected(self) -> None:
        with self.assertRaises(SpineProtocolError):
            normalize_reach(("administrator",))

    def test_empty_reach_values_are_ignored(self) -> None:
        self.assertEqual(normalize_reach(("", None)), ("read-only",))

    def test_snapshot_is_bounded_and_excludes_internal_host_state(self) -> None:
        snapshot = snapshot_document(
            {
                "ok": True,
                "current_game_id": "game-1",
                "memory_writable": True,
                "memory_dir": "C:/secret/path",
                "admin_settings": {"trust_private_network_admin": True},
                "env": {"CERBERUS_PIN": True},
                "public_wallets": {"owner_eoa": "0xowner"},
                "claw_runtime": {
                    "state": "playing",
                    "mode": "free",
                    "game_status": "running",
                    "gameplay_ready": True,
                    "can_act": True,
                    "last_frame_type": "agent_view",
                    "last_error": "",
                    "updated_at": 123,
                    "raw_frame": {"private": True},
                },
                "longterm_memory": {"items": 12, "bytes": 4096, "path": "C:/secret/db"},
                "public_thought": "measuring the room",
                "autonomy": {"suggested_edits": 2, "match_evidence": 3, "stale_paid_rooms": 0, "social_queue": 1},
                "launch": {"ok": False, "blockers": ["waiting for heartbeat"]},
            },
            surface="vigil",
            reach=("read-only",),
            observed_at="2026-08-03T00:00:00+00:00",
        )

        encoded = json.dumps(snapshot)
        self.assertEqual(snapshot["runtime"]["current_game_id"], "game-1")
        self.assertEqual(snapshot["memory"], {"items": 12, "bytes": 4096})
        self.assertNotIn("C:/secret", encoded)
        self.assertNotIn("admin_settings", encoded)
        self.assertNotIn("public_wallets", encoded)
        self.assertNotIn("raw_frame", encoded)

    def test_snapshot_tolerates_malformed_internal_counts(self) -> None:
        snapshot = snapshot_document(
            {"longterm_memory": {"items": "unknown", "bytes": object()}},
            surface="vigil",
            reach=(),
        )

        self.assertEqual(snapshot["memory"], {"items": 0, "bytes": 0})

    def test_spine_routes_require_auth_and_remote_sessions_remain_read_only(self) -> None:
        sent = []

        class Handler(render_app.CerberusHandler):
            def __init__(self, path: str, *, authorized: bool, local: bool, surface: str = "vigil"):
                self.path = path
                self.authorized = authorized
                self.local = local
                self.headers = {"X-Cerberus-Surface": surface}

            def _authorized(self) -> bool:
                return self.authorized

            def _request_is_local_trusted(self) -> bool:
                return self.local

            def _send(self, body, *, status=200):  # type: ignore[no-untyped-def]
                sent.append((status, body))

        Handler("/spine/v1/session", authorized=False, local=False).do_GET()
        Handler("/spine/v1/session", authorized=True, local=False).do_GET()
        Handler("/spine/v1/session", authorized=True, local=True, surface="helm").do_GET()

        self.assertEqual(sent[0], (401, {"ok": False, "error": "unauthorized"}))
        self.assertEqual(sent[1][1]["reach"], ["read-only"])
        self.assertEqual(sent[2][1]["reach"], ["read-only", "standard"])

    def test_snapshot_route_uses_bounded_projection(self) -> None:
        sent = []

        class Handler(render_app.CerberusHandler):
            path = "/spine/v1/snapshot"
            headers = {"X-Cerberus-Surface": "vigil"}

            def __init__(self):
                pass

            def _authorized(self) -> bool:
                return True

            def _request_is_local_trusted(self) -> bool:
                return False

            def _send(self, body, *, status=200):  # type: ignore[no-untyped-def]
                sent.append((status, body))

        original_stats = render_app.stats
        try:
            render_app.stats = lambda: {  # type: ignore[assignment]
                "ok": True,
                "memory_writable": True,
                "memory_dir": "C:/never/send",
                "claw_runtime": {},
                "longterm_memory": {},
                "autonomy": {},
                "launch": {},
            }
            Handler().do_GET()
        finally:
            render_app.stats = original_stats  # type: ignore[assignment]

        self.assertEqual(sent[0][0], 200)
        self.assertEqual(sent[0][1]["kind"], "snapshot")
        self.assertNotIn("never/send", json.dumps(sent[0][1]))


if __name__ == "__main__":
    unittest.main()
