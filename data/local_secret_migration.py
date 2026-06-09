"""
Find and encrypt local Cerberus/legacy secret JSON files.

This tool does not delete plaintext by default. Use --remove-plain only after
you have verified that the corresponding .vault.json files decrypt correctly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_system import secret_like_key, stable_hash
from secret_vault import is_vault_envelope, read_vault, write_vault


ROOT = Path(__file__).resolve().parent
KNOWN_SECRET_FILES = [
    ROOT / "dev-agent" / "credentials.json",
    ROOT / "dev-agent" / "agent-wallet.json",
    ROOT / "dev-agent" / "owner-wallet.json",
    Path.home() / ".molty-royale" / "credentials.json",
    Path.home() / ".molty-royale" / "agent-wallet.json",
    Path.home() / ".molty-royale" / "owner-wallet.json",
]
DEFAULT_IMPORTED_SECRETS_VAULT = Path.home() / ".cerberus" / "imported_parts_secrets.vault.json"
SECRET_VALUE_PATTERNS = {
    "evm_private_key": re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{64}(?![A-Za-z0-9])"),
    "bare_private_key": re.compile(r"(?<![A-Za-z0-9])[a-fA-F0-9]{64}(?![A-Za-z0-9])"),
    "mr_live_api_key": re.compile(r"mr_live_[A-Za-z0-9_-]{12,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "mongodb_uri_secret": re.compile(r"mongodb(?:\\+srv)?://[^\\s:@/]+:[^\\s@/]+@[^\\s]+", re.I),
    "jwt_like": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}"),
}
PLACEHOLDER_BITS = (
    "xxxxxxxx",
    "xxxx",
    "your_",
    "your",
    "placeholder",
    "example",
    "dummy",
    "<",
    ">",
    "...",
)
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def json_has_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if secret_like_key(str(key)):
                return True
            if json_has_secret(item):
                return True
    elif isinstance(value, list):
        return any(json_has_secret(item) for item in value)
    return False


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def looks_placeholder(value: str) -> bool:
    lower = value.lower()
    if lower in {"true", "false", "0", "1", "none", "null"}:
        return True
    return any(bit in lower for bit in PLACEHOLDER_BITS)


def iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def scan_file_for_values(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    rel = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for kind, pattern in SECRET_VALUE_PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(0)
            if looks_placeholder(value):
                continue
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "file": rel,
                    "absolute_file": str(path),
                    "kind": kind,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "hash": stable_hash(value, length=16),
                    "length": len(value),
                    "value": value,
                }
            )

    # JSON/env-style key fallback for secrets that do not match a known shape.
    if path.suffix.lower() == ".json":
        try:
            obj = json.loads(text)
            for key_path, value in iter_json_secret_values(obj):
                value_str = str(value).strip()
                if not value_str or looks_placeholder(value_str):
                    continue
                key = ("json_secret_key", value_str)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "file": rel,
                        "absolute_file": str(path),
                        "kind": "json_secret_key",
                        "key": key_path,
                        "hash": stable_hash(value_str, length=16),
                        "length": len(value_str),
                        "value": value_str,
                    }
                )
        except Exception:
            pass

    if path.name.lower().startswith(".env") or path.suffix.lower() in {".env", ".toml", ".yml", ".yaml"}:
        for line_no, line in enumerate(text.splitlines(), 1):
            if "=" not in line or line.strip().startswith("#"):
                continue
            key, raw_value = line.split("=", 1)
            value = raw_value.split("#", 1)[0].strip().strip("\"'")
            if not secret_like_key(key) or not value or looks_placeholder(value):
                continue
            pair = ("env_secret_key", value)
            if pair in seen:
                continue
            seen.add(pair)
            hits.append(
                {
                    "file": rel,
                    "absolute_file": str(path),
                    "kind": "env_secret_key",
                    "key": key.strip(),
                    "line": line_no,
                    "hash": stable_hash(value, length=16),
                    "length": len(value),
                    "value": value,
                }
            )

    return hits


def iter_json_secret_values(value: Any, key_path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{key_path}.{key}" if key_path else str(key)
            if secret_like_key(str(key)) and not isinstance(item, (dict, list)):
                yield child_path, item
            yield from iter_json_secret_values(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_json_secret_values(item, f"{key_path}[{index}]")


def scan_tree(root: Path) -> list[dict[str, Any]]:
    files = iter_files(root)
    hits: list[dict[str, Any]] = []
    for path in files:
        hits.extend(scan_file_for_values(path, root=root if root.is_dir() else root.parent))
    return hits


def public_hit_summary(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: hit[key]
            for key in ("file", "kind", "line", "key", "hash", "length")
            if key in hit
        }
        for hit in hits
    ]


def sanitize_tree(root: Path, *, vault_path: Path = DEFAULT_IMPORTED_SECRETS_VAULT) -> dict[str, Any]:
    hits = scan_tree(root)
    if not hits:
        return {
            "status": "no_secrets_found",
            "root": str(root),
            "vault_path": str(vault_path),
            "hits": [],
        }

    secret_records = [
        {
            "file": hit["file"],
            "kind": hit["kind"],
            "line": hit.get("line"),
            "key": hit.get("key"),
            "hash": hit["hash"],
            "length": hit["length"],
            "value": hit["value"],
        }
        for hit in hits
    ]
    vault_data = {
        "type": "cerberus.imported_parts_secrets",
        "created_at": utc_now(),
        "source_root": str(root),
        "records": secret_records,
    }
    write_vault(vault_path, vault_data, purpose="cerberus.imported_parts_secrets")

    verified = read_vault(vault_path)
    verified_hashes = {record["hash"] for record in verified.get("records", [])}
    expected_hashes = {record["hash"] for record in secret_records}
    if verified_hashes != expected_hashes:
        raise RuntimeError("Vault verification failed; source files were not changed.")

    by_file: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        by_file.setdefault(hit["absolute_file"], []).append(hit)

    redacted_files = []
    for file_name, file_hits in by_file.items():
        path = Path(file_name)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for hit in sorted(file_hits, key=lambda item: len(item["value"]), reverse=True):
            marker = f"[vault:{hit['kind']}:{hit['hash']}]"
            text = text.replace(hit["value"], marker)
        path.write_text(text, encoding="utf-8")
        redacted_files.append(str(path))

    return {
        "status": "vaulted_and_redacted",
        "root": str(root),
        "vault_path": str(vault_path),
        "secret_count": len(hits),
        "redacted_files": redacted_files,
        "hits": public_hit_summary(hits),
    }


def scan_secret_files(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        row: dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "vault_exists": path.with_suffix(path.suffix + ".vault.json").exists(),
        }
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                row["is_vault"] = is_vault_envelope(data)
                row["has_secret_keys"] = json_has_secret(data)
                row["sha256"] = stable_hash(data, length=24)
            except Exception as exc:
                row["error"] = str(exc)
        rows.append(row)
    return rows


def migrate_secret_files(paths: list[Path], *, remove_plain: bool = False) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        if not path.exists():
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        if is_vault_envelope(data):
            rows.append({"path": str(path), "status": "already_vault"})
            continue
        if not json_has_secret(data):
            rows.append({"path": str(path), "status": "no_secret_keys"})
            continue

        vault_path = path.with_suffix(path.suffix + ".vault.json")
        write_vault(vault_path, data, purpose=f"local_secret:{path.name}")
        verified = read_vault(vault_path)
        ok = stable_hash(verified, length=24) == stable_hash(data, length=24)

        if remove_plain and ok:
            path.unlink()
            status = "vault_written_plain_removed"
        else:
            status = "vault_written_plain_kept"

        rows.append(
            {
                "path": str(path),
                "vault_path": str(vault_path),
                "status": status,
                "verified": ok,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypt local secret JSON files")
    parser.add_argument("command", choices=["scan", "migrate", "scan-tree", "sanitize-tree"])
    parser.add_argument("--remove-plain", action="store_true")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--root", default="")
    parser.add_argument("--vault-path", default=str(DEFAULT_IMPORTED_SECRETS_VAULT))
    args = parser.parse_args()

    if args.command == "scan-tree":
        root = Path(args.root or ".").resolve()
        print(json.dumps(public_hit_summary(scan_tree(root)), ensure_ascii=True, indent=2))
        return 0

    if args.command == "sanitize-tree":
        root = Path(args.root or ".").resolve()
        result = sanitize_tree(root, vault_path=Path(args.vault_path))
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0

    paths = [Path(p) for p in args.path] if args.path else KNOWN_SECRET_FILES
    if args.command == "scan":
        print(json.dumps(scan_secret_files(paths), ensure_ascii=True, indent=2))
        return 0

    rows = migrate_secret_files(paths, remove_plain=args.remove_plain)
    print(json.dumps(rows, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
