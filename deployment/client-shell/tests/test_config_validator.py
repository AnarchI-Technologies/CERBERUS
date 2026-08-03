from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from config_validator import validate_account, validate_agent  # noqa: E402


def write(path: Path, rows: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in rows.items()),
        encoding="utf-8",
    )


class ConfigurationBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "account.runtime"
        self.credentials = self.root / "account.credentials"
        self.config_root = self.root / "configs"
        self.state_root = "/var/lib/cerberus-tenants"
        self.log_root = "/var/log/cerberus-tenants"
        self.account_runtime = {
            "PORT": "12001",
            "CERBERUS_BIND_HOST": "127.0.0.1",
            "CERBERUS_MEMORY_DIR": f"{self.state_root}/account-a/memory",
            "CERBERUS_STATE_DIR": f"{self.state_root}/account-a/state",
            "CERBERUS_LOG_DIR": f"{self.log_root}/account-a",
            "CERBERUS_MEMORY_BACKEND": "sqlite",
            "MONGODB_DATABASE": "",
            "CERBERUS_MONGO_COLLECTION_PREFIX": "",
            "CERBERUS_MANAGEMENT_HOSTNAME": "account-a.management.example",
            "CLAW_ROYALE_RUNTIME_ENABLED": "false",
            "CLAW_ROYALE_GAME_MODE": "free",
            "CLAW_ROYALE_DISABLE_PAID_AUTO_UPGRADE": "true",
        }
        self.account_credentials = {
            "CERBERUS_HTTP_TOKEN": "account_a_http_token_value_abcdefghijklmnopqrstuvwxyz",
            "CERBERUS_PIN": "account-a-pin",
            "MONGODB_URI": "",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def account_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            scope="managed",
            tenant="account-a",
            runtime=self.runtime,
            credentials=self.credentials,
            config_root=self.config_root,
            state_root=self.state_root,
            log_root=self.log_root,
        )

    def test_isolated_managed_sqlite_scope_passes(self) -> None:
        write(self.runtime, self.account_runtime)
        write(self.credentials, self.account_credentials)
        validate_account(self.account_args())

    def test_arbitrary_runtime_key_is_rejected(self) -> None:
        values = dict(self.account_runtime)
        values["CERBERUS_MEMORY_DIR_OVERRIDE"] = "/shared"
        write(self.runtime, values)
        write(self.credentials, self.account_credentials)
        with self.assertRaises(SystemExit):
            validate_account(self.account_args())

    def test_short_or_quoted_account_token_is_rejected(self) -> None:
        write(self.runtime, self.account_runtime)
        short = dict(self.account_credentials)
        short["CERBERUS_HTTP_TOKEN"] = "short-token"
        write(self.credentials, short)
        with self.assertRaises(SystemExit):
            validate_account(self.account_args())

        quoted = dict(self.account_credentials)
        quoted["CERBERUS_HTTP_TOKEN"] = '"account_a_http_token_value_abcdefghijklmnopqrstuvwxyz"'
        write(self.credentials, quoted)
        with self.assertRaises(SystemExit):
            validate_account(self.account_args())

    def test_managed_mongo_is_rejected_without_principal_scope_proof(self) -> None:
        values = dict(self.account_runtime)
        values["CERBERUS_MEMORY_BACKEND"] = "atlas"
        values["MONGODB_DATABASE"] = "cerberus_account_a"
        values["CERBERUS_MONGO_COLLECTION_PREFIX"] = "account_a"
        credentials = dict(self.account_credentials)
        credentials["MONGODB_URI"] = "mongodb+srv://account-a-host/database"
        write(self.runtime, values)
        write(self.credentials, credentials)
        with self.assertRaises(SystemExit):
            validate_account(self.account_args())

    def test_agent_input_cannot_override_protected_memory_path(self) -> None:
        write(self.runtime, self.account_runtime)
        agent_runtime = self.root / "agent.runtime"
        agent_credentials = self.root / "agent.credentials"
        write(
            agent_runtime,
            {
                "CLAWROYALE_PLUGIN_ENABLED": "true",
                "CERBERUS_MEMORY_DIR": "/shared",
            },
        )
        write(agent_credentials, {"CLAW_ROYALE_API_KEY": "agent-owned-value"})
        args = argparse.Namespace(
            runtime=agent_runtime,
            credentials=agent_credentials,
            account_runtime=self.runtime,
            state_dir=f"{self.state_root}/account-a",
            agent="scout",
            installed=False,
        )
        with self.assertRaises(SystemExit):
            validate_agent(args)

    def test_paid_agent_requires_a_well_formed_dedicated_signing_key(self) -> None:
        paid_runtime = dict(self.account_runtime)
        paid_runtime["CLAW_ROYALE_GAME_MODE"] = "onchain"
        write(self.runtime, paid_runtime)
        agent_runtime = self.root / "agent.runtime"
        agent_credentials = self.root / "agent.credentials"
        write(agent_runtime, {"CLAWROYALE_PLUGIN_ENABLED": "true"})
        write(agent_credentials, {"CLAW_ROYALE_API_KEY": "agent-owned-value"})
        args = argparse.Namespace(
            runtime=agent_runtime,
            credentials=agent_credentials,
            account_runtime=self.runtime,
            state_dir=f"{self.state_root}/account-a",
            agent="scout",
            installed=False,
        )
        with self.assertRaises(SystemExit):
            validate_agent(args)

        write(
            agent_credentials,
            {
                "CLAW_ROYALE_API_KEY": "agent-owned-value",
                "CERBERUS_AGENT_EOA_PRIVATE_KEY": "0x" + ("1" * 64),
                "CERBERUS_AGENT_EOA_ADDRESS": "0x" + ("2" * 40),
            },
        )
        validate_agent(args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
