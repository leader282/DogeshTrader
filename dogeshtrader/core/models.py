"""Pydantic v2 domain models.

These types are the lingua franca between the strategy, risk engine, broker
adapters and the backtest engine. Strategies never instantiate broker-specific
types directly; they emit :class:`OrderIntent` and let the framework translate.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dogeshtrader.core.enums import (
    Exchange,
    InstrumentType,
    OrderStatus,
    OrderType,
    Product,
    Segment,
    Side,
    Validity,
    Variety,
)


class _Base(BaseModel):
    """Project-wide pydantic base.

    * `extra="forbid"` so typos in YAML configs fail loudly.
    * `populate_by_name=True` so we can accept Kite's snake_case payloads.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        arbitrary_types_allowed=True,
    )


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


class Instrument(_Base):
    """Tradeable instrument.

    The *unique key* for an instrument is ``(exchange, tradingsymbol)`` because
    Zerodha can re-use ``instrument_token`` across F&O expiries. We keep the
    token for fast lookup but never as the primary identity.
    """

    exchange: Exchange
    tradingsymbol: str
    instrument_token: int | None = None
    exchange_token: int | None = None
    name: str | None = None
    instrument_type: InstrumentType | None = None
    segment: Segment | None = None
    expiry: date | None = None
    strike: float | None = None
    tick_size: float = 0.05
    lot_size: int = 1

    @property
    def key(self) -> str:
        """Stable string key, e.g. ``NSE:INFY``."""
        return f"{self.exchange.value}:{self.tradingsymbol}"

    @classmethod
    def equity(cls, exchange: Exchange | str, tradingsymbol: str, **kw: Any) -> Instrument:
        ex = Exchange(exchange) if isinstance(exchange, str) else exchange
        return cls(
            exchange=ex,
            tradingsymbol=tradingsymbol,
            instrument_type=InstrumentType.EQ,
            segment=Segment(ex.value) if ex.value in {"NSE", "BSE"} else None,
            **kw,
        )


