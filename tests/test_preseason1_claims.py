from __future__ import annotations

import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from preseason1_claims import (  # noqa: E402
    claim_reached_preseason1_points,
    daily_claim_candidates,
    objective_progress_snapshot,
    stepped_claim_candidates,
)
import claw_runtime  # noqa: E402


class Preseason1ClaimTests(unittest.TestCase):
    def test_progress_snapshot_is_allowlisted_and_identity_free(self) -> None:
        payload = {
            "walletAddress": "0xNEVER-RETAIN-THIS",
            "quests": {
                "off_beaten_path": {
                    "currentLevel": 2,
                    "progress": 17,
                    "nextTarget": 25,
                    "status": "active",
                    "reward": {"token": "secret-ish"},
                }
            },
        }

        self.assertEqual(
            objective_progress_snapshot(payload),
            [{"key": "off_beaten_path", "level": 2, "progress": 17, "target": 25, "status": "active"}],
        )

    def test_progress_snapshot_aggregates_highest_claimed_tier(self) -> None:
        payload = {
            "quests": {
                "survival": {
                    "tiers": [
                        {"tier": 1, "claimed": True},
                        {"tier": 2, "claimed": True},
                        {"tier": 3, "claimable": True},
                        {"tier": 4, "status": "locked"},
                    ]
                }
            }
        }

        self.assertEqual(
            objective_progress_snapshot(payload),
            [{"key": "survival", "level": 2, "claimed": True, "claimable": True, "next_tier": 3, "status": "locked"}],
        )

    def test_discovers_only_explicit_unclaimed_stepped_tiers(self) -> None:
        payload = {
            "quests": {
                "attendance": {
                    "progress": 4,
                    "tiers": [
                        {"tier": 1, "reached": True, "claimed": False},
                        {"tier": 2, "claimable": True},
                        {"tier": 3, "reached": True, "claimed": True},
                        {"tier": 4, "progress": 4},
                    ],
                },
                "kills": {"tiers": [{"tierNumber": "1", "status": "claimable"}]},
            }
        }

        self.assertEqual(
            stepped_claim_candidates(payload),
            [("attendance", 1), ("attendance", 2), ("kills", 1)],
        )

    def test_discovers_only_explicit_unclaimed_daily_quests(self) -> None:
        payload = {
            "dailyQuests": [
                {"key": "daily-kills", "completed": True, "claimed": False},
                {"key": "daily-survive", "status": "ready"},
                {"key": "daily-damage", "reached": True, "claimed": True},
                {"key": "daily-play", "progress": 2},
            ]
        }

        self.assertEqual(daily_claim_candidates(payload), ["daily-kills", "daily-survive"])

    def test_claim_sweep_is_idempotent_and_continues_after_one_error(self) -> None:
        calls: list[tuple[object, ...]] = []

        class FakeClient:
            def claim_preseason1_quest(self, key: str, tier: int) -> dict[str, object]:
                calls.append(("stepped", key, tier))
                if key == "damage":
                    raise RuntimeError("temporary quest error")
                return {"claimed": True}

            def claim_preseason1_daily_quest(self, key: str) -> dict[str, object]:
                calls.append(("daily", key))
                return {"claimed": False}

        result = claim_reached_preseason1_points(
            FakeClient(),
            stepped_payload={
                "quests": [
                    {"key": "attendance", "tiers": [{"tier": 1, "claimable": True}]},
                    {"key": "damage", "tiers": [{"tier": 2, "reached": True}]},
                ]
            },
            daily_payload={"dailyQuests": [{"key": "daily-kills", "completed": True}]},
        )

        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["newly_claimed"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(
            calls,
            [
                ("stepped", "attendance", 1),
                ("stepped", "damage", 2),
                ("daily", "daily-kills"),
            ],
        )

    def test_runtime_sweep_claims_and_reads_fresh_summary(self) -> None:
        calls: list[tuple[object, ...]] = []

        class FakeClient:
            def __init__(self, api_key: str = "", base_url: str = "") -> None:
                calls.append(("init", api_key, base_url))

            def preseason1_quests(self) -> dict[str, object]:
                return {"quests": [{"key": "attendance", "tiers": [{"tier": 1, "claimable": True}]}]}

            def preseason1_daily_quests(self) -> dict[str, object]:
                return {"dailyQuests": [{"key": "daily-play", "completed": True}]}

            def claim_preseason1_quest(self, key: str, tier: int) -> dict[str, object]:
                calls.append(("stepped", key, tier))
                return {"claimed": True}

            def claim_preseason1_daily_quest(self, key: str) -> dict[str, object]:
                calls.append(("daily", key))
                return {"claimed": True}

            def preseason1_summary(self) -> dict[str, object]:
                return {"seasonPoints": 250, "rank": 42}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claw_runtime, "ClawRoyaleClient", FakeClient):
            with mock.patch.dict(
                os.environ,
                {
                    "CERBERUS_MEMORY_DIR": tmp,
                    "CERBERUS_PRESEASON1_AUTO_CLAIM_ENABLED": "true",
                },
                clear=False,
            ):
                report = claw_runtime.run_preseason1_claim_sweep(
                    claw_runtime.ClawRuntimeConfig(api_key="mr_test"),
                    force=True,
                )

        self.assertTrue(report["ok"])
        self.assertEqual(report["newly_claimed"], 2)
        self.assertEqual(report["summary"]["rank"], 42)
        self.assertIn(("stepped", "attendance", 1), calls)
        self.assertIn(("daily", "daily-play"), calls)


if __name__ == "__main__":
    unittest.main()
