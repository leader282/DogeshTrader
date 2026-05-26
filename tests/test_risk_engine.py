"""Risk engine tests."""

from __future__ import annotations

import pytest

from dogeshtrader.config import RiskConfig
from dogeshtrader.core.enums import OrderType, Side
from dogeshtrader.core.errors import RiskRejected
from dogeshtrader.core.models import Instrument, OrderIntent
from dogeshtrader.risk.risk_engine import RiskEngine

from .conftest import make_context


def _intent(infy: Instrument, **overrides):
    base = dict(
        strategy_id="t",
        instrument=infy,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        market_protection_pct=1.0,
        reason="unit",
    )
    base.update(overrides)
    return OrderIntent(**base)


def test_rejects_empty_strategy_id(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    with pytest.raises(RiskRejected):
        risk.check(
            _intent(infy, strategy_id="placeholder").model_copy(update={"strategy_id": ""}),
            make_context(infy),
        )


def test_rejects_quantity_above_max(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_intent(infy, quantity=10_000), make_context(infy))
    assert exc.value.rule == "max_order_quantity"


def test_clamps_quantity_to_notional(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    risk_config.max_order_notional = 5_000  # very tight cap
    risk_config.max_order_quantity = 100
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    req = risk.check(_intent(infy, quantity=50), make_context(infy, last_price=1500.0))
    # 5000 / 1500 = 3 lots
    assert req.quantity == 3


def test_requires_market_protection(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    bad = _intent(infy, market_protection_pct=None)
    with pytest.raises(RiskRejected) as exc:
        risk.check(bad, make_context(infy))
    assert exc.value.rule == "market_protection_required"


def test_lot_size_alignment_floors(infy: Instrument, risk_config: RiskConfig) -> None:
    infy_lot = infy.model_copy(update={"lot_size": 25})
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    req = risk.check(_intent(infy_lot, quantity=30), make_context(infy_lot))
    assert req.quantity == 25  # floored to lot size


def test_blocked_instrument(infy: Instrument, risk_config: RiskConfig) -> None:
    risk_config.blocked_instruments = ["NSE:INFY"]
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_intent(infy), make_context(infy))
    assert exc.value.rule == "instrument_not_allowed"


def test_circuit_breaker_trips_on_max_loss(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    risk.update_realised_pnl(-10_000)
    assert risk.breaker.is_tripped
    with pytest.raises(RiskRejected) as exc:
        risk.check(_intent(infy), make_context(infy))
    assert exc.value.rule == "circuit_breaker"
