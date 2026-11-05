# DogeshTrader

> **This is software infrastructure, not financial advice.** Nothing in this
> repository is a recommendation to buy, sell, or hold any instrument. You are
> 100% responsible for any orders that originate from your Kite account.
> Never start with live trading. Verify with backtests, paper, and dry-run
> first. The defaults of this framework actively refuse to place real orders.

A local-first, modular algorithmic-trading framework for Zerodha Kite Connect.
DogeshTrader is built around four explicit run modes (BACKTEST, PAPER,
DRY_RUN, LIVE) and a strict separation of concerns: strategies emit *intents*
and never touch the network or the broker themselves. Everything else - risk,
sizing, rate limiting, idempotency, execution, reconciliation - is generic
across instruments, exchanges, order types and product types.

## Highlights

- One strategy contract used by **backtest, paper, dry-run, and live** —
  no rewrites between research and deployment.
- Pre-trade risk engine (allow/block lists, notional caps, lot/tick alignment,
  market-protection enforcement, idempotency, circuit breakers).
- Paper broker with deterministic candle/tick matching, configurable
  slippage and an Indian-equity costs model.
- Backtest engine with per-bar fills, charges, equity curve, trade ledger,
  HTML report and JSON summary.
- Kite Connect adapters for instruments, market data, orders, portfolio,
  margins, GTT, WebSocket ticks, and postbacks.
- Local SQLite for runs/orders/fills/auth + Parquet for market data.
- Typer-based CLI (`dogesh ...`).
- Strict, typed configuration via Pydantic v2 + pydantic-settings.

## Run modes

| Mode | Data | Broker | Real orders? | Typical use |
|------|------|--------|--------------|-------------|
| `BACKTEST` | Historical candles on disk | `PaperBroker` | No | Research / strategy tuning |
| `PAPER` | Live WebSocket *or* candle replay | `PaperBroker` | No | "What would have happened?" without risk |
| `DRY_RUN` | Live data (optional) | `DryRunBroker` | No | Validate intents through risk + Kite margins |
| `LIVE` | Live WebSocket | `KiteBroker` | **Yes**, with guardrails | Real trading |

`LIVE` requires **both** `live_trading_enabled: true` in the app config
**and** the `--i-understand-this-places-real-orders` CLI flag. Either missing
and the framework refuses to route a single order.

## Repository layout

```
dogeshtrader/
  pyproject.toml             # metadata, tool config, strategy entry points
  Makefile                   # make check = lint + typecheck + tests with coverage
  configs/                   # example YAML configs
  dogeshtrader/
    runtime.py               # composition root: every run mode is wired here
    cli/                     # thin Typer layer (`dogesh`); no wiring logic
    config.py                # AppConfig + RiskConfig + StrategyConfig
    core/                    # domain types, interfaces, enums, clock, errors
    strategy/                # base class, registry/plugins, signals, strategies
    risk/                    # risk engine, sizing, breakers, kill switch, flatten
    execution/               # router, order manager, rate limiter, reconciliation
    brokers/                 # paper, dry-run, kite brokers
    backtest/                # engine, fills, slippage, costs, metrics, report
    research/                # in-sample / out-of-sample promotion gate
    simulation/              # paper/live event loop
    operator/                # state snapshot + local dashboard
    data/                    # instrument master, candle/tick stores, quality
    storage/                 # SQLite schema + repositories
    auth/                    # Kite auth, callback server, token store
    adapters/kite/           # KiteClient + sub-adapters
    reports/                 # HTML render + plots
  tests/                     # pytest suite (CLI, engines, risk, data, adapters)
```

### How the pieces fit

The CLI parses flags and prints. `runtime.Workspace` opens config, storage and
the operator files for one data directory; `runtime.StrategyRun` builds the
engine for a mode and runs a workflow (`run_backtest`, `run_research`,
`replay_paper`, `dry_run`, `live_engine`). Tests and any future service entry
point (scheduler, API, notebook) use the same two classes, so adding a mode or
a broker is a change in one file.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/) (preferred) or `pip` + venv
- A Zerodha trading account
- A Kite Connect app: https://developers.kite.trade/
  (note the personal Kite Connect plan + the daily-token model, see below)

## Installation

