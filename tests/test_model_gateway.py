from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from model_gateway import GatewayDisabled, GatewayValidationError, OllamaModelGateway


SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["runtime", "combat"]},
        "confidence": {"type": "number"},
    },
    "required": ["category", "confidence"],
    "additionalProperties": False,
}


def alias_file(tmp: str, *, status: str = "production") -> Path:
    path = Path(tmp) / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "aliases": {
                    "cerberus-fast": {
                        "model": "local:test",
                        "digest": "abc123",
                        "status": status,
                        "tasks": ["classification"],
                        "deadline_seconds": 2,
                        "context_limit": 1024,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


class ModelGatewayTests(unittest.TestCase):
    def test_disabled_by_default_and_requests_deterministic_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            gateway = OllamaModelGateway(aliases_path=alias_file(tmp), transport=lambda *_: {})
            self.assertEqual(gateway.health()["mode"], "deterministic")
            with self.assertRaises(GatewayDisabled):
                gateway.propose(
                    alias="cerberus-fast", prompt_version="v1", task="classification",
                    context={"event": "cooldown"}, output_schema=SCHEMA,
                )

    def test_rejects_non_loopback_endpoint(self) -> None:
        with self.assertRaises(GatewayValidationError):
            OllamaModelGateway(endpoint="http://example.com:11434")

    def test_kill_switch_overrides_enabled_gateway(self) -> None:
        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true", "CERBERUS_INFERENCE_KILL_SWITCH": "true"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(OllamaModelGateway(aliases_path=alias_file(tmp)).enabled())

    def test_rejects_secret_like_context_before_transport(self) -> None:
        called = []
        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            gateway = OllamaModelGateway(aliases_path=alias_file(tmp), transport=lambda *args: called.append(args))
            with self.assertRaises(GatewayValidationError):
                gateway.propose(
                    alias="cerberus-fast", prompt_version="v1", task="classification",
                    context={"private_key": "never"}, output_schema=SCHEMA,
                )
        self.assertEqual(called, [])

    def test_evaluation_alias_requires_explicit_opt_in(self) -> None:
        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            gateway = OllamaModelGateway(aliases_path=alias_file(tmp, status="evaluation_only"), transport=lambda *_: {})
            with self.assertRaises(GatewayValidationError):
                gateway.propose(
                    alias="cerberus-fast", prompt_version="v1", task="classification",
                    context={}, output_schema=SCHEMA,
                )

    def test_validates_structured_output_and_records_provenance(self) -> None:
        requests = []
        def transport(path, payload, timeout):
            requests.append((path, payload, timeout))
            return {"response": '{"category":"runtime","confidence":0.9}', "prompt_eval_count": 20, "eval_count": 9}

        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            proposal = OllamaModelGateway(aliases_path=alias_file(tmp), transport=transport).propose(
                alias="cerberus-fast", prompt_version="classify-v1", task="classification",
                context={"event": "cooldown"}, output_schema=SCHEMA,
            )

        self.assertEqual(proposal.output["category"], "runtime")
        self.assertEqual(proposal.model, "local:test")
        self.assertEqual(proposal.digest, "abc123")
        self.assertEqual(requests[0][0], "/api/generate")
        self.assertNotIn("tools", requests[0][1])

    def test_readiness_requires_installed_pinned_alias_digests(self) -> None:
        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            aliases = alias_file(tmp)
            ready = OllamaModelGateway(
                aliases_path=aliases,
                health_transport=lambda path, timeout: (
                    {"version": "0.32.1"}
                    if path == "/api/version"
                    else {"models": [{"name": "local:test", "digest": "abc123"}]}
                ),
            ).readiness()
            mismatch = OllamaModelGateway(
                aliases_path=aliases,
                health_transport=lambda path, timeout: (
                    {"version": "0.32.1"}
                    if path == "/api/version"
                    else {"models": [{"name": "local:test", "digest": "different"}]}
                ),
            ).readiness()

        self.assertTrue(ready["aliases_ready"])
        self.assertEqual(mismatch["digest_mismatches"], ["cerberus-fast"])
        self.assertEqual(mismatch["mode"], "model_available")

    def test_rejects_unknown_output_fields(self) -> None:
        env = {"CERBERUS_MODEL_GATEWAY_ENABLED": "true"}
        response = {"response": '{"category":"runtime","confidence":0.9,"execute":true}'}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            gateway = OllamaModelGateway(aliases_path=alias_file(tmp), transport=lambda *_: response)
            with self.assertRaises(GatewayValidationError):
                gateway.propose(
                    alias="cerberus-fast", prompt_version="v1", task="classification",
                    context={}, output_schema=SCHEMA,
                )


if __name__ == "__main__":
    unittest.main()

