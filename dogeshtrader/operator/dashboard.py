"""Local operator page.

Serves the latest engine snapshot: open positions, signal evidence, P&L,
risk state, kill switch, and whether the engine is still alive. It is
read-only and never places orders; kill and resume stay on the CLI.

* ``GET /``           - the page; it polls ``/api/state`` and updates in place
* ``GET /api/state``  - the view model as JSON
* ``GET /healthz``    - liveness of the dashboard process itself
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from dogeshtrader.operator.state import load_state
from dogeshtrader.risk.kill_switch import KillSwitch

Health = Literal["waiting", "live", "stale", "stopped"]

DEFAULT_STALE_AFTER = 10.0
POLL_MS = 2000


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_view(
    state: dict[str, Any] | None,
    *,
    kill_switch: KillSwitch,
    stale_after: float = DEFAULT_STALE_AFTER,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn a raw snapshot into what the page shows, including engine health."""
    now = now or datetime.now(UTC)
    state = state or {}
    engaged = kill_switch.is_engaged()

    published = _parse_ts(state.get("published_at"))
    age = (now - published).total_seconds() if published else None
    health: Health
    if not state:
        health = "waiting"
        health_text = "No engine snapshot yet. Start a backtest, paper, or live run."
    elif state.get("status") == "stopped":
        health = "stopped"
        health_text = "Run finished. Figures below are final for this run."
    elif age is None or age > stale_after:
        health = "stale"
        since = f"{age:.0f}s" if age is not None else "an unknown time"
        health_text = (
            f"No update from the engine for {since}. It may have stopped or hung; "
            "positions below may be out of date."
        )
    else:
        health = "live"
        health_text = "Engine is running."

    risk = state.get("risk") or {}
    positions = [row for row in state.get("positions") or [] if int(row.get("quantity") or 0) != 0]
    return {
        "health": health,
        "health_text": health_text,
        "age_seconds": round(age, 1) if age is not None else None,
        "stale_after": stale_after,
        "run_id": state.get("run_id"),
        "mode": state.get("mode"),
        "market_time": state.get("timestamp"),
        "published_at": state.get("published_at"),
        "kill_switch": {
            "engaged": engaged,
            "reason": kill_switch.reason() if engaged else "",
        },
        "equity": state.get("equity"),
        "day_pnl": state.get("day_pnl"),
        "month_pnl": risk.get("month_pnl"),
        "trades_today": risk.get("trades_today"),
        "breakers": list(risk.get("breakers") or []),
        "bad_data": dict(risk.get("bad_data") or {}),
        "positions": positions,
        "signals": list(reversed(state.get("signals") or [])),
    }


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>DogeshTrader</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; background: #0f1419; color: #e7ecf1; }
  h1 { font-size: 1.25rem; margin: 0 0 4px; }
  h2 { font-size: 1rem; margin: 24px 0 8px; color: #c5cfd9; }
  .muted { color: #8b98a5; }
  .banner { padding: 10px 14px; border-radius: 8px; margin: 8px 0; }
  .bad { background: #3d1515; color: #ffb4b4; }
  .warn { background: #3a2e10; color: #ffd98a; }
  .good { background: #13261c; color: #b7ebc9; }
  .info { background: #16202b; color: #c5d6e8; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  .card { background: #16202b; border-radius: 8px; padding: 10px 12px; }
  .card .label { color: #8b98a5; font-size: 0.8rem; }
  .card .value { font-size: 1.1rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .pos { color: #7ee2a8; } .neg { color: #ff9b9b; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a3441; font-size: 0.9rem; }
  th { color: #8b98a5; font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<h1>DogeshTrader</h1>
<p class="muted" id="subtitle">Loading…</p>
<div class="banner info" id="health">Connecting to the dashboard…</div>
<div class="banner info" id="kill"></div>
<div class="cards" id="cards"></div>
<h2>Open positions</h2>
<div id="positions"></div>
<h2>Signals</h2>
<div id="signals"></div>
<h2>Data quality</h2>
<div id="bad-data"></div>
<script>
const POLL_MS = __POLL_MS__;
const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function money(v) { return (v === null || v === undefined) ? "—" : inr.format(Number(v)); }
function signed(v) { return (v === null || v === undefined) ? "" : (Number(v) >= 0 ? "pos" : "neg"); }

function table(headers, rows, numeric) {
  if (!rows.length) return el("p", "None.", "muted");
  const t = el("table");
  const head = el("tr");
  headers.forEach((h, i) => head.appendChild(el("th", h, numeric.has(i) ? "num" : "")));
  t.appendChild(head);
  rows.forEach(r => {
    const tr = el("tr");
    r.forEach((cell, i) => {
      const td = el("td", cell.text ?? cell, numeric.has(i) ? "num" : "");
      if (cell.cls) td.classList.add(cell.cls);
      tr.appendChild(td);
    });
    t.appendChild(tr);
  });
  return t;
}

function card(label, value, cls) {
  const c = el("div", null, "card");
  c.appendChild(el("div", label, "label"));
  c.appendChild(el("div", value, "value " + (cls || "")));
  return c;
}

function render(v) {
  const sub = [v.mode, v.run_id, v.market_time].filter(Boolean).join(" · ");
  document.getElementById("subtitle").textContent = sub || "No run yet.";

  const health = document.getElementById("health");
  health.className = "banner " + ({ live: "good", stale: "bad", stopped: "info", waiting: "info" }[v.health] || "info");
  const age = v.age_seconds === null ? "" : ` (updated ${v.age_seconds}s ago)`;
  health.textContent = v.health_text + (v.health === "live" ? age : "");

  const kill = document.getElementById("kill");
  if (v.kill_switch.engaged) {
    kill.className = "banner bad";
    kill.textContent = "Kill switch engaged. New entries are blocked. " + (v.kill_switch.reason || "");
  } else {
    kill.className = "banner good";
    kill.textContent = "Kill switch clear. New entries follow the risk policy.";
  }

  const cards = document.getElementById("cards");
  cards.replaceChildren(
    card("Equity", money(v.equity)),
    card("Day P&L", money(v.day_pnl), signed(v.day_pnl)),
    card("Month P&L", money(v.month_pnl), signed(v.month_pnl)),
    card("Trades today", v.trades_today ?? "—"),
    card("Breakers", v.breakers.length ? v.breakers.join(", ") : "none", v.breakers.length ? "neg" : ""),
  );

  document.getElementById("positions").replaceChildren(table(
    ["Instrument", "Qty", "Avg", "Last", "Unrealised", "Realised"],
    v.positions.map(p => [
      p.instrument, p.quantity, money(p.avg_price), money(p.last_price),
      { text: money(p.unrealised_pnl), cls: signed(p.unrealised_pnl) },
      { text: money(p.realised_pnl), cls: signed(p.realised_pnl) },
    ]),
    new Set([1, 2, 3, 4, 5]),
  ));

  document.getElementById("signals").replaceChildren(table(
    ["Time", "Instrument", "Side", "Reason", "Evidence"],
    v.signals.map(s => [
      s.timestamp, s.instrument, s.side, s.reason,
      Object.entries(s.evidence || {}).filter(([, val]) => val !== null).map(([k, val]) => `${k}=${val}`).join(", "),
    ]),
    new Set(),
  ));

  document.getElementById("bad-data").replaceChildren(table(
    ["Instrument", "Blocked because"],
    Object.entries(v.bad_data).map(([k, reason]) => [k, reason]),
    new Set(),
  ));
}

async function tick() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
  } catch (err) {
    const health = document.getElementById("health");
    health.className = "banner bad";
    health.textContent = "Lost connection to the dashboard server. Figures below are frozen. (" + err.message + ")";
  }
}
tick();
setInterval(tick, POLL_MS);
</script>
</body>
</html>
"""


def page_html() -> str:
    return _PAGE.replace("__POLL_MS__", str(POLL_MS))


def build_dashboard_app(
    *,
    state_path: Path,
    kill_path: Path,
    stale_after: float = DEFAULT_STALE_AFTER,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(title="DogeshTrader operator", docs_url=None, redoc_url=None)
    switch = KillSwitch(kill_path)
    html = page_html()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        now = clock() if clock else None
        return build_view(
            load_state(state_path), kill_switch=switch, stale_after=stale_after, now=now
        )

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


__all__ = ["DEFAULT_STALE_AFTER", "build_dashboard_app", "build_view", "page_html"]
