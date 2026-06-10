"""Environment loading helpers for local launch commands.

PowerShell only sees persistent User/Machine environment changes after a new
session starts. These helpers let Python commands read those persistent Windows
values directly, while still preferring the current process and `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv_file(path: str | Path | None = None) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(path or ROOT / ".env")
    except ImportError:
        _load_simple_dotenv(Path(path or ROOT / ".env"))


def _load_simple_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def windows_persistent_env(name: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    locations = [
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ]
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                if value:
                    return os.path.expandvars(str(value))
        except OSError:
            continue
    return ""


def env_value(name: str, default: str = "") -> str:
    load_dotenv_file()
    return os.getenv(name) or windows_persistent_env(name) or default


def hydrate_env(names: Iterable[str]) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for name in names:
        value = env_value(name)
        if value:
            os.environ.setdefault(name, value)
            loaded[name] = value
    return loaded


def env_report(names: Iterable[str]) -> list[dict[str, str | bool]]:
    load_dotenv_file()
    report = []
    for name in names:
        source = ""
        value = os.getenv(name)
        if value:
            source = "process_or_dotenv"
        else:
            value = windows_persistent_env(name)
            if value:
                source = "windows_persistent"
        report.append({"name": name, "set": bool(value), "source": source})
    return report
