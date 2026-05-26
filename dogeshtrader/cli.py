"""DogeshTrader command-line interface.

Sub-commands:
* ``dogesh auth login-url``           - print the Kite login URL.
* ``dogesh auth callback-server``     - run the local FastAPI capture server.
* ``dogesh auth status``              - show stored token metadata.
* ``dogesh instruments download``     - refresh the instrument master.
* ``dogesh data historical``          - download candles for an instrument.
* ``dogesh backtest``                 - run a backtest.
* ``dogesh paper``                    - run live-paper (live ticks or candle replay).
* ``dogesh dry-run``                  - validate a strategy without placing orders.
* ``dogesh live``                     - LIVE trading (guardrailed; off by default).
* ``dogesh reconcile``                - reconcile local state with broker.
* ``dogesh report``                   - show / open a run's report.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from dogeshtrader.config import (
    AppSettings,
    load_app_config,
    load_risk_config,
    load_strategy_config,
)
from dogeshtrader.core.enums import Exchange, Mode
from dogeshtrader.core.errors import DogeshTraderError
from dogeshtrader.core.models import Instrument
from dogeshtrader.logging_config import configure_logging, get_logger
from dogeshtrader.storage import (
    AuditRepository,
    Database,
    OrderRepository,
    RunRepository,
    run_migrations,
)

app = typer.Typer(
    help="DogeshTrader: local-first algo-trading framework for Zerodha Kite Connect.",
    no_args_is_help=True,
    add_completion=False,
)

auth_app = typer.Typer(help="Kite authentication helpers.", no_args_is_help=True)
instruments_app = typer.Typer(help="Instrument master management.", no_args_is_help=True)
data_app = typer.Typer(help="Market data tooling.", no_args_is_help=True)

app.add_typer(auth_app, name="auth")
app.add_typer(instruments_app, name="instruments")
app.add_typer(data_app, name="data")

console = Console()
_log = get_logger("cli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bootstrap(config_path: Path | None = None) -> tuple[Database, AppSettings]:
    configure_logging()
    cfg = load_app_config(config_path)
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    db = Database(cfg.db_path)
    run_migrations(db)
    settings = AppSettings()
    return db, settings


def _resolve_instruments(db: Database, refs: list[dict]) -> list[Instrument]:
    from dogeshtrader.data import InstrumentMaster

    master = InstrumentMaster(db)
    out: list[Instrument] = []
    for ref in refs:
        ex = ref.get("exchange") if isinstance(ref, dict) else ref.exchange
        sym = ref.get("tradingsymbol") if isinstance(ref, dict) else ref.tradingsymbol
        inst = master.get(ex, sym)
        if inst is None:
            inst = Instrument(exchange=Exchange(ex), tradingsymbol=sym)
        out.append(inst)
    return out


# ---------------------------------------------------------------------------
# auth commands
# ---------------------------------------------------------------------------


@auth_app.command("login-url")
def auth_login_url(
    config: Path | None = typer.Option(None, help="Path to app config YAML."),
) -> None:
    """Print the Kite login URL for the configured API key."""
    from dogeshtrader.auth import build_login_url

    _, settings = _bootstrap(config)
    if not settings.kite_api_key:
        console.print("[red]KITE_API_KEY is empty in .env[/red]")
        raise typer.Exit(code=1)
    console.print(build_login_url(settings.kite_api_key))


@auth_app.command("callback-server")
def auth_callback_server(
    port: int = typer.Option(8000, help="Port for the local callback server."),
    host: str = typer.Option("127.0.0.1"),
    config: Path | None = typer.Option(None),
    max_seconds: int = typer.Option(600, help="Stop after this many seconds without a token."),
) -> None:
    """Run the FastAPI callback that captures the request_token."""
    from dogeshtrader.auth import CallbackServer, TokenStore

    db, settings = _bootstrap(config)
    cfg = load_app_config(config)
    if not settings.kite_api_key or not settings.kite_api_secret:
        console.print("[red]KITE_API_KEY and KITE_API_SECRET must be set in .env[/red]")
        raise typer.Exit(code=1)
    token_store = TokenStore(data_dir=cfg.data_dir, db=db)
    server = CallbackServer(token_store=token_store, settings=settings, host=host, port=port)
    console.print(f"Listening on http://{host}:{port}{server.callback_path}")
    console.print(
        f"Open the Kite login URL printed by [bold]dogesh auth login-url[/bold] "
        f"and ensure your Kite app redirect is set to http://{host}:{port}{server.callback_path}"
    )
    ok = server.run_until_token(max_seconds=max_seconds)
    if ok:
        console.print("[green]Token captured and saved.[/green]")
    else:
        console.print("[yellow]Timed out without capturing a token.[/yellow]")
        raise typer.Exit(code=2)


@auth_app.command("status")
def auth_status(
    config: Path | None = typer.Option(None),
) -> None:
    """Show the latest stored token (without exposing the secret)."""
    from dogeshtrader.auth import TokenStore

    db, settings = _bootstrap(config)
    cfg = load_app_config(config)
    if not settings.kite_api_key:
        console.print("[red]KITE_API_KEY is empty in .env[/red]")
        raise typer.Exit(code=1)
    store = TokenStore(data_dir=cfg.data_dir, db=db)
    payload = store.load(settings.kite_api_key)
    if not payload:
        console.print("[yellow]No token stored yet. Run `dogesh auth callback-server`.[/yellow]")
        raise typer.Exit(code=2)
    token = payload.get("access_token", "") or ""
    masked = token[:6] + "…" + token[-4:] if len(token) > 12 else "***"
    table = Table(title="Kite token", show_header=False)
    table.add_row("user_id", str(payload.get("user_id", "")))
    table.add_row("user_name", str(payload.get("user_name", "")))
    table.add_row("login_time", str(payload.get("login_time", "")))
    table.add_row("access_token", masked)
    console.print(table)


# ---------------------------------------------------------------------------
# instruments
# ---------------------------------------------------------------------------


@instruments_app.command("download")
def instruments_download(
    exchange: str | None = typer.Option(None, help="Optional exchange filter (NSE, BSE, ...)."),
    config: Path | None = typer.Option(None),
) -> None:
    """Refresh the local instrument master from Kite."""
    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.auth import TokenStore
    from dogeshtrader.data import InstrumentMaster

    db, settings = _bootstrap(config)
    cfg = load_app_config(config)
    store = TokenStore(data_dir=cfg.data_dir, db=db)
    token = store.access_token(settings.kite_api_key)
    if not token:
        console.print("[red]No access token; run `dogesh auth callback-server` first.[/red]")
        raise typer.Exit(code=1)
    client = KiteClient.from_settings(settings, token)
    adapter = client.instruments_adapter()
    inst_list = adapter.download(exchange=exchange)
    master = InstrumentMaster(db)
    saved = master.upsert_many(inst_list)
    console.print(
        f"[green]Saved {saved} instruments[/green] (exchange filter: {exchange or 'ALL'})"
    )


# ---------------------------------------------------------------------------
# data historical
# ---------------------------------------------------------------------------


@data_app.command("historical")
def data_historical(
    exchange: str = typer.Option(..., help="Exchange code (NSE, BSE, NFO...)."),
    symbol: str = typer.Option(..., help="Trading symbol (case-sensitive)."),
    interval: str = typer.Option("5minute"),
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    config: Path | None = typer.Option(None),
) -> None:
    """Download historical candles for a single instrument."""
    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.auth import TokenStore
    from dogeshtrader.data import CandleStore, HistoricalDownloader, InstrumentMaster

    db, settings = _bootstrap(config)
    cfg = load_app_config(config)
    store = TokenStore(data_dir=cfg.data_dir, db=db)
    token = store.access_token(settings.kite_api_key)
    if not token:
        console.print("[red]No access token; run auth flow first.[/red]")
        raise typer.Exit(code=1)
    client = KiteClient.from_settings(settings, token)
    master = InstrumentMaster(db)
    inst = master.get(exchange, symbol)
    if inst is None:
        console.print(
            f"[red]Instrument {exchange}:{symbol} not found in master. "
            f"Run `dogesh instruments download --exchange {exchange}` first.[/red]"
        )
        raise typer.Exit(code=1)
    candle_store = CandleStore(cfg.data_dir)
    downloader = HistoricalDownloader(client.market_data_adapter(), candle_store)
    progress = downloader.download(
        inst,
        from_dt=date.fromisoformat(from_),
        to_dt=date.fromisoformat(to),
        interval=interval,
    )
    console.print(
        f"[green]Downloaded {progress.candles} candles in {progress.chunks} chunks "
        f"for {inst.key} @ {interval}[/green]"
    )


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


@app.command("backtest")
def cmd_backtest(
    strategy: str = typer.Option(..., help="Strategy name (e.g. sample_sma_crossover)."),
    strategy_config: Path = typer.Option(..., "--config", help="Strategy YAML config."),
    app_config: Path | None = typer.Option(None, "--app-config"),
    output_dir: Path | None = typer.Option(None, help="Override reports output dir."),
) -> None:
    """Run a backtest using historical candles stored on disk."""
    from dogeshtrader.backtest.engine import BacktestEngine
    from dogeshtrader.data import CandleStore, InstrumentMaster
    from dogeshtrader.strategy.registry import get_strategy

    db, _ = _bootstrap(app_config)
    cfg = load_app_config(app_config)
    risk_cfg = load_risk_config()
    sc = load_strategy_config(strategy_config)

    master = InstrumentMaster(db)
    instruments: list[Instrument] = []
    for ref in sc.instruments:
        inst = master.get(ref.exchange, ref.tradingsymbol)
        if inst is None:
            inst = Instrument(
                exchange=Exchange(ref.exchange),
                tradingsymbol=ref.tradingsymbol,
            )
        instruments.append(inst)

    if not sc.backtest:
        console.print("[red]Strategy config must include `backtest.from` and `backtest.to`.[/red]")
        raise typer.Exit(code=1)

    candle_store = CandleStore(cfg.data_dir)
    candles_iter = candle_store.iter_chronological(
        instruments,
        sc.interval,
        from_dt=date.fromisoformat(sc.backtest.from_),
        to_dt=date.fromisoformat(sc.backtest.to),
    )

    strategy_cls = get_strategy(strategy or sc.strategy)
    strat = strategy_cls(params=sc.params)

    engine = BacktestEngine(
        strategy=strat,
        candles_provider=candles_iter,
        instruments=instruments,
        strategy_cfg=sc,
        app_cfg=cfg,
        risk_cfg=risk_cfg,
        db=db,
    )
    result = engine.run(output_dir=output_dir)
    _print_backtest_result(result)


def _print_backtest_result(result) -> None:
    table = Table(title=f"Backtest {result.run_id}", show_lines=False)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    m = result.metrics
    for label, value in [
        ("Start equity", m.start_equity),
        ("End equity", m.end_equity),
        ("Total return %", m.total_return_pct),
        ("CAGR %", m.cagr_pct),
        ("Max DD %", m.max_drawdown_pct),
        ("Sharpe-like", m.sharpe_like),
        ("Win rate %", m.win_rate_pct),
        ("Trades", m.trade_count),
        ("Profit factor", m.profit_factor),
        ("Total fees", m.total_fees),
    ]:
        table.add_row(label, str(value if value is not None else "n/a"))
    console.print(table)
    if result.report_paths:
        console.print(f"Reports: {result.report_paths.get('html')}")


# ---------------------------------------------------------------------------
# paper
# ---------------------------------------------------------------------------


@app.command("paper")
def cmd_paper(
    strategy: str = typer.Option(..., help="Strategy name."),
    strategy_config: Path = typer.Option(..., "--config"),
    app_config: Path | None = typer.Option(None, "--app-config"),
    replay_historical: bool = typer.Option(
        False,
        help="If set, replay historical candles instead of subscribing to live ticks.",
    ),
    max_events: int | None = typer.Option(
        None, help="Stop after N events; useful for smoke tests."
    ),
) -> None:
    """Run the strategy in PAPER mode.

    By default we connect to Kite's WebSocket for live ticks. Pass
    ``--replay-historical`` to instead replay candles already on disk.
    """
    from dogeshtrader.data import CandleStore, InstrumentMaster
    from dogeshtrader.simulation import LivePaperEngine
    from dogeshtrader.strategy.registry import get_strategy

    db, settings = _bootstrap(app_config)
    cfg = load_app_config(app_config)
    risk_cfg = load_risk_config()
    sc = load_strategy_config(strategy_config)

    master = InstrumentMaster(db)
    instruments = [master.ensure(ref.exchange, ref.tradingsymbol) for ref in sc.instruments]

    strategy_cls = get_strategy(strategy or sc.strategy)
    strat = strategy_cls(params=sc.params)

    engine = LivePaperEngine(
        strategy=strat,
        instruments=instruments,
        strategy_cfg=sc,
        app_cfg=cfg,
        risk_cfg=risk_cfg,
        db=db,
    )

    if replay_historical:
        if not sc.backtest:
            console.print(
                "[red]--replay-historical needs backtest.from/to in the strategy config.[/red]"
            )
            raise typer.Exit(code=1)
        candle_store = CandleStore(cfg.data_dir)
        candles_iter = candle_store.iter_chronological(
            instruments,
            sc.interval,
            from_dt=date.fromisoformat(sc.backtest.from_),
            to_dt=date.fromisoformat(sc.backtest.to),
        )
        import threading

        def _feed() -> None:
            for inst, candle in candles_iter:
                engine.push_candle(inst, candle)
            engine.stop()

        thread = threading.Thread(target=_feed, name="paper-replay", daemon=True)
        thread.start()
        try:
            engine.run(max_events=max_events)
        finally:
            thread.join(timeout=2.0)
        console.print(f"[green]Paper replay complete: run_id={engine.run_id}[/green]")
        return

    # Live ticks path.
    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.auth import TokenStore

    token_store = TokenStore(data_dir=cfg.data_dir, db=db)
    token = token_store.access_token(settings.kite_api_key)
    if not token:
        console.print("[red]No access token; run auth flow first.[/red]")
        raise typer.Exit(code=1)
    client = KiteClient.from_settings(settings, token)
    ws = client.websocket_adapter()
    ws.on_tick(engine.push_tick)

    tokens = [i.instrument_token for i in instruments if i.instrument_token]
    if not tokens:
        console.print(
            "[red]None of the configured instruments have an instrument_token. "
            "Run `dogesh instruments download` first.[/red]"
        )
        raise typer.Exit(code=1)
    try:
        ws.connect(tokens=tokens, mode=cfg.default_quote_mode, threaded=True)
        engine.run(max_events=max_events)
    finally:
        ws.stop()
    console.print(f"[green]Paper run complete: run_id={engine.run_id}[/green]")


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


@app.command("dry-run")
def cmd_dry_run(
    strategy: str = typer.Option(..., help="Strategy name."),
    strategy_config: Path = typer.Option(..., "--config"),
    app_config: Path | None = typer.Option(None, "--app-config"),
    n_intents: int = typer.Option(
        5, help="Generate up to N synthetic intents from the strategy at the latest candle."
    ),
) -> None:
    """Replay the latest available candles through the strategy and validate
    its intents through the risk engine and (optionally) Kite margins,
    *without ever placing an order*."""
    from dogeshtrader.brokers.dry_run_broker import DryRunBroker
    from dogeshtrader.data import CandleStore, InstrumentMaster
    from dogeshtrader.execution.order_router import OrderRouter
    from dogeshtrader.risk.risk_engine import RiskEngine
    from dogeshtrader.strategy.registry import get_strategy

    db, _ = _bootstrap(app_config)
    cfg = load_app_config(app_config)
    risk_cfg = load_risk_config()
    sc = load_strategy_config(strategy_config)

    master = InstrumentMaster(db)
    instruments = [master.ensure(ref.exchange, ref.tradingsymbol) for ref in sc.instruments]

    strategy_cls = get_strategy(strategy or sc.strategy)
    strat = strategy_cls(params=sc.params)
    risk = RiskEngine(risk_cfg, tag_prefix=cfg.order_tag_prefix, enforce_market_hours=False)
    broker = DryRunBroker()
    router = OrderRouter(mode=Mode.DRY_RUN, broker=broker, risk=risk)

    store = CandleStore(cfg.data_dir)
    candles = store.iter_chronological(instruments, sc.interval)
    from dogeshtrader.core.models import Position, StrategyContext

    positions = {i.key: Position(instrument=i) for i in instruments}
    latest_prices: dict[str, float] = {}
    ctx = StrategyContext(
        run_id="dry_run",
        strategy_id=sc.strategy_id,
        mode="DRY_RUN",
        timestamp=datetime.now(),
        cash=sc.initial_cash,
        positions=positions,
        latest_prices=latest_prices,
        config={"instrument_keys": [i.key for i in instruments]},
        params=sc.params,
        storage=db,
    )
    strat.on_start(ctx)
    last_candles: list = list(candles)
    if not last_candles:
        console.print("[red]No candles on disk - run `dogesh data historical` first.[/red]")
        raise typer.Exit(code=1)
    for inst, candle in last_candles[-200:]:
        latest_prices[inst.key] = candle.close
        ctx.timestamp = candle.timestamp
        ctx.extra["_current_instrument"] = inst
        intents = strat.on_candle(ctx, candle)
        if intents:
            result = router.route(intents[:n_intents], ctx)
            for upd in result.accepted:
                console.print(f"DRY_RUN OK   {upd.raw}")
            for intent, msg in result.risk_rejected:
                console.print(f"[yellow]DRY_RUN risk[/yellow] {intent.instrument.key} -> {msg}")


# ---------------------------------------------------------------------------
# live
# ---------------------------------------------------------------------------


@app.command("live")
def cmd_live(
    strategy: str = typer.Option(..., help="Strategy name."),
    strategy_config: Path = typer.Option(..., "--config"),
    app_config: Path | None = typer.Option(None, "--app-config"),
    i_understand_this_places_real_orders: bool = typer.Option(
        False,
        "--i-understand-this-places-real-orders",
        help="Required confirmation flag. WILL place real orders if all guardrails pass.",
    ),
) -> None:
    """LIVE trading. DISABLED by default. Multiple guardrails must align.

    This command exits with a clear error unless ALL of the following are true:
    * ``live_trading_enabled: true`` in the app config
    * ``--i-understand-this-places-real-orders`` passed on the command line
    * a valid Kite access token is available
    * the strategy config has a non-empty ``strategy_id``
    * the configured order tag prefix matches the dogesh_ pattern
    """
    db, settings = _bootstrap(app_config)
    cfg = load_app_config(app_config)
    risk_cfg = load_risk_config()
    sc = load_strategy_config(strategy_config)

    if not cfg.live_trading_enabled:
        console.print(
            "[red]live_trading_enabled is false in app config; refusing to start LIVE.[/red]"
        )
        raise typer.Exit(code=1)
    if not i_understand_this_places_real_orders:
        console.print("[red]Missing --i-understand-this-places-real-orders[/red]")
        raise typer.Exit(code=1)
    if not sc.strategy_id:
        console.print("[red]strategy_id is required for LIVE.[/red]")
        raise typer.Exit(code=1)
    if not cfg.order_tag_prefix.startswith("dogesh"):
        console.print(
            "[red]order_tag_prefix must start with 'dogesh' for compatibility with reconciliation.[/red]"
        )
        raise typer.Exit(code=1)

    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.auth import TokenStore
    from dogeshtrader.brokers.kite_broker import KiteBroker
    from dogeshtrader.data import InstrumentMaster
    from dogeshtrader.execution.idempotency import IdempotencyGuard
    from dogeshtrader.execution.order_router import OrderRouter
    from dogeshtrader.execution.rate_limiter import RateLimiter
    from dogeshtrader.risk.risk_engine import RiskEngine
    from dogeshtrader.simulation import LivePaperEngine  # only for engine scaffolding
    from dogeshtrader.storage import IdempotencyRepository
    from dogeshtrader.strategy.registry import get_strategy

    store = TokenStore(data_dir=cfg.data_dir, db=db)
    token = store.access_token(settings.kite_api_key)
    if not token:
        console.print("[red]Missing access token. Run auth flow.[/red]")
        raise typer.Exit(code=1)

    client = KiteClient.from_settings(settings, token)
    master = InstrumentMaster(db)
    instruments = [master.ensure(ref.exchange, ref.tradingsymbol) for ref in sc.instruments]
    strategy_cls = get_strategy(strategy or sc.strategy)
    strat = strategy_cls(params=sc.params)
    risk = RiskEngine(risk_cfg, tag_prefix=cfg.order_tag_prefix, enforce_market_hours=True)
    broker = KiteBroker(client.kite)
    idem_repo = IdempotencyRepository(db)

    # Reuse the live-paper engine scaffolding for context/queue but swap the broker.
    engine = LivePaperEngine(
        strategy=strat,
        instruments=instruments,
        strategy_cfg=sc,
        app_cfg=cfg,
        risk_cfg=risk_cfg,
        mode=Mode.LIVE,
        db=db,
    )
    engine.set_broker(broker)
    engine.router = OrderRouter(
        mode=Mode.LIVE,
        broker=broker,
        risk=risk,
        idempotency=IdempotencyGuard(idem_repo, engine.run_id),
        rate_limiter=RateLimiter(cfg.order_rate_per_second, cfg.order_rate_burst),
        live_trading_enabled=True,
        live_confirmation=True,
    )

    console.print("[bold yellow]LIVE TRADING ENABLED.[/bold yellow] Press Ctrl-C to stop.")
    ws = client.websocket_adapter()
    ws.on_tick(engine.push_tick)
    ws.on_order_update(lambda raw: engine.push_order_update(broker.normalize_order_update(raw)))
    tokens = [i.instrument_token for i in instruments if i.instrument_token]
    if not tokens:
        console.print(
            "[red]None of the configured instruments have an instrument_token. "
            "Run `dogesh instruments download` first.[/red]"
        )
        raise typer.Exit(code=1)
    try:
        ws.connect(tokens=tokens, mode=cfg.default_quote_mode, threaded=True)
        engine.run()
    finally:
        ws.stop()


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


@app.command("reconcile")
def cmd_reconcile(
    config: Path | None = typer.Option(None),
) -> None:
    """Reconcile local order state with the broker."""
    from dogeshtrader.adapters.kite import KiteClient
    from dogeshtrader.auth import TokenStore
    from dogeshtrader.brokers.kite_broker import KiteBroker
    from dogeshtrader.execution.reconciliation import Reconciliation

    db, settings = _bootstrap(config)
    cfg = load_app_config(config)
    store = TokenStore(data_dir=cfg.data_dir, db=db)
    token = store.access_token(settings.kite_api_key)
    if not token:
        console.print("[red]No access token.[/red]")
        raise typer.Exit(code=1)
    client = KiteClient.from_settings(settings, token)
    broker = KiteBroker(client.kite)
    reconcile = Reconciliation(OrderRepository(db), AuditRepository(db))
    items = reconcile.reconcile(broker.orders())
    table = Table(title="Reconciliation")
    table.add_column("Kind")
    table.add_column("Order/Broker id")
    table.add_column("Delta")
    for item in items:
        oid = (item.local or {}).get("order_id") or (item.broker.order_id if item.broker else "")
        table.add_row(item.kind, str(oid), str(item.delta))
    console.print(table)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@app.command("report")
def cmd_report(
    run_id: str | None = typer.Option(None, help="Run id to show; defaults to most recent."),
    config: Path | None = typer.Option(None),
) -> None:
    """List recent runs or show details for a specific run."""
    db, _ = _bootstrap(config)
    runs = RunRepository(db)
    if run_id is None:
        recent = runs.list_recent()
        if not recent:
            console.print("[yellow]No runs recorded yet.[/yellow]")
            return
        table = Table(title="Recent runs")
        for col in ("run_id", "mode", "strategy", "strategy_id", "status", "started_at"):
            table.add_column(col)
        for r in recent:
            table.add_row(
                r["run_id"],
                r["mode"],
                r["strategy"],
                r["strategy_id"],
                r["status"],
                r["started_at"],
            )
        console.print(table)
        return

    record = runs.get(run_id)
    if not record:
        console.print(f"[red]Unknown run_id {run_id}[/red]")
        raise typer.Exit(code=1)
    console.print(record)


def main() -> None:
    try:
        app()
    except DogeshTraderError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


__all__ = ["app", "main"]