```bash
# Preferred: uv
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Alternative (plain `pip`):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The framework exposes itself as the `dogesh` console script after install.

## First-time configuration

1. Copy environment template:
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and fill in `KITE_API_KEY` and `KITE_API_SECRET`.
   **Never commit the real `.env`.**

2. Keep `configs/app.yaml` and `configs/risk.yaml` on this machine and point
   the framework at them with `DOGESHTRADER_CONFIG` and
   `DOGESHTRADER_RISK_CONFIG`. The whole `configs/` directory is gitignored.
   Nothing in it is pushed.

3. Make sure the Kite app's *redirect URL* in the Kite developer console
   matches `redirect_url` in your YAML config (default
   `http://127.0.0.1:8000/kite/callback`).

## Zerodha app setup at a high level

1. Sign up at https://developers.kite.trade/ and create a Kite Connect app
   (personal subscription is required, currently a small monthly fee).
2. Note the **API key** and **API secret**.
3. Set the redirect URL of the Kite app to
   `http://127.0.0.1:8000/kite/callback` (or whatever you configure).
4. For **live order placement** Zerodha requires a static IP whitelisted on
   the Kite app dashboard. Local desktops typically don't have one, so live
   trading from a home connection is fragile. Consider a dedicated VPS with
   a static IP that you whitelist via the Kite dashboard.
5. There is **no official Kite "sandbox"**. Paper trading in this framework
   is internal simulation via `PaperBroker`. Live trading hits the real
   exchange.

## Daily login (manual)

Kite issues a fresh `access_token` per login session per day. The login
itself - including TOTP - **must be done manually in your browser**.
DogeshTrader never automates Zerodha credentials, TOTP, or any
password-style secret.

Daily routine:

```bash
# 1. Get the login URL
dogesh auth login-url
# -> https://kite.zerodha.com/connect/login?api_key=...&v=3

# 2. Start the local callback server
dogesh auth callback-server --port 8000

# 3. In your browser, open the URL above, complete Kite login + TOTP.
#    Kite redirects you to http://127.0.0.1:8000/kite/callback?request_token=...
#    DogeshTrader exchanges the request_token for an access_token and stores
#    it locally (file is chmod 600 + SQLite row, both ignored by git).

# 4. Confirm token is stored:
dogesh auth status
```

The framework refuses to clobber an existing token without explicit user
action: `dogesh auth callback-server` overwrites the token row on success.

## Test Kite auth without placing orders

Once the access token is captured, the safest first check is the *dry-run*
flow which proves that DogeshTrader can talk to Kite end-to-end but never
sends an order:

```bash
# Refresh the instrument master (no orders, just market metadata):
dogesh instruments download --exchange NSE

# Download a few months of historical candles for sanity checking:
dogesh data historical \
  --exchange NSE --symbol INFY \
  --interval 5minute \
  --from 2024-01-01 --to 2024-03-31

# Validate a strategy without placing anything:
dogesh dry-run \
  --config configs/strategies/sma_crossover.yaml
```

`dry-run` runs the strategy against the most recent stored candles, passes
its intents through the risk engine, and prints what *would* have gone to
the broker. Optionally, the dry-run broker can ask Kite for margin estimates
without placing the order.

## Run your first backtest

```bash
# 1. Pull instruments + history (only needed once per universe/window):
dogesh instruments download --exchange NSE
dogesh data historical --exchange NSE --symbol INFY \
  --interval 5minute --from 2024-01-01 --to 2024-06-30
dogesh data historical --exchange NSE --symbol RELIANCE \
  --interval 5minute --from 2024-01-01 --to 2024-06-30

# 2. Run the backtest:
dogesh backtest \
  --config configs/strategies/sma_crossover.yaml
```

Outputs land in `data/reports/<run_id>/`:

- `summary.json` - all metrics in one structured file
- `trades.csv` - round-trip ledger
- `orders.csv` - order ledger
- `fills.csv` - per-fill detail
- `equity_curve.csv` - equity sampled per candle
- `report.html` - interactive summary you can open in a browser

`dogesh report` lists recent runs; `dogesh report --run-id <id>` shows
details.

Every run command accepts `--app-config` and `--risk-config` (defaulting to
`DOGESHTRADER_CONFIG` / `DOGESHTRADER_RISK_CONFIG`, then the example files).
The strategy comes from the `strategy:` key in the config; `--strategy`
overrides it. `dogesh strategies` lists everything installed.

Backtests fill market orders on the next bar's open. An order still pending
when the session ends fills at that session's last close, and limit/stop
orders expire at the end of the day, so intraday positions are never carried
overnight.

## Research gate

```bash
dogesh research --config configs/strategies/intraday_orb_vwap.yaml
```

