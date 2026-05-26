"""Backtest fill model determinism tests."""

from __future__ import annotations

from datetime import datetime

from dogeshtrader.backtest.costs import BpsCostModel
from dogeshtrader.backtest.fills import CandleFillModel
from dogeshtrader.backtest.slippage import FixedBpsSlippage, NoSlippage
from dogeshtrader.core.enums import OrderType, Product, Side, Variety
from dogeshtrader.core.models import Candle, Instrument, OrderRequest


def _make_request(infy: Instrument, **kwargs) -> OrderRequest:
    base = dict(
        intent_id="i_1",
        strategy_id="t",
        instrument=infy,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        product=Product.MIS,
        variety=Variety.REGULAR,
        tag="dogesh_t_001",
        idempotency_key="ABC",
    )
    base.update(kwargs)
    return OrderRequest(**base)


def _candle(o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        timestamp=datetime(2024, 1, 1, 10, 0),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000,
    )


def test_market_fills_at_open_with_no_slippage(infy: Instrument) -> None:
    model = CandleFillModel(slippage=NoSlippage(), costs=BpsCostModel(bps=0, fee_per_order=0))
    decision = model.evaluate(
        request=_make_request(infy),
        candle=_candle(1500, 1510, 1490, 1505),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    assert decision is not None
    assert decision.fill_price == 1500
    assert decision.quantity == 10


def test_market_fills_with_bps_slippage(infy: Instrument) -> None:
    model = CandleFillModel(
        slippage=FixedBpsSlippage(bps=5), costs=BpsCostModel(bps=0, fee_per_order=0)
    )
    decision = model.evaluate(
        request=_make_request(infy),
        candle=_candle(1500, 1510, 1490, 1505),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    assert decision is not None
    assert abs(decision.fill_price - 1500 * (1 + 5 / 10_000)) < 1e-6


def test_limit_buy_no_fill_if_low_above_limit(infy: Instrument) -> None:
    model = CandleFillModel(slippage=NoSlippage(), costs=BpsCostModel(0, 0))
    decision = model.evaluate(
        request=_make_request(infy, order_type=OrderType.LIMIT, price=1480.0),
        candle=_candle(1500, 1510, 1495, 1505),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    assert decision is None


def test_limit_sell_fills_at_limit_when_high_crosses(infy: Instrument) -> None:
    model = CandleFillModel(slippage=NoSlippage(), costs=BpsCostModel(0, 0))
    decision = model.evaluate(
        request=_make_request(infy, side=Side.SELL, order_type=OrderType.LIMIT, price=1520.0),
        candle=_candle(1500, 1530, 1495, 1525),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    assert decision is not None
    assert decision.fill_price == 1520.0


def test_sl_m_buy_triggers_when_high_crosses(infy: Instrument) -> None:
    model = CandleFillModel(slippage=NoSlippage(), costs=BpsCostModel(0, 0))
    decision = model.evaluate(
        request=_make_request(infy, order_type=OrderType.SL_M, trigger_price=1520.0),
        candle=_candle(1500, 1530, 1495, 1525),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    assert decision is not None
    assert decision.triggered is True
    assert decision.fill_price == 1520.0


def test_sl_buy_with_limit_doesnt_fill_above_limit(infy: Instrument) -> None:
    model = CandleFillModel(slippage=NoSlippage(), costs=BpsCostModel(0, 0))
    decision = model.evaluate(
        request=_make_request(infy, order_type=OrderType.SL, trigger_price=1520.0, price=1521.0),
        candle=_candle(1525, 1530, 1525, 1528),
        already_filled=0,
        triggered=False,
        last_close=None,
    )
    # triggered=True but no fill because low > limit
    assert decision is not None
    assert decision.quantity == 0
