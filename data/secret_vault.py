"""
Local secret vault for Cerberus.

Secrets are encrypted with Windows DPAPI plus PIN-derived optional entropy.
The PIN is never stored. Provide it through the CERBERUS_PIN environment
variable or as an explicit function argument.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VAULT_TYPE = "cerberus.secret.vault"
VAULT_VERSION = 1
DEFAULT_PIN_ENV = "CERBERUS_PIN"
DEFAULT_ITERATIONS = 310_000


class SecretVaultError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _require_windows() -> None:
    if os.name != "nt":
        raise SecretVaultError(
            "Cerberus DPAPI vault requires Windows. Install a vetted crypto "
            "backend before using this vault on another OS."
        )


def _pin_value(pin: str | None = None, env_name: str = DEFAULT_PIN_ENV) -> str:
    value = pin if pin is not None else os.getenv(env_name, "")
    if not value:
        raise SecretVaultError(
            f"No vault PIN supplied. Set {env_name} or pass pin= explicitly."
        )
    if len(value) < 4:
        raise SecretVaultError("Vault PIN must be at least 4 characters.")
    return value


def _derive_entropy(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )


def _make_blob(data: bytes) -> tuple[_DATA_BLOB, Any]:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(
        len(data),
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buf


def _protect(plaintext: bytes, entropy: bytes) -> bytes:
    _require_windows()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob, in_buf = _make_blob(plaintext)
    entropy_blob, entropy_buf = _make_blob(entropy)
    out_blob = _DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    _ = (in_buf, entropy_buf)
    if not ok:
        raise SecretVaultError("CryptProtectData failed.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _unprotect(ciphertext: bytes, entropy: bytes) -> bytes:
    _require_windows()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob, in_buf = _make_blob(ciphertext)
    entropy_blob, entropy_buf = _make_blob(entropy)
    out_blob = _DATA_BLOB()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    _ = (in_buf, entropy_buf)
    if not ok:
        raise SecretVaultError("CryptUnprotectData failed. Wrong PIN or user.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def encrypt_bytes(
    plaintext: bytes,
    *,
    pin: str | None = None,
    purpose: str = "secret",
    pin_env: str = DEFAULT_PIN_ENV,
) -> dict[str, Any]:
    pin_value = _pin_value(pin, pin_env)
    salt = os.urandom(16)
    iterations = DEFAULT_ITERATIONS
    entropy = _derive_entropy(pin_value, salt, iterations)
    ciphertext = _protect(plaintext, entropy)
    return {
        "type": VAULT_TYPE,
        "version": VAULT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "backend": "windows-dpapi-user",
        "pin_kdf": "pbkdf2-hmac-sha256",
        "pin_iterations": iterations,
        "pin_salt_b64": base64.b64encode(salt).decode("ascii"),
        "payload_b64": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_bytes(
    envelope: dict[str, Any],
    *,
    pin: str | None = None,
    pin_env: str = DEFAULT_PIN_ENV,
) -> bytes:
    if envelope.get("type") != VAULT_TYPE:
        raise SecretVaultError("Not a Cerberus vault envelope.")
    if envelope.get("version") != VAULT_VERSION:
        raise SecretVaultError(f"Unsupported vault version: {envelope.get('version')}")

    pin_value = _pin_value(pin, pin_env)
    salt = base64.b64decode(envelope["pin_salt_b64"])
    iterations = int(envelope["pin_iterations"])
    entropy = _derive_entropy(pin_value, salt, iterations)
    ciphertext = base64.b64decode(envelope["payload_b64"])
    return _unprotect(ciphertext, entropy)


def encrypt_json(data: Any, *, pin: str | None = None, purpose: str = "json") -> dict[str, Any]:
    plaintext = json.dumps(
        data,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return encrypt_bytes(plaintext, pin=pin, purpose=purpose)


def decrypt_json(envelope: dict[str, Any], *, pin: str | None = None) -> Any:
    return json.loads(decrypt_bytes(envelope, pin=pin).decode("utf-8"))


def write_vault(path: str | Path, data: Any, *, pin: str | None = None, purpose: str = "json") -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    envelope = encrypt_json(data, pin=pin, purpose=purpose)
    out.write_text(json.dumps(envelope, ensure_ascii=True, indent=2), encoding="utf-8")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return out


def read_vault(path: str | Path, *, pin: str | None = None) -> Any:
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    return decrypt_json(envelope, pin=pin)


def is_vault_envelope(data: Any) -> bool:
    return isinstance(data, dict) and data.get("type") == VAULT_TYPE


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Cerberus local secret vault")
    sub = parser.add_subparsers(dest="cmd", required=True)

    enc = sub.add_parser("encrypt-json", help="Encrypt a JSON file into a vault")
    enc.add_argument("input")
    enc.add_argument("output")
    enc.add_argument("--purpose", default="json")

    dec = sub.add_parser("decrypt-json", help="Decrypt a vault JSON file")
    dec.add_argument("input")

    args = parser.parse_args()

    if args.cmd == "encrypt-json":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        write_vault(args.output, data, purpose=args.purpose)
        print(args.output)
        return 0

    if args.cmd == "decrypt-json":
        data = read_vault(args.input)
        print(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
