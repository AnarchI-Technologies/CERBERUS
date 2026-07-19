from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from agent_lab_guard import validate_agent_profile


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def test_disabled_unique_profile_passes_safe_preflight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "scout.env"
        write(profile, "PORT=10002\nCLAW_ROYALE_RUNTIME_ENABLED=false\n")
        report = validate_agent_profile("scout", profile)
    assert report["ok"] is True
    assert report["runtime_enabled"] is False


def test_live_profile_requires_distinct_official_identity_and_no_signing_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "scout.env"
        production = Path(tmp) / "production.env"
        write(production, "CLAW_ROYALE_API_KEY=hellion-key\nCLAW_ROYALE_ERC8004_ID=hellion-id\n")
        write(
            profile,
            "PORT=10002\nCLAW_ROYALE_RUNTIME_ENABLED=true\n"
            "CLAW_ROYALE_API_KEY=hellion-key\nCLAW_ROYALE_ERC8004_ID=scout-id\n"
            "CERBERUS_AGENT_EOA_PRIVATE_KEY=do-not-print\n",
        )
        report = validate_agent_profile("scout", profile, production_path=production, allow_live=True)
    assert report["ok"] is False
    assert "production_identity_reused" in report["errors"]
    assert "signing_key_not_allowed" in report["errors"]
    assert report["duplicate_identity_fields"] == ["CLAW_ROYALE_API_KEY"]
    assert "do-not-print" not in repr(report)


def test_reserved_server_port_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "scout.env"
        write(profile, "PORT=10001\nCLAW_ROYALE_RUNTIME_ENABLED=false\n")
        report = validate_agent_profile("scout", profile)
    assert "reserved_port" in report["errors"]
