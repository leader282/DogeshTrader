"""Enumerated domain values.

These intentionally mirror the constants Zerodha Kite Connect exposes (see
https://kite.trade/docs/connect/v3/orders/#order-varieties) so that broker
adapters can map them with minimal translation logic. The enums are framework
constants though, the broker adapter is the single source of truth for
broker-specific strings.
"""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    """High-level run mode.

    BACKTEST   - replay historical data, simulated fills, no broker calls
    PAPER      - live or replayed feed + PaperBroker; no real orders
    DRY_RUN    - validate intents through the risk engine and optionally Kite
                 margins/charges endpoints, but never place orders
    LIVE       - real broker order placement (guardrailed)
    """

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class Product(str, Enum):
    CNC = "CNC"
    MIS = "MIS"
    NRML = "NRML"
    MTF = "MTF"
    # CO/BO retained as constants for completeness even though Kite has
    # phased them out for most users.
    CO = "CO"
    BO = "BO"


class Variety(str, Enum):
    REGULAR = "regular"
    AMO = "amo"
    ICEBERG = "iceberg"
    CO = "co"
    AUCTION = "auction"


class Validity(str, Enum):
    DAY = "DAY"
    IOC = "IOC"
    TTL = "TTL"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"
    CDS = "CDS"
    BCD = "BCD"
    MCX = "MCX"
    NCO = "NCO"


class Segment(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO_FUT = "NFO-FUT"
    NFO_OPT = "NFO-OPT"
    BFO_FUT = "BFO-FUT"
    BFO_OPT = "BFO-OPT"
    CDS_FUT = "CDS-FUT"
    CDS_OPT = "CDS-OPT"
    BCD_FUT = "BCD-FUT"
    BCD_OPT = "BCD-OPT"
    MCX_FUT = "MCX-FUT"
    MCX_OPT = "MCX-OPT"
    INDICES = "INDICES"


class InstrumentType(str, Enum):
    EQ = "EQ"
    FUT = "FUT"
    CE = "CE"
    PE = "PE"
    INDEX = "INDEX"
    BOND = "BOND"
    ETF = "ETF"


class OrderStatus(str, Enum):
    """Normalized order status used across paper, dry-run and live."""

    PENDING = "PENDING"  # locally created, not sent
    SUBMITTED = "SUBMITTED"  # accepted by broker, waiting at exchange
    OPEN = "OPEN"  # exchange has it, working
    PARTIAL = "PARTIAL"  # partially filled
    COMPLETE = "COMPLETE"  # fully filled
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.COMPLETE, OrderStatus.CANCELLED, OrderStatus.REJECTED}
)