Runs the backtest, splits the equity curve and trades at `research.split`
(or `--split YYYY-MM-DD`), and scores both windows after costs. It exits 1
unless the out-of-sample window has closed trades, positive expectancy, and
ends above where it started. Treat a pass as permission to paper trade, not
as a forecast.

## Run paper mode

Paper mode keeps a `PaperBroker` in memory and feeds it either real ticks
from Kite WebSocket or replayed historical candles. Either way, **no orders
are placed at the broker**.

```bash
# Live ticks (requires a fresh access token):
dogesh paper \
  --config configs/strategies/sma_crossover.yaml

# Replay historical candles (offline, no token required):
dogesh paper \
  --config configs/strategies/sma_crossover.yaml \
  --replay-historical
```

Use `--max-events N` to stop after a fixed number of events when smoke
testing.

## Going live (only if you have already proved the strategy)

```bash
# 1. Make sure your config explicitly opts in:
#    configs/app.yaml:
#      live_trading_enabled: true
#      order_tag_prefix: dogesh_
#
# 2. Verify your strategy config has a unique, non-empty strategy_id.
#
# 3. Run live:
DOGESHTRADER_CONFIG=configs/app.yaml \
dogesh live \
  --config configs/strategies/sma_crossover.yaml \
  --i-understand-this-places-real-orders
```

If any of the guardrails fail (no token, missing config flag, missing CLI
flag, missing strategy id, wrong tag prefix, etc.) the command exits with a
clear message and does not place any orders.

## Reconcile and audit

```bash
# Compare local order state with what Kite reports:
dogesh reconcile
```

Reconciliation rows land in the `reconciliation` SQLite table; the CLI also
prints a summary. Any drift (status mismatch, missing-on-broker,
missing-locally) is recorded for later investigation. If the broker holds a
position that differs from the last run snapshot, `reconcile` engages the
kill switch and exits 2. A LIVE engine runs the same check at start and
periodically while running.

## Operator controls

```bash
dogesh kill --reason "news event"   # block new entries; running engines flatten
dogesh resume                       # clear the kill switch
dogesh dashboard --port 8765        # local positions / signals / P&L page
```

The kill switch is a file (`<data_dir>/KILL`), so it works across processes
and survives restarts. Exits are always allowed so open positions can close.

The dashboard is read-only and binds to 127.0.0.1. Run it in a second
terminal next to `paper` or `live`. It updates every two seconds without
reloading and shows:

- engine health: running, finished, or **stale** when the engine has not
  updated for `--stale-after` seconds (default 10). Engines send a heartbeat
  even when no ticks arrive, so a stale banner means the process stopped or
  hung, not that the market is quiet
- kill-switch state and reason
- equity, day and month P&L, trades today, tripped breakers
- open positions, recent strategy signals with their evidence, and
  instruments blocked for bad data

It reads `<data_dir>/operator/state.json`, which every run mode writes.
`GET /api/state` returns the same data as JSON.

## Writing a new strategy

A strategy is just a Python class that inherits from
`dogeshtrader.strategy.Strategy` and uses the `@register` decorator. The
contract is intentionally minimal:

```python
# dogeshtrader/strategy/my_breakout.py
from dogeshtrader.core.enums import OrderType, Product, Side
from dogeshtrader.core.models import Candle, OrderIntent, StrategyContext
from dogeshtrader.strategy import Strategy, register


@register("my_breakout")
class MyBreakout(Strategy):
    def on_start(self, ctx: StrategyContext) -> None:
        self.high_so_far: dict[str, float] = {}

    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        inst = ctx.extra["_current_instrument"]
        prev_high = self.high_so_far.get(inst.key, 0.0)
        intents: list[OrderIntent] = []
        if candle.close > prev_high * 1.02 and ctx.position(inst).is_flat:
            intents.append(
                OrderIntent(
                    strategy_id=ctx.strategy_id,
                    instrument=inst,
                    side=Side.BUY,
                    quantity=int(self.get_param("quantity", 1)),
                    order_type=OrderType.MARKET,
                    product=Product(self.get_param("product", "MIS")),
                    market_protection_pct=1.0,
                    reason="breakout",
                )
            )
        self.high_so_far[inst.key] = max(prev_high, candle.high)
        return intents
```

Then add a config:

