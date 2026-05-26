"""OrderRouter mode / guardrail tests."""

from __future__ import annotations

import pytest

from dogeshtrader.brokers.dry_run_broker import DryRunBroker
from dogeshtrader.brokers.paper_broker import PaperBroker
from dogeshtrader.config import RiskConfig
from dogeshtrader.core.enums import Mode, OrderStatus, OrderType, Side
from dogeshtrader.core.errors import OrderRejected
from dogeshtrader.core.models import Instrument, OrderIntent
from dogeshtrader.execution.idempotency import IdempotencyGuard
from dogeshtrader.execution.order_router import OrderRouter
from dogeshtrader.risk.risk_engine import RiskEngine
from dogeshtrader.storage import Database, IdempotencyRepository, run_migrations

from .conftest import make_context


def _intent(infy: Instrument, **k):
    base = dict(
        strategy_id="t",
        instrument=infy,
        side=Side.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        market_protection_pct=1.0,
        reason="unit",
    )
    base.update(k)
    return OrderIntent(**base)


def test_paper_mode_routes_to_paper_broker(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    broker = PaperBroker()
    router = OrderRouter(mode=Mode.PAPER, broker=broker, risk=risk)
    result = router.route([_intent(infy)], make_context(infy))
    assert len(result.accepted) == 1
    assert result.accepted[0].status == OrderStatus.OPEN


def test_dry_run_never_places(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    broker = DryRunBroker()
    router = OrderRouter(mode=Mode.DRY_RUN, broker=broker, risk=risk)
    result = router.route([_intent(infy)], make_context(infy))
    assert len(result.accepted) == 1
    upd = result.accepted[0]
    assert upd.status_message == "dry-run validated"
    assert upd.raw and "would_send" in upd.raw


def test_live_mode_requires_both_guardrails(infy: Instrument, risk_config: RiskConfig) -> None:
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    broker = PaperBroker()  # broker irrelevant; we expect guardrail to refuse
    router = OrderRouter(
        mode=Mode.LIVE,
        broker=broker,
        risk=risk,
        live_trading_enabled=False,
        live_confirmation=False,
    )
    with pytest.raises(OrderRejected):
        router.route([_intent(infy)], make_context(infy))

    router2 = OrderRouter(
        mode=Mode.LIVE,
        broker=broker,
        risk=risk,
        live_trading_enabled=True,
        live_confirmation=False,
    )
    with pytest.raises(OrderRejected):
        router2.route([_intent(infy)], make_context(infy))


def test_idempotency_dedupes_duplicate_requests(infy: Instrument, risk_config: RiskConfig) -> None:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    repo = IdempotencyRepository(db)
    guard = IdempotencyGuard(repo, run_id="r1")
    risk = RiskEngine(risk_config, enforce_market_hours=False)
    broker = PaperBroker()
    router = OrderRouter(mode=Mode.PAPER, broker=broker, risk=risk, idempotency=guard)

    intent = _intent(infy, quantity=1, idempotency_key="dup-key")
    ctx = make_context(infy)
    # First placement OK.
    res = router.route([intent], ctx)
    assert len(res.accepted) == 1
    # Second placement with the same key should be deduped.
    res2 = router.route([intent], ctx)
    assert len(res2.accepted) == 0
    assert len(res2.duplicates) == 1