class Candle(_Base):
    """OHLCV(+OI) candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int | None = None

    @field_validator("high")
    @classmethod
    def _high_ge_low(cls, v: float, info: Any) -> float:
        low = info.data.get("low")
        if low is not None and v < low:
            raise ValueError(f"candle high {v} < low {low}")
        return v


class Depth(_Base):
    """One side of a market depth book."""

    price: float
    quantity: int
    orders: int = 0


class Tick(_Base):
    """A single market tick. Bid/ask/depth fields are best-effort."""

    timestamp: datetime
    instrument_token: int
    ltp: float
    last_quantity: int | None = None
    volume_traded: int | None = None
    average_traded_price: float | None = None
    total_buy_quantity: int | None = None
    total_sell_quantity: int | None = None
    oi: int | None = None
    bid: float | None = None
    ask: float | None = None
    depth_buy: list[Depth] = Field(default_factory=list)
    depth_sell: list[Depth] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderIntent(_Base):
    """A strategy's intent to trade.

    Strategies *only* produce intents; the broker layer turns intents into
    broker-specific calls (or simulated fills). An intent is the smallest unit
    the framework reasons about for risk, idempotency, and reporting.
    """

    strategy_id: str = Field(min_length=1)
    instrument: Instrument
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    product: Product = Product.MIS
    variety: Variety = Variety.REGULAR
    price: float | None = None
    trigger_price: float | None = None
    validity: Validity = Validity.DAY
    disclosed_quantity: int | None = None
    iceberg_legs: int | None = None
    iceberg_quantity: int | None = None

    # Operator-facing free-form reason ("fast crossed slow up at 09:34").
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # When set, MARKET orders are protected by +/- this percentage from LTP.
    # If unset on a MARKET order, the risk engine will reject the intent when
    # require_market_protection is true.
    market_protection_pct: float | None = None

    # Optional client idempotency key. If left None the order router computes
    # a deterministic one from (strategy_id, instrument.key, signal_ts).
    idempotency_key: str | None = None

    # Optional logical signal timestamp - useful for backtests where the
    # current candle is meaningful but wall-clock isn't.
    signal_ts: datetime | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> OrderIntent:
        if self.order_type in (OrderType.LIMIT,) and self.price is None:
            raise ValueError("LIMIT order requires price")
        if self.order_type == OrderType.SL and (self.price is None or self.trigger_price is None):
            raise ValueError("SL order requires both price and trigger_price")
        if self.order_type == OrderType.SL_M and self.trigger_price is None:
            raise ValueError("SL-M order requires trigger_price")
        if self.variety == Variety.ICEBERG and (not self.iceberg_legs or not self.iceberg_quantity):
            raise ValueError("iceberg orders require iceberg_legs and iceberg_quantity")
        return self


class OrderRequest(_Base):
    """A risk-validated, broker-ready order.

    This is what a Broker.place_order accepts. Most fields are normalized
    versions of the originating :class:`OrderIntent`, but a tag is required
    so that operators can identify framework orders on the Kite dashboard.
    """

    intent_id: str
    strategy_id: str
    instrument: Instrument
    side: Side
    quantity: int
    order_type: OrderType
    product: Product
    variety: Variety
    price: float | None = None
    trigger_price: float | None = None
    validity: Validity = Validity.DAY
    disclosed_quantity: int | None = None
    iceberg_legs: int | None = None
    iceberg_quantity: int | None = None
    tag: str
    idempotency_key: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderUpdate(_Base):
    """Normalized order update from a broker."""

    order_id: str
    status: OrderStatus
    filled_quantity: int = 0
    pending_quantity: int = 0
    average_price: float = 0.0
    price: float | None = None
    trigger_price: float | None = None
    exchange_order_id: str | None = None
    exchange_timestamp: datetime | None = None
    status_message: str | None = None
    raw: dict[str, Any] | None = None


class Fill(_Base):
    """A single execution (a 'trade' in Kite parlance)."""

    order_id: str
    trade_id: str
    instrument: Instrument
    side: Side
    quantity: int
    price: float
    timestamp: datetime
    fees: float = 0.0
    raw: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class Position(_Base):
    """Aggregated position across fills.

    Sign convention: ``quantity`` is positive for long, negative for short.
    """

    instrument: Instrument
    quantity: int = 0
    avg_price: float = 0.0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    last_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def market_value(self) -> float:
        return self.last_price * self.quantity

    def apply_fill(self, fill: Fill) -> None:
        """Mutate this position with the given fill.

        Pure FIFO / average-price accounting; sufficient for individual retail
        use. Realised P&L on an opposite-sign fill is computed against the
        running average price.
        """
        signed_qty = fill.quantity if fill.side == Side.BUY else -fill.quantity

        if self.quantity == 0:
            self.avg_price = fill.price
            self.quantity = signed_qty
            self.realised_pnl -= fill.fees
            return

        same_direction = (self.quantity > 0) == (signed_qty > 0)
        if same_direction:
            total_cost = self.avg_price * abs(self.quantity) + fill.price * abs(signed_qty)
            self.quantity += signed_qty
            self.avg_price = total_cost / abs(self.quantity) if self.quantity else 0.0
        else:
            closing_qty = min(abs(self.quantity), abs(signed_qty))
            direction = 1 if self.quantity > 0 else -1
            pnl_per_unit = (fill.price - self.avg_price) * direction
            self.realised_pnl += pnl_per_unit * closing_qty
            self.quantity += signed_qty
            if self.quantity == 0:
                self.avg_price = 0.0
            elif (self.quantity > 0) != (direction > 0):
                # Flipped sides; new avg price is the fill price.
                self.avg_price = fill.price

        self.realised_pnl -= fill.fees

    def mark(self, last_price: float) -> None:
        self.last_price = last_price
        if self.quantity == 0:
            self.unrealised_pnl = 0.0
            return
        direction = 1 if self.quantity > 0 else -1
        self.unrealised_pnl = (last_price - self.avg_price) * abs(self.quantity) * direction


# ---------------------------------------------------------------------------
# Strategy context
# ---------------------------------------------------------------------------


class StrategyContext(_Base):
    """Per-tick / per-candle context handed to a strategy.

    Strategies must not mutate any field directly; mutation happens through
    the framework returning intents.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    run_id: str
    strategy_id: str
    mode: Literal["BACKTEST", "PAPER", "DRY_RUN", "LIVE"]
    timestamp: datetime
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)
    latest_prices: dict[str, float] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)

    # Useful caches the strategy can read but should treat as opaque.
    storage: Any = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def position(self, instrument: Instrument) -> Position:
        return self.positions.get(
            instrument.key,
            Position(instrument=instrument),
        )

    def last_price(self, instrument: Instrument, default: float | None = None) -> float | None:
        return self.latest_prices.get(instrument.key, default)

    def record_signal(
        self,
        instrument: Instrument | str,
        *,
        side: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """Report why the strategy acted (or would have) for the operator page.

        Signals are display-only. They never become orders; only returned
        :class:`OrderIntent` objects do.
        """
        key = instrument.key if isinstance(instrument, Instrument) else instrument
        self.extra.setdefault(_SIGNALS_KEY, []).append(
            {
                "timestamp": self.timestamp.isoformat(),
                "instrument": key,
                "side": side,
                "reason": reason,
                "evidence": dict(evidence or {}),
            }
        )

    def drain_signals(self) -> list[dict[str, Any]]:
        pending = self.extra.pop(_SIGNALS_KEY, None) or []
        return [item for item in pending if isinstance(item, dict)]


_SIGNALS_KEY = "_signals"


__all__ = [
    "Candle",
    "Decimal",
    "Depth",
    "Fill",
    "Instrument",
    "OrderIntent",
    "OrderRequest",
    "OrderUpdate",
    "Position",
    "StrategyContext",
    "Tick",
]