```yaml
# configs/strategies/my_breakout.yaml
strategy: my_breakout
strategy_id: breakout_demo_01
initial_cash: 200000
instruments:
  - exchange: NSE
    tradingsymbol: INFY
interval: 5minute
params:
  quantity: 2
  product: MIS
backtest:
  from: "2024-01-01"
  to: "2024-06-30"
```

Run it:

```bash
dogesh backtest --config configs/strategies/my_breakout.yaml
```

### Typed parameters

Give the class a `Params` model and the YAML `params` block is validated when
the strategy is built. A typo or wrong type fails at load time instead of
silently falling back to a default mid-session:

```python
from pydantic import BaseModel


class BreakoutParams(BaseModel, extra="forbid"):
    quantity: int = 1
    product: str = "MIS"
    threshold_pct: float = 2.0


@register("my_breakout")
class MyBreakout(Strategy):
    Params = BreakoutParams
```

`self.settings` holds the validated model; `self.params` / `get_param` keep
working for untyped strategies.

### Explaining decisions on the dashboard

Call `ctx.record_signal` when the strategy acts, or decides not to, and the
evidence shows up on the operator page. Signals are display-only; only
returned `OrderIntent` objects become orders.

```python
ctx.record_signal(inst, side="BUY", reason="breakout", evidence={"rvol": 2.1, "vwap": 101.4})
```

### Shipping a strategy as a plugin

Strategies do not have to live in this repository. Any installed package can
publish one through an entry point, and `dogesh strategies` picks it up:

```toml
# your package's pyproject.toml
[project.entry-points."dogeshtrader.strategies"]
my_breakout = "my_pkg.breakout:MyBreakout"
```

`pip install -e path/to/my_pkg`, then reference `strategy: my_breakout` in a
config. The built-in strategies are registered the same way.

Put each run in `configs/strategies/<name>.yaml`. Several files can point at
the same `strategy:` value when you want another universe or date window.
Those files are gitignored.

A strategy module you add under `dogeshtrader/strategy/` is gitignored too,
apart from the two samples and the shared signal helpers. Drop the file in
and `dogesh strategies` picks it up locally. It is not part of the published
package. Add the filename to the allowlist in `.gitignore` only when a
strategy is meant to be public.

Strategies must not import any broker, kite client, FastAPI, or
`requests.post(...)`. They must only return `OrderIntent`. The framework will
reject pull requests that violate this rule.

## Safety rules (all enforced by code, not by docs)

1. Default mode is `PAPER`. Even with a token, no orders are placed.
2. `LIVE` requires `live_trading_enabled: true` **and** the CLI confirmation
   flag.
3. Strategies must declare a non-empty `strategy_id`.
4. All orders are tagged with `dogesh_<strategy>_<intent_short>` so they're
   visible in the Kite dashboard for manual review.
5. The risk engine enforces max order quantity, max order notional, max
   position notional, max daily loss, max open orders, lot/tick alignment,
   instrument allow/block lists, and per-minute order budget.
6. MARKET orders are rejected unless `market_protection_pct` is provided.
7. Outbound order rate is throttled below Zerodha's documented 10 req/sec.
8. Idempotency keys de-dupe duplicate placements after retries.
9. Zerodha password / TOTP are **never** stored. Daily login is manual.
10. Secrets live in `.env` (gitignored) or environment variables only.

## Development

```bash
make install   # .venv + editable install with dev extras
make hooks     # pre-commit: ruff, mypy, and a guard against committing .env/configs
make check     # what CI runs: lint, mypy, tests with an 80% coverage floor
make test      # quick test run
make format    # apply ruff fixes + formatting
```

CI (`.github/workflows/ci.yml`) runs `make lint typecheck cov` on Python 3.12
and 3.13 for every push and pull request.

Conventions:

- Strategies return `OrderIntent` only; they never import brokers or HTTP.
- New run modes, brokers, or entry points are wired in `runtime.py`; CLI
  commands stay thin and raise `DogeshTraderError` subclasses, which the CLI
  turns into a one-line message and exit code 1.
- Tests build throwaway workspaces with `tests/conftest.py::build_workspace`
  (real YAML configs plus Parquet candles) and drive the CLI through
  `typer.testing.CliRunner`. No test touches the network.
- `tests/test_import_graph.py` imports every module in a fresh interpreter,
  so circular imports fail CI rather than a user's command.

## Disclaimer

DogeshTrader is provided as-is. The authors and contributors disclaim any
liability for trading losses incurred by software that uses this framework.
This repository does not provide investment advice. Read every line of the
risk engine and broker glue **before** you toggle `live_trading_enabled`.
