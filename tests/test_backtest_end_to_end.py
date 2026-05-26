"""End-to-end backtest smoke test.

Drives the BacktestEngine through a synthetic candle stream and verifies that
the run completes, produces report files, and the equity curve is non-empty.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

from dogeshtrader.backtest.engine import BacktestEngine
from dogeshtrader.config import (
    AppConfig,
    CostsConfig,
    InstrumentRef,
    RiskConfig,
    SlippageConfig,
    StrategyConfig,
)
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Candle, Instrument
from dogeshtrader.storage import Database, run_migrations
from dogeshtrader.strategy.registry import get_strategy


def _sine_candles(start: datetime, n: int, base: float = 1000.0, amp: float = 20.0):
    inst = Instrument(
        exchange=Exchange.NSE,
        tradingsymbol="DEMO",
        instrument_token=1234,
        tick_size=0.05,
        lot_size=1,
    )
    for i in range(n):
        price = base + amp * math.sin(i / 5.0)
        ts = start + timedelta(minutes=5 * i)
        yield (
            inst,
            Candle(
                timestamp=ts,
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1000,
            ),
        )


def test_backtest_engine_runs_end_to_end(tmp_path: Path) -> None:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    inst = Instrument(
        exchange=Exchange.NSE,
        tradingsymbol="DEMO",
        instrument_token=1234,
        tick_size=0.05,
        lot_size=1,
    )

    sc = StrategyConfig(
        strategy="sample_sma_crossover",
        strategy_id="sma_e2e",
        initial_cash=100_000,
        instruments=[InstrumentRef(exchange="NSE", tradingsymbol="DEMO")],
        interval="5minute",
        params={
            "fast": 3,
            "slow": 8,
            "quantity": 1,
            "product": "MIS",
            "order_type": "MARKET",
            "market_protection_pct": 1.0,
            "exit_on_session_end": False,
            "allow_short": True,
        },
    )

    app_cfg = AppConfig(
        data_dir=tmp_path,
        db_path=tmp_path / "db.sqlite",
        reports_dir=tmp_path / "reports",
        live_trading_enabled=False,
        order_rate_per_second=100,
        order_rate_burst=100,
        costs=CostsConfig(model="bps_only", brokerage_per_order=0, bps_fallback=0),
        slippage=SlippageConfig(model="fixed_bps", bps=0, ticks=0),
    )
    risk_cfg = RiskConfig(
        max_order_quantity=100,
        max_order_notional=10_000_000,
        max_position_notional=10_000_000,
        max_daily_loss=1_000_000,
        max_open_orders=1000,
        require_market_protection=True,
        default_market_protection_pct=1.0,
        max_orders_per_minute=10_000,
    )

    strat_cls = get_strategy("sample_sma_crossover")
    strat = strat_cls(params=sc.params)

    candles = list(_sine_candles(datetime(2024, 1, 1, 9, 15), n=120))
    engine = BacktestEngine(
        strategy=strat,
        candles_provider=iter(candles),
        instruments=[inst],
        strategy_cfg=sc,
        app_cfg=app_cfg,
        risk_cfg=risk_cfg,
        db=db,
    )
    result = engine.run(output_dir=tmp_path / "reports")
    assert result.metrics.trade_count >= 1
    assert len(result.equity_curve) == 120
    assert (tmp_path / "reports" / "summary.json").exists()
    assert (tmp_path / "reports" / "trades.csv").exists()
    assert (tmp_path / "reports" / "orders.csv").exists()
    assert (tmp_path / "reports" / "report.html").exists()
