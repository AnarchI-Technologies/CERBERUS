"""Signal Pyre: deterministic in-process event transport for AnarchI runtimes.

The initial implementation is intentionally synchronous and dependency-free.
Publishers emit immutable event envelopes. Subscribers execute in registration
order, and one subscriber failure cannot prevent later subscribers from
receiving the event.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from types import MappingProxyType
from typing import Any
from uuid import uuid4


EventHandler = Callable[["SignalEnvelope"], Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_event_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("event name must be a string")

    normalized = value.strip()

    if not normalized:
        raise ValueError("event name cannot be empty")

    if normalized == "*":
        return normalized

    if any(character.isspace() for character in normalized):
        raise ValueError("event name cannot contain whitespace")

    return normalized


@dataclass(frozen=True, slots=True)
class SignalEnvelope:
    """Immutable metadata and payload carried through Signal Pyre."""

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    event_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: datetime = field(default_factory=_utc_now)
    schema_version: int = 1

    def __post_init__(self) -> None:
        normalized_name = _validate_event_name(self.name)

        if normalized_name == "*":
            raise ValueError("'*' is reserved for wildcard subscriptions")

        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")

        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")

        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        if self.schema_version < 1:
            raise ValueError("schema_version must be at least 1")

        immutable_payload = MappingProxyType(dict(self.payload))

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "payload", immutable_payload)


@dataclass(frozen=True, slots=True)
class Subscription:
    """Opaque subscriber registration returned by SignalPyre.subscribe."""

    token: int
    event_name: str
    subscriber_name: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Result of one subscriber receiving one event."""

    subscription: Subscription
    succeeded: bool
    returned: Any = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    """Evidence describing the complete synchronous delivery attempt."""

    event: SignalEnvelope
    deliveries: tuple[DeliveryResult, ...]
    published_at: datetime = field(default_factory=_utc_now)

    @property
    def attempted(self) -> int:
        return len(self.deliveries)

    @property
    def succeeded(self) -> int:
        return sum(result.succeeded for result in self.deliveries)

    @property
    def failed(self) -> int:
        return self.attempted - self.succeeded

    @property
    def clean(self) -> bool:
        return self.failed == 0


@dataclass(slots=True)
class _RegisteredHandler:
    subscription: Subscription
    handler: EventHandler


class SignalPyre:
    """Deterministic synchronous publish/subscribe event bus.

    Exact-event and wildcard subscribers are merged and executed according to
    their global registration order. Subscriber exceptions are captured in the
    returned receipt rather than escaping from publish().
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._next_token = 1
        self._handlers: dict[str, list[_RegisteredHandler]] = {}

    def subscribe(
        self,
        event_name: str,
        handler: EventHandler,
        *,
        subscriber_name: str | None = None,
    ) -> Subscription:
        normalized_name = _validate_event_name(event_name)

        if not callable(handler):
            raise TypeError("handler must be callable")

        resolved_name = subscriber_name or getattr(
            handler,
            "__qualname__",
            getattr(handler, "__name__", handler.__class__.__name__),
        )

        if not isinstance(resolved_name, str) or not resolved_name.strip():
            raise ValueError("subscriber_name must be a non-empty string")

        with self._lock:
            subscription = Subscription(
                token=self._next_token,
                event_name=normalized_name,
                subscriber_name=resolved_name.strip(),
            )
            self._next_token += 1

            self._handlers.setdefault(normalized_name, []).append(
                _RegisteredHandler(
                    subscription=subscription,
                    handler=handler,
                )
            )

        return subscription

    def unsubscribe(self, subscription: Subscription) -> bool:
        if not isinstance(subscription, Subscription):
            raise TypeError("subscription must be a Subscription")

        with self._lock:
            handlers = self._handlers.get(subscription.event_name)

            if not handlers:
                return False

            for index, registered in enumerate(handlers):
                if registered.subscription.token == subscription.token:
                    del handlers[index]

                    if not handlers:
                        del self._handlers[subscription.event_name]

                    return True

        return False

    def publish(
        self,
        event: SignalEnvelope | str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str = "unknown",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> PublishReceipt:
        if isinstance(event, SignalEnvelope):
            if payload is not None:
                raise ValueError(
                    "payload cannot be supplied when publishing an envelope"
                )
            envelope = event
        else:
            envelope = SignalEnvelope(
                name=event,
                payload=payload or {},
                source=source,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        registered = self._snapshot_handlers(envelope.name)
        deliveries: list[DeliveryResult] = []

        for item in registered:
            try:
                returned = item.handler(envelope)
            except Exception as error:  # Subscriber failures are isolated.
                deliveries.append(
                    DeliveryResult(
                        subscription=item.subscription,
                        succeeded=False,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                )
            else:
                deliveries.append(
                    DeliveryResult(
                        subscription=item.subscription,
                        succeeded=True,
                        returned=returned,
                    )
                )

        return PublishReceipt(
            event=envelope,
            deliveries=tuple(deliveries),
        )

    def subscriber_count(self, event_name: str | None = None) -> int:
        with self._lock:
            if event_name is None:
                return sum(len(items) for items in self._handlers.values())

            normalized_name = _validate_event_name(event_name)
            return len(self._handlers.get(normalized_name, ()))

    def _snapshot_handlers(self, event_name: str) -> tuple[_RegisteredHandler, ...]:
        with self._lock:
            exact = self._handlers.get(event_name, ())
            wildcard = self._handlers.get("*", ())
            combined = [*exact, *wildcard]
            combined.sort(key=lambda item: item.subscription.token)
            return tuple(combined)


__all__ = [
    "DeliveryResult",
    "PublishReceipt",
    "SignalEnvelope",
    "SignalPyre",
    "Subscription",
]
