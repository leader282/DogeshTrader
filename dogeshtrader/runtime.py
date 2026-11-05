"""Composition root.

Every run mode is assembled here from configuration. The CLI, the tests, and
any later service entry point share this one wiring path, so a new mode or a
new broker is added in one place instead of in each command.

Nothing in this module prints. Failures raise :class:`DogeshTraderError`
subclasses and the caller decides how to present them.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from dogeshtrader.config import (
    AppConfig,
    AppSettings,
    InstrumentRef,
    RiskConfig,
    StrategyConfig,
    load_app_config,
    load_risk_config,
    load_strategy_config,
)
from dogeshtrader.core.enums import Exchange, Mode
from dogeshtrader.core.errors import AuthError, ConfigError, DataError
from dogeshtrader.core.models import Candle, Instrument, Position, StrategyContext
from dogeshtrader.logging_config import configure_logging
from dogeshtrader.operator.state import state_path
from dogeshtrader.risk.kill_switch import KillSwitch, kill_switch_path
from dogeshtrader.storage import Database, run_migrations
from dogeshtrader.strategy.base import Strategy
from dogeshtrader.strategy.registry import get_strategy

if TYPE_CHECKING:
    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.backtest.engine import BacktestEngine, BacktestResult
    from dogeshtrader.research import ResearchReport
    from dogeshtrader.simulation import LivePaperEngine


class Workspace:
    """Configuration, storage, and operator files for one data directory."""

    def __init__(
        self,
        app: AppConfig,
        db: Database,
        *,
        settings: AppSettings | None = None,
        risk_config_path: Path | str | None = None,
        risk: RiskConfig | None = None,
    ) -> None:
        self.app = app
        self.db = db
        self.settings = settings or AppSettings()
        self._risk_config_path = risk_config_path
        if risk is not None:
            self.__dict__["risk"] = risk

    @classmethod
    def open(
        cls,
        app_config: Path | str | None = None,
        *,
        risk_config: Path | str | None = None,
    ) -> Workspace:
        configure_logging()
        app = load_app_config(app_config)
        Path(app.data_dir).mkdir(parents=True, exist_ok=True)
        db = Database(app.db_path)
        run_migrations(db)
        return cls(app, db, risk_config_path=risk_config)

    @cached_property
    def risk(self) -> RiskConfig:
        return load_risk_config(self._risk_config_path)

    @property
    def kill_switch(self) -> KillSwitch:
        return KillSwitch(kill_switch_path(self.app.data_dir))

    @property
    def operator_state_path(self) -> Path:
        return state_path(self.app.data_dir)

    def resolve_instruments(self, refs: list[InstrumentRef]) -> list[Instrument]:
        """Prefer the downloaded instrument master; fall back to a bare equity."""
        from dogeshtrader.data import InstrumentMaster

        master = InstrumentMaster(self.db)
        out: list[Instrument] = []
        for ref in refs:
            inst = master.get(ref.exchange, ref.tradingsymbol)
            if inst is None:
                inst = Instrument(exchange=Exchange(ref.exchange), tradingsymbol=ref.tradingsymbol)
            out.append(inst)
        return out

    def access_token(self) -> str:
        from dogeshtrader.auth import TokenStore

        if not self.settings.kite_api_key:
            raise AuthError("KITE_API_KEY is empty in .env")
        token = TokenStore(data_dir=self.app.data_dir, db=self.db).access_token(
            self.settings.kite_api_key
        )
        if not token:
            raise AuthError("no Kite access token stored; run `dogesh auth callback-server` first")
        return token

    def kite_client(self) -> KiteClient:
        from dogeshtrader.adapters.kite import KiteClient

        return KiteClient.from_settings(self.settings, self.access_token())


@dataclass
class StrategyRun:
    """A strategy, its config, and its resolved universe inside a workspace."""

    workspace: Workspace
    config: StrategyConfig
    strategy: Strategy
    instruments: list[Instrument] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        workspace: Workspace,
        config_path: Path | str,
        *,
        strategy_name: str | None = None,
    ) -> StrategyRun:
        config = load_strategy_config(config_path)
        strategy_cls = get_strategy(strategy_name or config.strategy)
        return cls(
            workspace=workspace,
            config=config,
            strategy=strategy_cls(params=config.params),
            instruments=workspace.resolve_instruments(config.instruments),
        )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def backtest_window(self) -> tuple[date, date]:
        window = self.config.backtest
        if window is None:
            raise ConfigError("strategy config must include backtest.from and backtest.to")
        return date.fromisoformat(window.from_), date.fromisoformat(window.to)

    def historical_candles(self) -> Iterator[tuple[Instrument, Candle]]:
        from dogeshtrader.data import CandleStore

        start, end = self.backtest_window()
        return CandleStore(self.workspace.app.data_dir).iter_chronological(
            self.instruments, self.config.interval, from_dt=start, to_dt=end
        )

    def instrument_tokens(self) -> list[int]:
        tokens = [i.instrument_token for i in self.instruments if i.instrument_token]
        if not tokens:
            raise DataError(
                "none of the configured instruments have an instrument_token; "
                "run `dogesh instruments download` first"
            )
        return tokens

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------

    def backtest_engine(self) -> BacktestEngine:
        from dogeshtrader.backtest.engine import BacktestEngine

        return BacktestEngine(
            strategy=self.strategy,
            candles_provider=self.historical_candles(),
            instruments=self.instruments,
            strategy_cfg=self.config,
            app_cfg=self.workspace.app,
            risk_cfg=self.workspace.risk,
            db=self.workspace.db,
        )

    def paper_engine(self) -> LivePaperEngine:
        from dogeshtrader.simulation import LivePaperEngine

        return LivePaperEngine(
            strategy=self.strategy,
            instruments=self.instruments,
            strategy_cfg=self.config,
            app_cfg=self.workspace.app,
            risk_cfg=self.workspace.risk,
            db=self.workspace.db,
        )

    def live_engine(self, client: KiteClient, *, confirmed: bool) -> LivePaperEngine:
        from dogeshtrader.brokers.kite_broker import KiteBroker
        from dogeshtrader.simulation import LivePaperEngine

        check_live_guardrails(self.workspace.app, self.config, confirmed=confirmed)
        return LivePaperEngine(
            strategy=self.strategy,
            instruments=self.instruments,
            strategy_cfg=self.config,
            app_cfg=self.workspace.app,
            risk_cfg=self.workspace.risk,
            mode=Mode.LIVE,
            db=self.workspace.db,
            broker=KiteBroker(client.kite),
            live_confirmation=confirmed,
        )

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    def run_backtest(self, output_dir: Path | None = None) -> BacktestResult:
        return self.backtest_engine().run(output_dir=output_dir)

    def run_research(
        self,
        *,
        split: date | None = None,
        output_dir: Path | None = None,
    ) -> tuple[BacktestResult, ResearchReport, Path]:
        from dogeshtrader.research import evaluate_windows, write_research_report

        if split is None:
            if self.config.research is None:
                raise ConfigError("pass --split or set research.split in the strategy config")
            split = date.fromisoformat(self.config.research.split)
        app = self.workspace.app
        result = self.run_backtest(output_dir)
        report = evaluate_windows(
            equity_curve=result.equity_curve,
            trades=result.trades,
            split=split,
            costs_note=(
                f"costs={app.costs.model}, brokerage={app.costs.brokerage_per_order}, "
                f"slippage={app.slippage.model} {app.slippage.bps} bps"
            ),
        )
        report_dir = output_dir or (Path(app.reports_dir) / result.run_id)
        written = write_research_report(report_dir / "research.json", report)
        return result, report, written

    def replay_paper(self, *, max_events: int | None = None) -> LivePaperEngine:
        """Feed stored candles through the paper engine on a background thread."""
        engine = self.paper_engine()
        candles = self.historical_candles()

        def _feed() -> None:
            for inst, candle in candles:
                engine.push_candle(inst, candle)
            engine.stop()

        thread = threading.Thread(target=_feed, name="paper-replay", daemon=True)
        thread.start()
        try:
            engine.run(max_events=max_events)
        finally:
            thread.join(timeout=2.0)
        return engine

    def dry_run(self, *, max_intents: int = 5, tail: int = 200) -> list[tuple[bool, str]]:
        """Push the most recent stored candles through risk without placing orders.

        Returns ``(accepted, detail)`` rows in the order they were produced.
        """
        from dogeshtrader.brokers.dry_run_broker import DryRunBroker
        from dogeshtrader.data import CandleStore
        from dogeshtrader.execution.order_router import OrderRouter
        from dogeshtrader.risk.risk_engine import RiskEngine

        app = self.workspace.app
        risk = RiskEngine(
            self.workspace.risk,
            tag_prefix=app.order_tag_prefix,
            enforce_market_hours=False,
            kill_switch=self.workspace.kill_switch,
            market_interval=self.config.interval,
        )
        router = OrderRouter(mode=Mode.DRY_RUN, broker=DryRunBroker(), risk=risk)
        candles = list(
            CandleStore(app.data_dir).iter_chronological(self.instruments, self.config.interval)
        )
        if not candles:
            raise DataError("no candles on disk; run `dogesh data historical` first")

        latest_prices: dict[str, float] = {}
        ctx = StrategyContext(
            run_id="dry_run",
            strategy_id=self.config.strategy_id,
            mode="DRY_RUN",
            timestamp=datetime.now(),
            cash=self.config.initial_cash,
            positions={i.key: Position(instrument=i) for i in self.instruments},
            latest_prices=latest_prices,
            config=self._strategy_ctx_config(),
            params=self.config.params,
            storage=self.workspace.db,
        )
        self.strategy.on_start(ctx)
        rows: list[tuple[bool, str]] = []
        for inst, candle in candles[-tail:]:
            latest_prices[inst.key] = candle.close
            ctx.timestamp = candle.timestamp
            ctx.extra["_current_instrument"] = inst
            risk.note_candle(inst.key, candle)
            intents = self.strategy.on_candle(ctx, candle)
            if not intents:
                continue
            result = router.route(intents[:max_intents], ctx)
            rows.extend((True, str(upd.raw)) for upd in result.accepted)
            rows.extend(
                (False, f"{intent.instrument.key} -> {msg}") for intent, msg in result.risk_rejected
            )
        return rows

    def _strategy_ctx_config(self) -> dict[str, object]:
        risk = self.workspace.risk
        return {
            "instrument_keys": [i.key for i in self.instruments],
            "session_start": risk.session_start,
            "session_end": risk.session_end,
            "interval": self.config.interval,
        }


def check_live_guardrails(
    app: AppConfig,
    strategy: StrategyConfig,
    *,
    confirmed: bool,
) -> None:
    """Refuse to build a LIVE engine unless every opt-in is present."""
    if not app.live_trading_enabled:
        raise ConfigError("live_trading_enabled is false in app config; refusing to start LIVE")
    if not confirmed:
        raise ConfigError("missing --i-understand-this-places-real-orders")
    if not strategy.strategy_id:
        raise ConfigError("strategy_id is required for LIVE")
    if not app.order_tag_prefix.startswith("dogesh"):
        raise ConfigError(
            "order_tag_prefix must start with 'dogesh' so reconciliation can find orders"
        )


__all__ = ["StrategyRun", "Workspace", "check_live_guardrails"]
