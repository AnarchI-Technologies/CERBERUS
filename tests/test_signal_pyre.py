from __future__ import annotations

import unittest
from datetime import UTC, datetime

from signal_pyre import SignalEnvelope, SignalPyre


class SignalPyreTests(unittest.TestCase):
    def test_publish_delivers_in_global_registration_order(self) -> None:
        pyre = SignalPyre()
        observed: list[str] = []

        pyre.subscribe(
            "battle.finished",
            lambda event: observed.append("first"),
            subscriber_name="first",
        )
        pyre.subscribe(
            "*",
            lambda event: observed.append("wildcard"),
            subscriber_name="wildcard",
        )
        pyre.subscribe(
            "battle.finished",
            lambda event: observed.append("third"),
            subscriber_name="third",
        )

        receipt = pyre.publish(
            "battle.finished",
            {"winner": "hellion"},
            source="cerberus",
        )

        self.assertEqual(observed, ["first", "wildcard", "third"])
        self.assertEqual(receipt.attempted, 3)
        self.assertEqual(receipt.succeeded, 3)
        self.assertEqual(receipt.failed, 0)
        self.assertTrue(receipt.clean)

    def test_subscriber_failure_does_not_block_later_subscribers(self) -> None:
        pyre = SignalPyre()
        observed: list[str] = []

        def fail(_: SignalEnvelope) -> None:
            observed.append("failed")
            raise RuntimeError("controlled failure")

        def survive(_: SignalEnvelope) -> str:
            observed.append("survived")
            return "accepted"

        pyre.subscribe("runtime.checked", fail, subscriber_name="failing")
        pyre.subscribe("runtime.checked", survive, subscriber_name="surviving")

        receipt = pyre.publish(
            "runtime.checked",
            source="doctor",
        )

        self.assertEqual(observed, ["failed", "survived"])
        self.assertEqual(receipt.attempted, 2)
        self.assertEqual(receipt.succeeded, 1)
        self.assertEqual(receipt.failed, 1)
        self.assertFalse(receipt.clean)
        self.assertEqual(
            receipt.deliveries[0].error_type,
            "RuntimeError",
        )
        self.assertEqual(
            receipt.deliveries[0].error_message,
            "controlled failure",
        )
        self.assertEqual(
            receipt.deliveries[1].returned,
            "accepted",
        )

    def test_payload_is_copied_and_immutable(self) -> None:
        original = {"score": 10}
        event = SignalEnvelope(
            name="simulation.completed",
            payload=original,
            source="kiln",
        )

        original["score"] = 99

        self.assertEqual(event.payload["score"], 10)

        with self.assertRaises(TypeError):
            event.payload["score"] = 20  # type: ignore[index]

    def test_unsubscribe_removes_only_requested_subscription(self) -> None:
        pyre = SignalPyre()
        observed: list[str] = []

        first = pyre.subscribe(
            "memory.promoted",
            lambda event: observed.append("first"),
            subscriber_name="first",
        )
        pyre.subscribe(
            "memory.promoted",
            lambda event: observed.append("second"),
            subscriber_name="second",
        )

        self.assertTrue(pyre.unsubscribe(first))
        self.assertFalse(pyre.unsubscribe(first))

        receipt = pyre.publish(
            "memory.promoted",
            source="memory",
        )

        self.assertEqual(observed, ["second"])
        self.assertEqual(receipt.attempted, 1)

    def test_publish_accepts_prebuilt_envelope(self) -> None:
        pyre = SignalPyre()
        captured: list[SignalEnvelope] = []

        pyre.subscribe(
            "deployment.promoted",
            captured.append,
            subscriber_name="ledger",
        )

        envelope = SignalEnvelope(
            name="deployment.promoted",
            payload={"commit": "abc123"},
            source="cerberus",
            correlation_id="deployment-42",
            causation_id="verification-41",
            occurred_at=datetime(2026, 7, 21, 6, 0, tzinfo=UTC),
        )

        receipt = pyre.publish(envelope)

        self.assertIs(receipt.event, envelope)
        self.assertEqual(captured, [envelope])

    def test_publish_without_subscribers_returns_clean_receipt(self) -> None:
        pyre = SignalPyre()

        receipt = pyre.publish(
            "unknown.event",
            source="test",
        )

        self.assertEqual(receipt.attempted, 0)
        self.assertEqual(receipt.succeeded, 0)
        self.assertEqual(receipt.failed, 0)
        self.assertTrue(receipt.clean)

    def test_event_name_validation(self) -> None:
        pyre = SignalPyre()

        with self.assertRaises(ValueError):
            pyre.publish("", source="test")

        with self.assertRaises(ValueError):
            pyre.publish("contains whitespace", source="test")

        with self.assertRaises(ValueError):
            SignalEnvelope(name="*", source="test")

    def test_subscriber_count_supports_event_and_total_counts(self) -> None:
        pyre = SignalPyre()

        pyre.subscribe("one", lambda event: None)
        pyre.subscribe("one", lambda event: None)
        pyre.subscribe("*", lambda event: None)

        self.assertEqual(pyre.subscriber_count("one"), 2)
        self.assertEqual(pyre.subscriber_count("*"), 1)
        self.assertEqual(pyre.subscriber_count(), 3)


if __name__ == "__main__":
    unittest.main()
