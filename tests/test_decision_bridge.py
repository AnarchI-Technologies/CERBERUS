from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

import decision_bridge


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200, content: bytes = b"{}") -> None:
        self.payload = payload
        self.status_code = status_code
        self.content = content

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.trust_env = True
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


class DecisionBridgeTests(unittest.TestCase):
    def test_unset_url_preserves_existing_in_process_decider(self) -> None:
        state = {"turn": 4}
        expected = {"type": "move", "regionId": "r2"}
        local = mock.Mock(return_value=expected)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(decision_bridge.decide_action(state, in_process=local), expected)
        local.assert_called_once_with(state)

    def test_remote_bridge_sanitizes_state_and_sends_bearer_without_proxy_inheritance(self) -> None:
        response = FakeResponse({"ok": True, "action": {"type": "move", "regionId": "r2", "reason": "rotate"}})
        session = FakeSession(response)
        state = {
            "gameId": "g1",
            "apiKey": "do-not-send",
            "token": "do-not-send",
            "walletPrivateKeyHex": "do-not-send",
            "agentTokenAddress": "public-address",
            "view": {
                "self": {"id": "me"},
                "authorization": "do-not-send",
                "nested": [{"private_key": "do-not-send", "safe": True}],
            },
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    decision_bridge.BRIDGE_URL_ENV: "http://127.0.0.1:8000/tick",
                    decision_bridge.BRIDGE_TOKEN_ENV: "client-supplied-token",
                },
                clear=True,
            ),
            mock.patch.object(decision_bridge.requests, "Session", return_value=session),
        ):
            action = decision_bridge.decide_action(state, in_process=mock.Mock())

        self.assertEqual(action, {"type": "move", "regionId": "r2", "reason": "rotate"})
        self.assertFalse(session.trust_env)
        self.assertTrue(session.closed)
        call = session.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:8000/tick")
        self.assertEqual(call["headers"], {"Authorization": "Bearer client-supplied-token"})
        self.assertFalse(call["allow_redirects"])
        sent = call["json"]
        self.assertNotIn("apiKey", sent["state"])
        self.assertNotIn("token", sent["state"])
        self.assertNotIn("walletPrivateKeyHex", sent["state"])
        self.assertEqual(sent["state"]["agentTokenAddress"], "public-address")
        self.assertNotIn("authorization", sent["state"]["view"])
        self.assertNotIn("private_key", sent["state"]["view"]["nested"][0])

    def test_configured_bridge_fails_closed_without_using_local_core(self) -> None:
        local = mock.Mock(return_value={"type": "attack", "targetId": "unsafe"})
        with mock.patch.dict(
            os.environ,
            {
                decision_bridge.BRIDGE_URL_ENV: "https://example.com/tick",
                decision_bridge.BRIDGE_TOKEN_ENV: "secret-value",
            },
            clear=True,
        ):
            action = decision_bridge.decide_action({"gameId": "g1"}, in_process=local)
        self.assertEqual(action["type"], "rest")
        self.assertNotIn("secret-value", action["reason"])
        local.assert_not_called()

    def test_missing_token_and_transport_errors_fail_closed_without_leaking_details(self) -> None:
        local = mock.Mock()
        with mock.patch.dict(
            os.environ,
            {decision_bridge.BRIDGE_URL_ENV: "http://localhost:8000/tick"},
            clear=True,
        ):
            missing_token = decision_bridge.decide_action({}, in_process=local)
        self.assertEqual(missing_token["type"], "rest")

        session = FakeSession(RuntimeError("request exposed super-secret-token"))
        with (
            mock.patch.dict(
                os.environ,
                {
                    decision_bridge.BRIDGE_URL_ENV: "http://[::1]:8000/tick",
                    decision_bridge.BRIDGE_TOKEN_ENV: "super-secret-token",
                },
                clear=True,
            ),
            mock.patch.object(decision_bridge.requests, "Session", return_value=session),
        ):
            failed_request = decision_bridge.decide_action({}, in_process=local)
        self.assertEqual(failed_request["type"], "rest")
        self.assertNotIn("super-secret-token", failed_request["reason"])
        local.assert_not_called()

    def test_invalid_remote_actions_fail_closed(self) -> None:
        invalid_actions = [
            None,
            {"type": "launch_missiles"},
            {"type": "move"},
            {"type": "move", "regionId": "r2", "command": "unsupported"},
            {
                "type": "rest",
                "_side_effects": [
                    {"type": "game_free_action", "action": {"type": "attack", "targetId": "rival"}}
                ],
            },
        ]
        for invalid in invalid_actions:
            with self.subTest(action=invalid):
                session = FakeSession(FakeResponse({"ok": True, "action": invalid}))
                with (
                    mock.patch.dict(
                        os.environ,
                        {
                            decision_bridge.BRIDGE_URL_ENV: "http://127.0.0.1:8000/tick",
                            decision_bridge.BRIDGE_TOKEN_ENV: "fixture",
                        },
                        clear=True,
                    ),
                    mock.patch.object(decision_bridge.requests, "Session", return_value=session),
                ):
                    action = decision_bridge.decide_action({}, in_process=mock.Mock())
                self.assertEqual(action["type"], "rest")

    def test_valid_free_action_side_effects_are_narrowed_and_preserved(self) -> None:
        value = {
            "type": "rest",
            "_warnings": [{"error": "not forwarded"}],
            "_side_effects": [
                {"type": "moltybook_draft", "content": "not a provider action"},
                {
                    "type": "game_free_action",
                    "action": {"type": "whisper", "targetId": "ally", "message": "hold", "_private": "drop"},
                    "reason": "coordination",
                },
            ],
        }
        self.assertEqual(
            decision_bridge.validate_action_object(value),
            {
                "type": "rest",
                "_side_effects": [
                    {
                        "type": "game_free_action",
                        "action": {"type": "whisper", "targetId": "ally", "message": "hold"},
                        "reason": "coordination",
                    }
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
