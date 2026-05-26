"""Idempotent SQLite schema setup.

There is no Alembic-style migration tracking; the framework is small enough
that the schema lives in one place. Every CREATE statement uses
``IF NOT EXISTS`` so :func:`run_migrations` is safe to call on every startup.
"""

from __future__ import annotations

from dogeshtrader.storage.db import Database

_SCHEMA: tuple[str, ...] = (
    # ------------------------------------------------------------------
    # Auth / tokens
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS kite_tokens (
        api_key       TEXT PRIMARY KEY,
        access_token  TEXT NOT NULL,
        user_id       TEXT,
        user_name     TEXT,
        login_time    TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        raw_json      TEXT
    );
    """,
    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id        TEXT PRIMARY KEY,
        mode          TEXT NOT NULL,
        strategy      TEXT NOT NULL,
        strategy_id   TEXT NOT NULL,
        started_at    TEXT NOT NULL,
        ended_at      TEXT,
        status        TEXT NOT NULL,
        config_yaml   TEXT,
        notes         TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
    """,
    # ------------------------------------------------------------------
    # Instruments cache
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS instruments (
        exchange         TEXT NOT NULL,
        tradingsymbol    TEXT NOT NULL,
        instrument_token INTEGER,
        exchange_token   INTEGER,
        name             TEXT,
        instrument_type  TEXT,
        segment          TEXT,
        expiry           TEXT,
        strike           REAL,
        tick_size        REAL DEFAULT 0.05,
        lot_size         INTEGER DEFAULT 1,
        last_refreshed   TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (exchange, tradingsymbol)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_instruments_token ON instruments(instrument_token);
    """,
    # ------------------------------------------------------------------
    # Orders (intents and broker orders, all modes)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS order_intents (
        intent_id       TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL,
        strategy_id     TEXT NOT NULL,
        instrument_key  TEXT NOT NULL,
        side            TEXT NOT NULL,
        quantity        INTEGER NOT NULL,
        order_type      TEXT NOT NULL,
        product         TEXT NOT NULL,
        variety         TEXT NOT NULL,
        price           REAL,
        trigger_price   REAL,
        validity        TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        reason          TEXT,
        metadata_json   TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_intents_run ON order_intents(run_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id        TEXT PRIMARY KEY,
        intent_id       TEXT,
        run_id          TEXT NOT NULL,
        broker          TEXT NOT NULL,                  -- paper | dry_run | kite
        mode            TEXT NOT NULL,                  -- BACKTEST | PAPER | DRY_RUN | LIVE
        instrument_key  TEXT NOT NULL,
        side            TEXT NOT NULL,
        quantity        INTEGER NOT NULL,
        filled_quantity INTEGER DEFAULT 0,
        pending_quantity INTEGER DEFAULT 0,
        order_type      TEXT NOT NULL,
        product         TEXT NOT NULL,
        variety         TEXT NOT NULL,
        price           REAL,
        trigger_price   REAL,
        average_price   REAL DEFAULT 0,
        status          TEXT NOT NULL,
        status_message  TEXT,
        tag             TEXT,
        exchange_order_id TEXT,
        exchange_timestamp TEXT,
        placed_at       TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now')),
        raw_json        TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(run_id),
        FOREIGN KEY(intent_id) REFERENCES order_intents(intent_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orders_run ON orders(run_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
    """,
    # ------------------------------------------------------------------
    # Fills / trades
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS fills (
        trade_id       TEXT PRIMARY KEY,
        order_id       TEXT NOT NULL,
        run_id         TEXT NOT NULL,
        instrument_key TEXT NOT NULL,
        side           TEXT NOT NULL,
        quantity       INTEGER NOT NULL,
        price          REAL NOT NULL,
        fees           REAL DEFAULT 0,
        timestamp      TEXT NOT NULL,
        raw_json       TEXT,
        FOREIGN KEY(order_id) REFERENCES orders(order_id),
        FOREIGN KEY(run_id) REFERENCES runs(run_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fills_run ON fills(run_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
    """,
    # ------------------------------------------------------------------
    # Idempotency / dedupe
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key           TEXT PRIMARY KEY,
        order_id      TEXT,
        run_id        TEXT,
        created_at    TEXT DEFAULT (datetime('now'))
    );
    """,
    # ------------------------------------------------------------------
    # Audit / reconciliation
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT DEFAULT (datetime('now')),
        run_id        TEXT,
        category      TEXT NOT NULL,            -- risk | broker | router | reconcile | auth
        event         TEXT NOT NULL,
        payload_json  TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS reconciliation (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT DEFAULT (datetime('now')),
        run_id        TEXT,
        intent_id     TEXT,
        order_id      TEXT,
        broker_order_id TEXT,
        kind          TEXT NOT NULL,             -- match | drift | missing_broker | missing_local
        delta_json    TEXT
    );
    """,
)


def run_migrations(db: Database) -> None:
    """Apply all schema statements. Safe to call repeatedly."""
    with db.transaction() as cur:
        for statement in _SCHEMA:
            cur.execute(statement)


__all__ = ["run_migrations"]
