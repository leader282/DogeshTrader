"""Exception hierarchy for DogeshTrader."""

from __future__ import annotations


class DogeshTraderError(Exception):
    """Base class for all framework-level errors."""


class ConfigError(DogeshTraderError):
    """Raised when configuration is missing or invalid."""


class StrategyError(DogeshTraderError):
    """Raised by the strategy layer (bad signal, bad parameter, etc.)."""


class RiskRejected(DogeshTraderError):
    """Raised when the risk engine rejects an OrderIntent.

    Attributes:
        rule: Short identifier for the rule that fired (e.g. ``max_order_qty``).
        detail: Human-readable detail.
    """

    def __init__(self, rule: str, detail: str = "") -> None:
        self.rule = rule
        self.detail = detail
        message = f"{rule}: {detail}" if detail else rule
        super().__init__(message)


class OrderRejected(DogeshTraderError):
    """Raised when a broker rejects an order.

    For paper / dry-run brokers, this is used to surface validation failures
    without contacting the network.
    """

    def __init__(self, reason: str, raw: object | None = None) -> None:
        self.reason = reason
        self.raw = raw
        super().__init__(reason)


class BrokerError(DogeshTraderError):
    """Broker adapter or network error."""


class AuthError(DogeshTraderError):
    """Kite auth/login error."""


class DataError(DogeshTraderError):
    """Market data or persistence error."""


class IdempotencyConflict(DogeshTraderError):
    """An order with the same idempotency key was already placed."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"duplicate order intent (idempotency_key={key})")
