"""Apply identity vault values to the local User environment without printing secrets."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import IdentityVault  # noqa: E402
from render_env_export import render_env  # noqa: E402


def set_windows_user_env(values: dict[str, str]) -> list[str]:
    if os.name != "nt":
        raise RuntimeError("Persistent User env apply is currently implemented for Windows only.")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
            os.environ[name] = value
    return sorted(values)


def apply_identity_env(*, include_empty: bool = False) -> list[str]:
    values = render_env(IdentityVault().load().data)
    if not include_empty:
        values = {key: value for key, value in values.items() if value}
    return set_windows_user_env(values)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Apply Cerberus identity env vars to Windows User env")
    parser.add_argument("--include-empty", action="store_true", help="Also set empty values from the identity export")
    args = parser.parse_args()
    names = apply_identity_env(include_empty=args.include_empty)
    print("updated:")
    for name in names:
        print(f"- {name}")
    print("Open a new PowerShell session or hydrate env in-process for existing shells.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
