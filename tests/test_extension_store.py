from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import render_app
from extension_store import CatalogEntry, ExtensionStore


def archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, value in files.items():
            bundle.writestr(name, value)
    return output.getvalue()


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]


class HTTP:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.body)


def one_entry(kind: str = "plugin") -> CatalogEntry:
    return CatalogEntry("fixture", "Fixture", kind, "AnarchI-Technologies/cerberus-fixture", "v1.0.0")


def test_layout_exposes_only_separate_plugin_and_adapter_directories(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path, catalog=(one_entry(),))
    inventory = store.inventory()

    assert Path(inventory["directories"]["plugins"]) == tmp_path / "plugins"
    assert Path(inventory["directories"]["adapters"]) == tmp_path / "adapters"
    assert (tmp_path / "plugins").is_dir()
    assert (tmp_path / "adapters").is_dir()


def test_install_uses_catalog_url_and_writes_only_to_its_kind(tmp_path: Path, monkeypatch) -> None:
    entry = one_entry("adapter")
    store = ExtensionStore(tmp_path, catalog=(entry,))
    body = archive({"root/cerberus-plugin.json": b'{"plugin_id":"fixture","plugin_version":"1.0.0"}', "root/src/code.py": b"value = 1\n"})
    http = HTTP(body)
    monkeypatch.setenv("CERBERUS_GITHUB_TOKEN", "secret-token")

    result = store.install("fixture", http=http)

    target = tmp_path / "adapters" / "cerberus-fixture"
    assert result["path"] == str(target)
    assert (target / "src" / "code.py").read_text() == "value = 1\n"
    assert not (tmp_path / "plugins" / "cerberus-fixture").exists()
    assert http.calls[0][0] == entry.archive_url
    assert http.calls[0][1]["headers"]["Authorization"] == "Bearer secret-token"


def test_install_rejects_unknown_catalog_ids_and_existing_destinations(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path, catalog=(one_entry(),))
    with pytest.raises(ValueError, match="approved catalog"):
        store.install("https://attacker.invalid/archive.zip", http=HTTP(b""))
    target = tmp_path / "plugins" / "cerberus-fixture"
    target.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="already installed"):
        store.install("fixture", http=HTTP(b""))


def test_archive_traversal_is_rejected_without_installing(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path, catalog=(one_entry(),))
    body = archive({"root/../../escaped.txt": b"nope", "root/README.md": b"fixture"})

    with pytest.raises(ValueError, match="unsafe path"):
        store.install("fixture", http=HTTP(body))

    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "plugins" / "cerberus-fixture").exists()


def test_windows_alternate_stream_paths_are_rejected(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path, catalog=(one_entry(),))
    body = archive({"root/README.md:hidden": b"nope"})

    with pytest.raises(ValueError, match="unsafe path"):
        store.install("fixture", http=HTTP(body))


def test_archive_requires_a_cerberus_manifest(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path, catalog=(one_entry(),))
    body = archive({"root/README.md": b"not an installable extension"})

    with pytest.raises(ValueError, match="valid Cerberus manifest"):
        store.install("fixture", http=HTTP(body))

    assert not (tmp_path / "plugins" / "cerberus-fixture").exists()


def test_inventory_reads_manifest_without_importing_installed_code(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "cerberus-fixture"
    folder.mkdir(parents=True)
    (folder / "cerberus-plugin.json").write_text(
        json.dumps({"plugin_id": "cerberus.fixture", "plugin_version": "1.2.3"}), encoding="utf-8"
    )
    (folder / "danger.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")

    inventory = ExtensionStore(tmp_path, catalog=(one_entry(),)).inventory()

    assert inventory["installed"]["plugin"][0]["name"] == "cerberus.fixture"
    assert inventory["installed"]["plugin"][0]["version"] == "1.2.3"
    assert inventory["available"]["plugin"][0]["installed"] is True


def test_dashboard_contains_extension_inventory_and_install_controls() -> None:
    html = render_app.dashboard_html().decode("utf-8")
    assert 'id="tab-extensions"' in html
    assert 'id="plugins-folder"' in html
    assert 'id="adapter-list"' in html
    assert 'fetchJson("/admin/extensions/install"' in html
    assert "installedExtensionCard" in html
    assert "available.filter((item) => !item.installed)" in html


def test_extension_inventory_route_requires_authorization(monkeypatch) -> None:
    sent = []

    class Handler(render_app.CerberusHandler):
        def __init__(self, authorized):
            self.path = "/admin/extensions"
            self.authorized = authorized

        def _authorized(self):
            return self.authorized

        def _send(self, body, *, status=200):
            sent.append((status, body))

    monkeypatch.setattr(render_app, "extension_inventory", lambda: {"ok": True, "installed": {}})
    Handler(False).do_GET()
    Handler(True).do_GET()

    assert sent[0] == (401, {"ok": False, "error": "unauthorized"})
    assert sent[1][0] == 200 and sent[1][1]["ok"] is True
