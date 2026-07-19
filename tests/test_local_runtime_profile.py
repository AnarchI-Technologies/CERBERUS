from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import render_app
import runtime_state


class LocalRuntimeProfileTests(unittest.TestCase):
    def test_agent_identity_defaults_to_sanitized_environment_profile(self) -> None:
        with mock.patch.dict("os.environ", {"CERBERUS_RUNTIME_AGENT_ID": " Scout Two! "}, clear=False):
            self.assertEqual(runtime_state.runtime_agent_id(), "scouttwo")
            self.assertEqual(runtime_state.current_game_file().name, "current_game_scouttwo.json")

    def test_live_agent_template_uses_release_and_forces_isolated_state(self) -> None:
        service = (ROOT / "deployment" / "local-linux" / "cerberus-agent-lab@.service").read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=/opt/cerberus-current", service)
        self.assertIn("CERBERUS_MEMORY_DIR=/var/lib/cerberus/agents/%i", service)
        self.assertIn("StateDirectory=cerberus/agents/%i", service)
        self.assertIn("CERBERUS_BIND_HOST=127.0.0.1", service)
        self.assertIn("CERBERUS_MODEL_GATEWAY_ENABLED=false", service)
        self.assertNotIn("CERBERUS/src/render_app.py", service)

    def test_render_app_honors_explicit_local_bind_host(self) -> None:
        server = mock.Mock()
        with mock.patch.dict("os.environ", {"CERBERUS_BIND_HOST": "127.0.0.1", "PORT": "18443", "CLAW_ROYALE_RUNTIME_ENABLED": "false"}, clear=False), mock.patch.object(render_app, "ThreadingHTTPServer", return_value=server) as factory:
            render_app.main()

        factory.assert_called_once_with(("127.0.0.1", 18443), render_app.CerberusHandler)
        server.serve_forever.assert_called_once_with()

    def test_local_launcher_forces_loopback_and_deterministic_model_mode(self) -> None:
        launcher = (ROOT / "deployment" / "local-windows" / "start-cerberus.ps1").read_text(encoding="utf-8")
        self.assertIn("CERBERUS_BIND_HOST = '127.0.0.1'", launcher)
        self.assertIn("CERBERUS_MODEL_GATEWAY_ENABLED = 'false'", launcher)
        self.assertNotIn("render.com", launcher.lower())
        self.assertNotIn("railway", launcher.lower())

    def test_installer_does_not_embed_secrets_in_scheduled_task(self) -> None:
        installer = (ROOT / "deployment" / "local-windows" / "install-local-runtime.ps1").read_text(encoding="utf-8")
        for secret in ("CLAW_ROYALE_API_KEY", "PRIVATE_KEY", "MONGODB_URI", "CERBERUS_PIN"):
            self.assertNotIn(secret, installer)
        self.assertIn("-RestartCount 999", installer)
        self.assertIn("-MultipleInstances IgnoreNew", installer)
        self.assertIn("-AllowStartIfOnBatteries", installer)
        self.assertIn("-DontStopIfGoingOnBatteries", installer)


if __name__ == "__main__":
    unittest.main()
