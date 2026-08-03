"""Governed, local-only Ollama gateway.

The gateway returns untrusted proposals.  It has no execution/tool loop and is
disabled by default so deterministic CERBERUS behavior remains available when
Ollama is absent, slow, or rejected.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_ALIASES_FILE = Path(__file__).resolve().parents[1] / "models" / "aliases.json"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SECRET_KEY = re.compile(r"(secret|private.?key|password|token|authorization|cookie|mnemonic|seed)", re.I)
SECRET_VALUE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|0x[a-fA-F0-9]{64}|Bearer\s+\S+)", re.I)


class ModelGatewayError(RuntimeError):
    """Base class for deterministic gateway rejection."""


class GatewayDisabled(ModelGatewayError):
    pass


class GatewayValidationError(ModelGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class ModelProposal:
    alias: str
    model: str
    digest: str
    prompt_version: str
    output: dict[str, Any]
    latency_ms: int
    prompt_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    alias: str
    model: str
    digest: str
    vectors: tuple[tuple[float, ...], ...]
    latency_ms: int


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        raise GatewayValidationError("model input nesting exceeds limit")
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            label = str(key)[:80]
            if SECRET_KEY.search(label):
                raise GatewayValidationError(f"secret-like input key rejected: {label}")
            clean[label] = _sanitize(item, depth=depth + 1)
        return clean
    if isinstance(value, list):
        if len(value) > 100:
            raise GatewayValidationError("model input list exceeds limit")
        return [_sanitize(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        text = " ".join(value.replace("\x00", " ").split())
        if SECRET_VALUE.search(text):
            raise GatewayValidationError("secret-like input value rejected")
        return text[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and not matches.get(str(expected), False):
        raise GatewayValidationError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise GatewayValidationError(f"{path} is not an allowed value")
    if expected == "object":
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        missing = [key for key in required if key not in value]
        if missing:
            raise GatewayValidationError(f"{path} missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise GatewayValidationError(f"{path} contains unknown fields: {', '.join(unknown)}")
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], path=f"{path}.{key}")
    elif expected == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        for index, item in enumerate(value):
            _validate_schema(item, items, path=f"{path}[{index}]")


class OllamaModelGateway:
    def __init__(
        self,
        *,
        endpoint: str | None = None,
        aliases_path: str | Path = DEFAULT_ALIASES_FILE,
        transport: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
        health_transport: Callable[[str, float], dict[str, Any]] | None = None,
    ):
        self.endpoint = (endpoint or os.getenv("CERBERUS_OLLAMA_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
        host = (urlparse(self.endpoint).hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            raise GatewayValidationError("Ollama endpoint must be loopback-only")
        self.aliases_path = Path(aliases_path)
        self.transport = transport or self._http_post
        self.health_transport = health_transport or self._http_get

    def enabled(self) -> bool:
        return _truthy("CERBERUS_MODEL_GATEWAY_ENABLED") and not _truthy("CERBERUS_INFERENCE_KILL_SWITCH")

    def aliases(self) -> dict[str, dict[str, Any]]:
        payload = json.loads(self.aliases_path.read_text(encoding="utf-8"))
        aliases = payload.get("aliases") if isinstance(payload.get("aliases"), dict) else {}
        return {str(key): value for key, value in aliases.items() if isinstance(value, dict)}

    def health(self, *, timeout: float = 2.0) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "mode": "deterministic", "reason": "gateway_disabled"}
        try:
            response = self.health_transport("/api/version", max(0.1, timeout))
            return {"ok": True, "mode": "model_available", "version": str(response.get("version") or "")[:40]}
        except Exception as exc:
            return {"ok": False, "mode": "deterministic", "reason": type(exc).__name__}

    def readiness(self, *, timeout: float = 3.0) -> dict[str, Any]:
        health = self.health(timeout=timeout)
        if not health.get("ok"):
            return {**health, "aliases_ready": False, "missing": [], "digest_mismatches": []}
        try:
            payload = self.health_transport("/api/tags", max(0.1, timeout))
            installed = {
                str(item.get("name") or item.get("model") or ""): str(item.get("digest") or "")
                for item in payload.get("models", [])
                if isinstance(item, dict)
            }
            missing: list[str] = []
            mismatches: list[str] = []
            for alias, config in self.aliases().items():
                model = str(config.get("model") or "")
                expected = str(config.get("digest") or "")
                actual = installed.get(model, "")
                if not actual:
                    missing.append(alias)
                elif expected and actual != expected:
                    mismatches.append(alias)
            return {
                **health,
                "aliases_ready": not missing and not mismatches,
                "missing": missing,
                "digest_mismatches": mismatches,
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "deterministic",
                "reason": type(exc).__name__,
                "aliases_ready": False,
                "missing": [],
                "digest_mismatches": [],
            }

    def propose(
        self,
        *,
        alias: str,
        prompt_version: str,
        task: str,
        context: dict[str, Any],
        output_schema: dict[str, Any],
        allow_evaluation: bool = False,
    ) -> ModelProposal:
        if not self.enabled():
            raise GatewayDisabled("model gateway is disabled; use deterministic fallback")
        config = self.aliases().get(alias)
        if not config:
            raise GatewayValidationError(f"unknown model alias: {alias}")
        status = str(config.get("status") or "")
        if status != "production" and not (allow_evaluation and status == "evaluation_only"):
            raise GatewayValidationError(f"model alias is not promoted: {alias}")
        if task not in (config.get("tasks") or []):
            raise GatewayValidationError(f"task is not approved for alias: {task}")
        safe_context = _sanitize(context)
        prompt = json.dumps(
            {"prompt_version": str(prompt_version)[:80], "task": task, "context": safe_context},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        deadline = min(60.0, max(0.1, float(config.get("deadline_seconds") or 10)))
        request = {
            "model": str(config.get("model") or ""),
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "format": output_schema,
            "options": {
                "temperature": float(config.get("temperature") or 0),
                "num_ctx": min(8192, int(config.get("context_limit") or 4096)),
                "num_predict": 512,
            },
        }
        started = time.monotonic()
        response = self.transport("/api/generate", request, deadline)
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            output = json.loads(str(response.get("response") or ""))
        except (TypeError, ValueError) as exc:
            raise GatewayValidationError("model response is not valid JSON") from exc
        if not isinstance(output, dict):
            raise GatewayValidationError("model proposal must be an object")
        _validate_schema(output, output_schema)
        return ModelProposal(
            alias=alias,
            model=str(config.get("model") or ""),
            digest=str(config.get("digest") or ""),
            prompt_version=str(prompt_version)[:80],
            output=output,
            latency_ms=latency_ms,
            prompt_tokens=int(response.get("prompt_eval_count") or 0),
            output_tokens=int(response.get("eval_count") or 0),
        )

    def embed(
        self,
        *,
        alias: str,
        texts: list[str],
        allow_evaluation: bool = False,
    ) -> EmbeddingBatch:
        """Create local vectors only; embeddings never carry execution authority."""
        if not self.enabled():
            raise GatewayDisabled("model gateway is disabled; retrieval must fail closed")
        if not texts or len(texts) > 64:
            raise GatewayValidationError("embedding batch must contain 1 to 64 texts")
        config = self.aliases().get(alias)
        if not config or "embedding" not in (config.get("tasks") or []):
            raise GatewayValidationError(f"alias is not approved for embedding: {alias}")
        status = str(config.get("status") or "")
        if status != "production" and not (allow_evaluation and status == "evaluation_only"):
            raise GatewayValidationError(f"model alias is not promoted: {alias}")
        safe_texts = [_sanitize(text) for text in texts]
        deadline = min(60.0, max(0.1, float(config.get("deadline_seconds") or 10)))
        started = time.monotonic()
        response = self.transport(
            "/api/embed",
            {
                "model": str(config.get("model") or ""),
                "input": safe_texts,
                "truncate": True,
                "keep_alive": 0,
            },
            deadline,
        )
        raw_vectors = response.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(texts):
            raise GatewayValidationError("embedding response count mismatch")
        vectors: list[tuple[float, ...]] = []
        dimensions = 0
        for raw in raw_vectors:
            if not isinstance(raw, list) or not raw or len(raw) > 8192:
                raise GatewayValidationError("embedding vector has invalid dimensions")
            vector = tuple(float(value) for value in raw)
            if any(not math.isfinite(value) for value in vector):
                raise GatewayValidationError("embedding vector contains non-finite values")
            dimensions = dimensions or len(vector)
            if len(vector) != dimensions:
                raise GatewayValidationError("embedding vectors have inconsistent dimensions")
            vectors.append(vector)
        return EmbeddingBatch(
            alias=alias,
            model=str(config.get("model") or ""),
            digest=str(config.get("digest") or ""),
            vectors=tuple(vectors),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def _http_post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.endpoint}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(result, dict):
            raise GatewayValidationError("Ollama response must be an object")
        return result

    def _http_get(self, path: str, timeout: float) -> dict[str, Any]:
        request = Request(f"{self.endpoint}{path}", method="GET")
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(result, dict):
            raise GatewayValidationError("Ollama response must be an object")
        return result

