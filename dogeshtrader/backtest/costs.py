"""Trading cost models used by the backtest engine.

Two models ship:

* :class:`BpsCostModel`              - flat bps on notional (simple, generic).
* :class:`IndiaEquitySimpleCostModel`- approximation of intraday equity charges
                                       (brokerage + STT + exch txn + GST + SEBI +
                                       stamp duty). Numbers are *approximate*
                                       and meant for backtests, not P&L
                                       certification. Adjust constants via the
                                       constructor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dogeshtrader.core.enums import Product, Side
from dogeshtrader.core.models import Instrument


class CostModel(Protocol):
    def estimate(
        self,
        *,
        instrument: Instrument,
        product: Product,
        side: Side,
        price: float,
        quantity: int,
    ) -> float: ...


@dataclass
class BpsCostModel:
    """Simple model: ``bps`` of notional applied per trade, plus a flat fee."""

    bps: float = 3.0
    fee_per_order: float = 20.0

    def estimate(
        self,
        *,
        instrument: Instrument,
        product: Product,
        side: Side,
        price: float,
        quantity: int,
    ) -> float:
        notional = price * quantity
        return (self.bps / 10_000.0) * notional + self.fee_per_order


@dataclass
class IndiaEquitySimpleCostModel:
    """Approximation of Indian retail intraday/delivery costs.

    Defaults follow Zerodha-style flat brokerage (Rs 20/order intraday,
    delivery free), STT 0.025% on intraday sell side, 0.1% on delivery (both
    sides), NSE exchange 0.00345% etc.

    The model is intentionally explicit; tweak the dataclass fields to match
    your broker.
    """

    brokerage_intraday: float = 20.0
    brokerage_delivery: float = 0.0
    stt_intraday_sell_pct: float = 0.025  # %
    stt_delivery_pct: float = 0.10  # %, both sides
    exchange_txn_nse_pct: float = 0.00345  # %
    sebi_charges_per_cr: float = 10.0  # Rs per crore
    gst_pct: float = 18.0  # on (brokerage + txn + sebi)
    stamp_duty_buy_pct: float = 0.003  # %, only on buy side

    def estimate(
        self,
        *,
        instrument: Instrument,
        product: Product,
        side: Side,
        price: float,
        quantity: int,
    ) -> float:
        turnover = price * quantity
        intraday = product in (Product.MIS, Product.CO, Product.BO, Product.MTF)
        brokerage = self.brokerage_intraday if intraday else self.brokerage_delivery

        stt = 0.0
        if intraday:
            if side == Side.SELL:
                stt = turnover * (self.stt_intraday_sell_pct / 100.0)
        else:
            stt = turnover * (self.stt_delivery_pct / 100.0)

        exch = turnover * (self.exchange_txn_nse_pct / 100.0)
        sebi = (turnover / 1e7) * self.sebi_charges_per_cr
        gst_base = brokerage + exch + sebi
        gst = gst_base * (self.gst_pct / 100.0)
        stamp = turnover * (self.stamp_duty_buy_pct / 100.0) if side == Side.BUY else 0.0

        return float(brokerage + stt + exch + sebi + gst + stamp)


def build_cost_model(name: str, **kwargs: float) -> CostModel:
    if name == "india_equity_simple":
        return IndiaEquitySimpleCostModel(**kwargs)  # type: ignore[arg-type]
    if name in ("bps_only", "bps"):
        return BpsCostModel(**kwargs)  # type: ignore[arg-type]
    raise ValueError(f"unknown cost model: {name}")


__all__ = [
    "BpsCostModel",
    "CostModel",
    "IndiaEquitySimpleCostModel",
    "build_cost_model",
]
