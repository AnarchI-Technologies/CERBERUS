"""Canonical JSON codec for interoperability contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    AdapterDescriptor,
    CapabilitySpec,
    Command,
    ContractError,
    Observation,
    Result,
    ResultStatus,
)


def _thaw(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def to_wire(value: AdapterDescriptor | CapabilitySpec | Command | Observation | Result) -> dict[str, Any]:
    if isinstance(value, CapabilitySpec):
        return {
            "name": value.name,
            "version": value.version,
            "modes": list(value.modes),
            "request_schema": _thaw(value.request_schema),
            "result_schema": _thaw(value.result_schema),
            "observation_schema": _thaw(value.observation_schema),
        }
    if isinstance(value, AdapterDescriptor):
        return {
            "kind": "adapter_descriptor",
            "schema_version": value.protocol_version,
            "adapter_id": value.adapter_id,
            "adapter_version": value.adapter_version,
            "capabilities": [to_wire(item) for item in value.capabilities],
            "metadata": _thaw(value.metadata),
        }
    if isinstance(value, Command):
        return {
            "kind": "command",
            "schema_version": value.schema_version,
            "command_id": value.command_id,
            "adapter_id": value.adapter_id,
            "capability": value.capability,
            "session_id": value.session_id,
            "payload": _thaw(value.payload),
            "idempotency_key": value.idempotency_key,
            "correlation_id": value.correlation_id,
            "metadata": _thaw(value.metadata),
        }
    if isinstance(value, Observation):
        return {
            "kind": "observation",
            "schema_version": value.schema_version,
            "observation_id": value.observation_id,
            "adapter_id": value.adapter_id,
            "capability": value.capability,
            "session_id": value.session_id,
            "sequence": value.sequence,
            "payload": _thaw(value.payload),
            "correlation_id": value.correlation_id,
            "metadata": _thaw(value.metadata),
        }
    if isinstance(value, Result):
        return {
            "kind": "result",
            "schema_version": value.schema_version,
            "result_id": value.result_id,
            "command_id": value.command_id,
            "adapter_id": value.adapter_id,
            "capability": value.capability,
            "status": value.status.value,
            "payload": _thaw(value.payload),
            "error_code": value.error_code,
            "error_message": value.error_message,
            "retryable": value.retryable,
            "correlation_id": value.correlation_id,
            "metadata": _thaw(value.metadata),
        }
    raise ContractError(f"unsupported contract type {type(value).__name__}")


def encode(value: AdapterDescriptor | Command | Observation | Result) -> str:
    return json.dumps(to_wire(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode(payload: str | bytes | Mapping[str, Any]) -> AdapterDescriptor | Command | Observation | Result:
    raw = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else dict(payload)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(f"unsupported schema version {raw.get('schema_version')!r}")

    kind = raw.get("kind")
    if kind == "adapter_descriptor":
        capabilities = tuple(
            CapabilitySpec(
                name=item["name"],
                version=item["version"],
                modes=tuple(item.get("modes", ("invoke",))),
                request_schema=item.get("request_schema", {}),
                result_schema=item.get("result_schema", {}),
                observation_schema=item.get("observation_schema", {}),
            )
            for item in raw.get("capabilities", ())
        )
        return AdapterDescriptor(
            adapter_id=raw["adapter_id"],
            adapter_version=raw["adapter_version"],
            capabilities=capabilities,
            protocol_version=raw["schema_version"],
            metadata=raw.get("metadata", {}),
        )
    if kind == "command":
        return Command(
            command_id=raw["command_id"],
            adapter_id=raw["adapter_id"],
            capability=raw["capability"],
            session_id=raw["session_id"],
            payload=raw.get("payload", {}),
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            metadata=raw.get("metadata", {}),
            schema_version=raw["schema_version"],
        )
    if kind == "observation":
        return Observation(
            observation_id=raw["observation_id"],
            adapter_id=raw["adapter_id"],
            capability=raw["capability"],
            session_id=raw["session_id"],
            sequence=int(raw["sequence"]),
            payload=raw.get("payload", {}),
            correlation_id=raw.get("correlation_id", ""),
            metadata=raw.get("metadata", {}),
            schema_version=raw["schema_version"],
        )
    if kind == "result":
        return Result(
            result_id=raw["result_id"],
            command_id=raw["command_id"],
            adapter_id=raw["adapter_id"],
            capability=raw["capability"],
            status=ResultStatus(raw["status"]),
            payload=raw.get("payload", {}),
            error_code=raw.get("error_code", ""),
            error_message=raw.get("error_message", ""),
            retryable=bool(raw.get("retryable", False)),
            correlation_id=raw.get("correlation_id", ""),
            metadata=raw.get("metadata", {}),
            schema_version=raw["schema_version"],
        )
    raise ContractError(f"unsupported message kind {kind!r}")
