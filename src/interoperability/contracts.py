"""Portable contracts for the AnarchI interoperability boundary.

This module intentionally depends only on the Python standard library. Its
wire representation is JSON so adapters may be implemented in any language.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, runtime_checkable


SCHEMA_VERSION = "anarchi.interop.v1"
JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class ContractError(ValueError):
    """Raised when a value cannot cross the interoperability boundary."""


class ResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    PENDING = "pending"


def _require_text(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ContractError(f"{name} must not be empty")
    return normalized


def freeze_json(value: Any, *, path: str = "$") -> JsonValue:
    """Validate and recursively freeze a JSON-compatible value."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ContractError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ContractError(f"{path} contains a non-string object key")
            frozen[key] = freeze_json(value[key], path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise ContractError(f"{path} contains unsupported value type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    version: str
    modes: tuple[str, ...] = ("invoke",)
    request_schema: JsonValue = field(default_factory=dict)
    result_schema: JsonValue = field(default_factory=dict)
    observation_schema: JsonValue = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text("capability name", self.name))
        object.__setattr__(self, "version", _require_text("capability version", self.version))
        modes = tuple(sorted({_require_text("capability mode", mode) for mode in self.modes}))
        if not modes:
            raise ContractError("capability modes must not be empty")
        object.__setattr__(self, "modes", modes)
        object.__setattr__(self, "request_schema", freeze_json(self.request_schema))
        object.__setattr__(self, "result_schema", freeze_json(self.result_schema))
        object.__setattr__(self, "observation_schema", freeze_json(self.observation_schema))


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    adapter_version: str
    capabilities: tuple[CapabilitySpec, ...]
    protocol_version: str = SCHEMA_VERSION
    metadata: JsonValue = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _require_text("adapter id", self.adapter_id))
        object.__setattr__(self, "adapter_version", _require_text("adapter version", self.adapter_version))
        if self.protocol_version != SCHEMA_VERSION:
            raise ContractError(
                f"unsupported protocol version {self.protocol_version!r}; expected {SCHEMA_VERSION!r}"
            )
        capabilities = tuple(sorted(self.capabilities, key=lambda item: (item.name, item.version)))
        keys = [(item.name, item.version) for item in capabilities]
        if len(keys) != len(set(keys)):
            raise ContractError("adapter declares a duplicate capability version")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def supports(self, capability: str, version: str | None = None) -> bool:
        return any(
            item.name == capability and (version is None or item.version == version)
            for item in self.capabilities
        )


@dataclass(frozen=True, slots=True)
class Command:
    command_id: str
    adapter_id: str
    capability: str
    session_id: str
    payload: JsonValue = field(default_factory=dict)
    idempotency_key: str = ""
    correlation_id: str = ""
    metadata: JsonValue = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("command_id", "adapter_id", "capability", "session_id"):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported schema version {self.schema_version!r}")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    adapter_id: str
    capability: str
    session_id: str
    sequence: int
    payload: JsonValue = field(default_factory=dict)
    correlation_id: str = ""
    metadata: JsonValue = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("observation_id", "adapter_id", "capability", "session_id"):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if self.sequence < 0:
            raise ContractError("sequence must not be negative")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported schema version {self.schema_version!r}")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))


@dataclass(frozen=True, slots=True)
class Result:
    result_id: str
    command_id: str
    adapter_id: str
    capability: str
    status: ResultStatus
    payload: JsonValue = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    correlation_id: str = ""
    metadata: JsonValue = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("result_id", "command_id", "adapter_id", "capability"):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if not isinstance(self.status, ResultStatus):
            object.__setattr__(self, "status", ResultStatus(self.status))
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported schema version {self.schema_version!r}")
        if self.status in {ResultStatus.FAILED, ResultStatus.REJECTED} and not self.error_code:
            raise ContractError("failed or rejected results require an error_code")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))


@runtime_checkable
class Adapter(Protocol):
    @property
    def descriptor(self) -> AdapterDescriptor: ...

    async def invoke(self, command: Command) -> Result: ...
