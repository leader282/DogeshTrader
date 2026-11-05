"""Pytest helpers shared across the suite."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from dogeshtrader.config import RiskConfig
from dogeshtrader.core.clock import IST
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Candle, Instrument, Position, StrategyContext
from dogeshtrader.data import CandleStore
from dogeshtrader.storage import Database, run_migrations

os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(__file__).resolve().parent.parent / ".pytest_cache" / "matplotlib")
)

SESSION_DAYS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5)]


@dataclass
class WorkspaceFiles:
    """Config files and stored candles for one throwaway data directory."""

    root: Path
    app: Path
    risk: Path
    strategy: Path

    def cli_args(self, *extra: str) -> list[str]:
        return ["--config", str(self.strategy), *self.common_args(), *extra]

    def common_args(self) -> list[str]:
        return ["--app-config", str(self.app), "--risk-config", str(self.risk)]


def trending_session(day: date, *, start: float = 100.0, step: float = 0.1) -> list[Candle]:
    """5-minute IST bars: a tight opening range, then a steady climb to the close."""
    out: list[Candle] = []
    ts = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    end = ts.replace(hour=15, minute=30)
    price = start
    while ts < end:
        in_range = ts.hour == 9 and ts.minute < 30
        if not in_range:
            price += step
        out.append(
            Candle(
                timestamp=ts,
                open=price,
                high=price + 0.05,
                low=price - 0.05,
                close=price,
                volume=10_000,
            )
        )
        ts += timedelta(minutes=5)
    return out


def write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def build_workspace(
    root: Path,
    *,
    strategy: str = "sample_opening_range",
    params: dict[str, Any] | None = None,
    app_overrides: dict[str, Any] | None = None,
    risk_overrides: dict[str, Any] | None = None,
    with_candles: bool = True,
) -> WorkspaceFiles:
    data_dir = root / "data"
    app = {
        "data_dir": str(data_dir),
        "db_path": str(data_dir / "test.sqlite"),
        "reports_dir": str(data_dir / "reports"),
        "costs": {"model": "bps_only", "brokerage_per_order": 0.0, "bps_fallback": 0.0},
        "slippage": {"model": "fixed_bps", "bps": 0.0},
        **(app_overrides or {}),
    }
    risk = {
        "max_order_quantity": 100,
        "max_order_notional": 1_000_000,
        "max_position_notional": 1_000_000,
        "max_daily_loss": 100_000,
        "max_orders_per_minute": 10_000,
        **(risk_overrides or {}),
    }
    strat = {
        "strategy": strategy,
        "strategy_id": "test_run",
        "initial_cash": 100_000,
        "instruments": [{"exchange": "NSE", "tradingsymbol": "DEMO"}],
        "interval": "5minute",
        "params": params if params is not None else {"quantity": 10, "range_minutes": 15},
        "backtest": {"from": SESSION_DAYS[0].isoformat(), "to": SESSION_DAYS[-1].isoformat()},
        "research": {"split": SESSION_DAYS[3].isoformat()},
    }
    if with_candles:
        demo = Instrument(exchange=Exchange.NSE, tradingsymbol="DEMO")
        candles = [c for day in SESSION_DAYS for c in trending_session(day)]
        CandleStore(data_dir).upsert(demo, "5minute", candles)
    return WorkspaceFiles(
        root=root,
        app=write_yaml(root / "app.yaml", app),
        risk=write_yaml(root / "risk.yaml", risk),
        strategy=write_yaml(root / "strategy.yaml", strat),
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> WorkspaceFiles:
    return build_workspace(tmp_path)


@pytest.fixture()
def memory_db() -> Database:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    return db


@pytest.fixture()
def infy() -> Instrument:
    return Instrument(
        exchange=Exchange.NSE,
        tradingsymbol="INFY",
        instrument_token=408065,
        tick_size=0.05,
        lot_size=1,
    )


@pytest.fixture()
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_order_quantity=100,
        max_order_notional=100_000,
        max_position_notional=250_000,
        max_daily_loss=5_000,
        max_open_orders=10,
        require_market_protection=True,
        default_market_protection_pct=1.0,
        max_orders_per_minute=600,
    )


def make_context(instrument: Instrument, last_price: float = 1500.0) -> StrategyContext:
    return StrategyContext(
        run_id="test",
        strategy_id="unit_test",
        mode="BACKTEST",
        timestamp=datetime(2024, 1, 2, 10, 0),
        cash=1_000_000,
        positions={instrument.key: Position(instrument=instrument)},
        latest_prices={instrument.key: last_price},
        config={"instrument_keys": [instrument.key]},
        params={},
        storage=None,
    )


def make_candles(
    start: datetime,
    closes: Iterable[float],
    *,
    minute_step: int = 5,
) -> list[Candle]:
    out: list[Candle] = []
    ts = start
    for close in closes:
        out.append(
            Candle(
                timestamp=ts,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000,
            )
        )
        ts = ts.replace(minute=ts.minute + minute_step) if ts.minute + minute_step < 60 else ts
    return out


__all__ = [
    "SESSION_DAYS",
    "WorkspaceFiles",
    "build_workspace",
    "make_candles",
    "make_context",
    "trending_session",
    "write_yaml",
]
