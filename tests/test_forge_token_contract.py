from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

import forge_token_contract


class ForgeTokenContractTests(unittest.TestCase):
    def test_agent_token_requires_user_wallet(self) -> None:
        with self.assertRaises(ValueError):
            forge_token_contract.options_for_category("ai_agent", wallet="tmp")

        self.assertEqual(
            forge_token_contract.options_for_category("ai_agent", wallet="user"),
            {"auth": "vendor", "wallet": "user", "category": "ai_agent"},
        )

    def test_game_token_defaults_to_vendor_tmp(self) -> None:
        self.assertEqual(
            forge_token_contract.options_for_category("game"),
            {"auth": "vendor", "wallet": "tmp", "category": "game"},
        )
        self.assertEqual(forge_token_contract.deployment_behavior("vendor", "tmp"), "token_deploy_pool_creation")

    def test_user_wallet_returns_unsigned_pool_tx_behavior(self) -> None:
        self.assertEqual(forge_token_contract.deployment_behavior("client", "user"), "token_deploy_unsigned_pool_tx")
        self.assertTrue(
            forge_token_contract.is_unsigned_tx_result(
                {"tokenAddress": "0x1", "tradeLink": "https://x", "unsignedTx": {}}
            )
        )

    def test_cli_args_preserve_agent_owner_wallet_mode(self) -> None:
        args = forge_token_contract.cli_args(
            name="Hellion",
            symbol="HELL",
            description="Hellion agent token",
            image_url="https://example.com/hellion.png",
            wallet_address="0x931610e795dCDa95E10a3E7D52fd8FFeE1feD8c7",
            category="ai_agent",
            wallet="user",
        )

        self.assertIn("--auth=vendor", args)
        self.assertIn("--wallet=user", args)
        self.assertEqual(args[-1], "ai_agent")


if __name__ == "__main__":
    unittest.main()
