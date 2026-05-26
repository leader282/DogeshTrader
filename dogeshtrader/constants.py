"""Framework-wide constants. Kept small and grep-able on purpose."""

from __future__ import annotations

# Order tag prefix; the operator config can override this, but every order
# placed by DogeshTrader must start with one of these to make reconciliation
# obvious on the Kite dashboard.
DEFAULT_ORDER_TAG_PREFIX = "dogesh_"

# Kite tag field has a max length of 20 chars.
KITE_TAG_MAX_LEN = 20

# Default supported candle intervals (mirrors Kite Connect historical API).
HISTORICAL_INTERVALS = (
    "minute",
    "3minute",
    "5minute",
    "10minute",
    "15minute",
    "30minute",
    "60minute",
    "day",
)

# Conservative cap below Zerodha's documented 10 req/sec order rate.
DEFAULT_ORDER_RATE_PER_SECOND = 5

# Default Indian retail tax/charge constants (used by the simple cost model
# only - the *india_equity_detailed* model takes its own config).
DEFAULT_BROKERAGE_FLAT = 20.0  # INR per executed order, intraday
DEFAULT_BROKERAGE_DELIVERY = 0.0  # most retail brokers don't charge for delivery
DEFAULT_BPS_FALLBACK = 3.0

# Auth
TOKEN_STORE_FILE = "kite_token.json"
DEFAULT_CALLBACK_PATH = "/kite/callback"
DEFAULT_CALLBACK_PORT = 8000

# Generated artifact prefixes - reportable IDs use these so they sort nicely.
RUN_ID_PREFIX = "run_"
ORDER_INTERNAL_PREFIX = "ord_"
TRADE_INTERNAL_PREFIX = "trd_"

# Numerical epsilon for tick-size / lot-size rounding comparisons.
TICK_EPSILON = 1e-6

# Special metadata keys.
META_BACKTEST_FILL_PRICE = "_backtest_fill_price"
META_CANDLE_TS = "_candle_ts"

__all__ = [
    "DEFAULT_BPS_FALLBACK",
    "DEFAULT_BROKERAGE_DELIVERY",
    "DEFAULT_BROKERAGE_FLAT",
    "DEFAULT_CALLBACK_PATH",
    "DEFAULT_CALLBACK_PORT",
    "DEFAULT_ORDER_RATE_PER_SECOND",
    "DEFAULT_ORDER_TAG_PREFIX",
    "HISTORICAL_INTERVALS",
    "KITE_TAG_MAX_LEN",
    "META_BACKTEST_FILL_PRICE",
    "META_CANDLE_TS",
    "ORDER_INTERNAL_PREFIX",
    "RUN_ID_PREFIX",
    "TICK_EPSILON",
    "TOKEN_STORE_FILE",
    "TRADE_INTERNAL_PREFIX",
]
