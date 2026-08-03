from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import social_runtime


class FailingClient:
    def post_draft(self, draft):  # type: ignore[no-untyped-def]
        return {"ok": False, "skipped": False, "reason": "temporary", "draft": draft}

    def follow(self, effect):  # type: ignore[no-untyped-def]
        return {"ok": False, "skipped": False, "reason": "temporary", "effect": effect}


class SocialRuntimeReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        self.old_autodrain = os.environ.get("CERBERUS_SOCIAL_AUTODRAIN_ON_ENQUEUE")
        os.environ["CERBERUS_MEMORY_DIR"] = self.temp_dir.name
        os.environ["CERBERUS_SOCIAL_AUTODRAIN_ON_ENQUEUE"] = "false"

    def tearDown(self) -> None:
        if self.old_memory_dir is None:
            os.environ.pop("CERBERUS_MEMORY_DIR", None)
        else:
            os.environ["CERBERUS_MEMORY_DIR"] = self.old_memory_dir
        if self.old_autodrain is None:
            os.environ.pop("CERBERUS_SOCIAL_AUTODRAIN_ON_ENQUEUE", None)
        else:
            os.environ["CERBERUS_SOCIAL_AUTODRAIN_ON_ENQUEUE"] = self.old_autodrain
        self.temp_dir.cleanup()

    def enqueue(self) -> None:
        social_runtime.enqueue_social_effects(
            [{"type": "moltybook_draft", "category": "test", "content": "retry me"}]
        )

    def test_transient_failures_are_requeued_until_attempt_limit(self) -> None:
        self.enqueue()

        for expected_attempt in (1, 2):
            result = social_runtime.drain_social_queue_once(client=FailingClient(), max_items=1)
            item = social_runtime.social_queue()[0]
            self.assertTrue(result["ok"])
            self.assertEqual(item["attempts"], expected_attempt)
            self.assertEqual(item["status"], "queued")

        social_runtime.drain_social_queue_once(client=FailingClient(), max_items=1)
        item = social_runtime.social_queue()[0]
        self.assertEqual(item["attempts"], 3)
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["last_result"]["reason"], "temporary")

    def test_enqueue_surfaces_failed_persistence(self) -> None:
        with mock.patch.object(social_runtime, "write_json", return_value=False):
            with self.assertRaisesRegex(OSError, "social queue write failed"):
                self.enqueue()

    def test_drain_reports_failed_persistence(self) -> None:
        self.enqueue()
        with mock.patch.object(social_runtime, "write_json", return_value=False):
            result = social_runtime.drain_social_queue_once(client=FailingClient(), max_items=1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["error"], "social queue write failed")


if __name__ == "__main__":
    unittest.main()
