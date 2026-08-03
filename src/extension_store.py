"""Catalog-backed, path-confined storage for Cerberus plugins and adapters."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import requests

from runtime_state import memory_dir


MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 5_000
GITHUB_API_VERSION = "2026-03-10"
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    extension_id: str
    name: str
    kind: str
    repository: str
    ref: str = "v0.1.0"

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.extension_id):
            raise ValueError("invalid catalog extension id")
        if self.kind not in {"plugin", "adapter"}:
            raise ValueError("extension kind must be plugin or adapter")
        owner, separator, repository = self.repository.partition("/")
        if not separator or not _SAFE_ID.fullmatch(owner.lower()) or not _SAFE_ID.fullmatch(repository.lower()):
            raise ValueError("repository must be an owner/name pair")
        if not self.ref or any(character in self.ref for character in "\\?#"):
            raise ValueError("invalid catalog ref")

    @property
    def folder_name(self) -> str:
        return self.repository.partition("/")[2]

    @property
    def archive_url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/zipball/{self.ref}"


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry("children-of-titan", "Children of Titan", "plugin", "AnarchI-Technologies/cerberus-children-of-titan-plugin", "65fa11b8a07e67eee2bc28c5e234d1a2b56b387d"),
    CatalogEntry("openapi-generator", "OpenAPI Generator", "adapter", "AnarchI-Technologies/cerberus-openapi-adapter-generator"),
    CatalogEntry("event-stream", "Universal Event Stream", "adapter", "AnarchI-Technologies/cerberus-event-stream-adapter"),
    CatalogEntry("mcp", "MCP", "adapter", "AnarchI-Technologies/cerberus-mcp-adapter"),
    CatalogEntry("local-process-container", "Local Process / Container", "adapter", "AnarchI-Technologies/cerberus-process-container-adapter"),
    CatalogEntry("graphql", "GraphQL", "adapter", "AnarchI-Technologies/cerberus-graphql-adapter"),
    CatalogEntry("sql-read-model", "SQL / Read Model", "adapter", "AnarchI-Technologies/cerberus-sql-read-model-adapter"),
    CatalogEntry("evm", "EVM", "adapter", "AnarchI-Technologies/cerberus-evm-adapter"),
    CatalogEntry("starknet", "Starknet", "adapter", "AnarchI-Technologies/cerberus-starknet-adapter"),
    CatalogEntry("sui-object", "Sui Object", "adapter", "AnarchI-Technologies/cerberus-sui-object-adapter"),
    CatalogEntry("solana-program", "Solana Program", "adapter", "AnarchI-Technologies/cerberus-solana-program-adapter"),
    CatalogEntry("pettingzoo-gymnasium", "PettingZoo / Gymnasium", "adapter", "AnarchI-Technologies/cerberus-pettingzoo-gymnasium-adapter"),
)


class ExtensionStore:
    def __init__(self, root: str | Path | None = None, *, catalog: Iterable[CatalogEntry] = CATALOG) -> None:
        configured = root or os.getenv("CERBERUS_EXTENSION_DIR")
        self.root = Path(configured) if configured else memory_dir() / "extensions"
        self.root = self.root.expanduser().absolute()
        entries = tuple(catalog)
        self.catalog = {entry.extension_id: entry for entry in entries}
        if len(self.catalog) != len(entries):
            raise ValueError("duplicate catalog extension id")

    def directory(self, kind: str) -> Path:
        if kind not in {"plugin", "adapter"}:
            raise ValueError("unknown extension kind")
        return self.root / ("plugins" if kind == "plugin" else "adapters")

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("extension root cannot be a symlink")
        for kind in ("plugin", "adapter"):
            target = self.directory(kind)
            target.mkdir(exist_ok=True)
            if target.is_symlink():
                raise ValueError(f"{kind} directory cannot be a symlink")
            self._require_inside(target, self.root)

    def inventory(self) -> dict[str, Any]:
        self.ensure_layout()
        installed = {kind: self._installed(kind) for kind in ("plugin", "adapter")}
        installed_names = {kind: {item["folder"] for item in values} for kind, values in installed.items()}
        available = {"plugin": [], "adapter": []}
        for entry in self.catalog.values():
            available[entry.kind].append(
                {
                    "id": entry.extension_id,
                    "name": entry.name,
                    "repository": entry.repository,
                    "ref": entry.ref,
                    "folder": entry.folder_name,
                    "installed": entry.folder_name in installed_names[entry.kind],
                }
            )
        for values in available.values():
            values.sort(key=lambda item: item["name"].casefold())
        return {
            "ok": True,
            "root": str(self.root),
            "directories": {kind + "s": str(self.directory(kind)) for kind in ("plugin", "adapter")},
            "github_token_configured": bool(os.getenv("CERBERUS_GITHUB_TOKEN", "").strip()),
            "installed": installed,
            "available": available,
        }

    def install(self, extension_id: str, *, http: Any = requests) -> dict[str, Any]:
        self.ensure_layout()
        entry = self.catalog.get(str(extension_id).strip())
        if entry is None:
            raise ValueError("extension is not in the approved catalog")
        parent = self.directory(entry.kind)
        destination = parent / entry.folder_name
        self._require_inside(destination, parent)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("extension is already installed")

        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": GITHUB_API_VERSION}
        token = os.getenv("CERBERUS_GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = http.get(entry.archive_url, headers=headers, timeout=(10, 60), stream=True)
        response.raise_for_status()
        archive = self._read_bounded(response)
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=parent))
        try:
            payload = staging / "payload"
            payload.mkdir()
            self._extract_archive(archive, payload)
            manifest = self._manifest(payload)
            if not manifest.get("plugin_id") or not manifest.get("plugin_version"):
                raise ValueError("extension archive is missing a valid Cerberus manifest")
            (payload / ".cerberus-install.json").write_text(
                json.dumps(
                    {
                        "catalog_id": entry.extension_id,
                        "kind": entry.kind,
                        "repository": entry.repository,
                        "ref": entry.ref,
                        "install_id": uuid.uuid4().hex,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            payload.replace(destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": True, "installed": entry.extension_id, "kind": entry.kind, "path": str(destination)}

    @staticmethod
    def _read_bounded(response: Any) -> bytes:
        content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
            raise ValueError("extension archive exceeds download limit")
        output = io.BytesIO()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            if output.tell() + len(chunk) > MAX_ARCHIVE_BYTES:
                raise ValueError("extension archive exceeds download limit")
            output.write(chunk)
        return output.getvalue()

    @classmethod
    def _extract_archive(cls, archive: bytes, destination: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            files = [member for member in members if not member.is_dir()]
            if not files or len(files) > MAX_ARCHIVE_FILES:
                raise ValueError("extension archive has an invalid file count")
            if sum(member.file_size for member in files) > MAX_EXPANDED_BYTES:
                raise ValueError("extension archive exceeds expanded-size limit")
            parsed = [PurePosixPath(member.filename) for member in members]
            roots = {path.parts[0] for path in parsed if path.parts}
            if len(roots) != 1:
                raise ValueError("extension archive must have one root directory")
            root = next(iter(roots))
            for member, path in zip(members, parsed, strict=True):
                if member.flag_bits & 0x1:
                    raise ValueError("encrypted extension archives are not supported")
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("extension archive contains a link or special file")
                windows_unsafe = any(
                    not part or ":" in part or "\x00" in part or part.endswith((" ", ".")) for part in path.parts
                )
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename or windows_unsafe or not path.parts or path.parts[0] != root:
                    raise ValueError("extension archive contains an unsafe path")
                relative = Path(*path.parts[1:])
                if not relative.parts:
                    continue
                target = destination / relative
                cls._require_inside(target, destination)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)

    def _installed(self, kind: str) -> list[dict[str, Any]]:
        values = []
        for item in self.directory(kind).iterdir():
            if item.name.startswith(".") or not item.is_dir():
                continue
            manifest = self._manifest(item)
            values.append(
                {
                    "folder": item.name,
                    "name": str(manifest.get("plugin_id") or manifest.get("name") or item.name),
                    "version": str(manifest.get("plugin_version") or manifest.get("version") or ""),
                    "path": str(item),
                    "symlink": item.is_symlink(),
                }
            )
        return sorted(values, key=lambda item: item["name"].casefold())

    @staticmethod
    def _manifest(folder: Path) -> dict[str, Any]:
        path = folder / "cerberus-plugin.json"
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _require_inside(path: Path, parent: Path) -> None:
        resolved_path = path.resolve(strict=False)
        resolved_parent = parent.resolve(strict=False)
        if resolved_path != resolved_parent and resolved_parent not in resolved_path.parents:
            raise ValueError("extension path escapes its managed directory")


def extension_inventory() -> dict[str, Any]:
    return ExtensionStore().inventory()


def install_extension(extension_id: str) -> dict[str, Any]:
    return ExtensionStore().install(extension_id)
