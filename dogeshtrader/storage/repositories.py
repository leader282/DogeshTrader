"""Repository helpers - one per aggregate.

These are intentionally small, stateless objects that take a :class:`Database`.
The framework avoids ORMs and writes plain SQL to keep the surface area tiny.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from dogeshtrader.core.models import (
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
)
from dogeshtrader.storage.db import Database

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt.isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        run_id: str,
        *,
        mode: str,
        strategy: str,
        strategy_id: str,
        config_yaml: str | None = None,
        notes: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        ts = (started_at or datetime.now()).isoformat()
        self.db.execute(
            """
            INSERT OR REPLACE INTO runs
                (run_id, mode, strategy, strategy_id, started_at, status, config_yaml, notes)
            VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?)
            """,
            (run_id, mode, strategy, strategy_id, ts, config_yaml, notes),
        )

    def complete(self, run_id: str, status: str = "COMPLETED") -> None:
        self.db.execute(
            "UPDATE runs SET status=?, ended_at=? WHERE run_id=?",
            (status, datetime.now().isoformat(), run_id),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM runs WHERE run_id=?", (run_id,))
        return dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record_intent(
        self,
        *,
        intent_id: str,
        run_id: str,
        request: OrderRequest,
        reason: str = "",
    ) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO order_intents
              (intent_id, run_id, strategy_id, instrument_key, side, quantity,
               order_type, product, variety, price, trigger_price, validity,
               idempotency_key, reason, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                run_id,
                request.strategy_id,
                request.instrument.key,
                request.side.value,
                request.quantity,
                request.order_type.value,
                request.product.value,
                request.variety.value,
                request.price,
                request.trigger_price,
                request.validity.value,
                request.idempotency_key,
                reason,
                _json(request.metadata),
            ),
        )

    def record_order(
        self,
        *,
        run_id: str,
        broker: str,
        mode: str,
        request: OrderRequest,
        update: OrderUpdate,
    ) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO orders
              (order_id, intent_id, run_id, broker, mode, instrument_key, side, quantity,
               filled_quantity, pending_quantity, order_type, product, variety,
               price, trigger_price, average_price, status, status_message, tag,
               exchange_order_id, exchange_timestamp, placed_at, updated_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT placed_at FROM orders WHERE order_id=?), datetime('now')),
                    datetime('now'), ?)
            """,
            (
                update.order_id,
                request.intent_id,
                run_id,
                broker,
                mode,
                request.instrument.key,
                request.side.value,
                request.quantity,
                update.filled_quantity,
                update.pending_quantity,
                request.order_type.value,
                request.product.value,
                request.variety.value,
                request.price,
                request.trigger_price,
                update.average_price,
                update.status.value,
                update.status_message,
                request.tag,
                update.exchange_order_id,
                _iso(update.exchange_timestamp),
                update.order_id,
                _json(update.raw or {}),
            ),
        )

    def update_status(self, update: OrderUpdate) -> None:
        self.db.execute(
            """
            UPDATE orders
               SET status=?, status_message=?, filled_quantity=?, pending_quantity=?,
                   average_price=?, exchange_order_id=COALESCE(?, exchange_order_id),
                   exchange_timestamp=COALESCE(?, exchange_timestamp),
                   updated_at=datetime('now'), raw_json=?
             WHERE order_id=?
            """,
            (
                update.status.value,
                update.status_message,
                update.filled_quantity,
                update.pending_quantity,
                update.average_price,
                update.exchange_order_id,
                _iso(update.exchange_timestamp),
                _json(update.raw or {}),
                update.order_id,
            ),
        )

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM orders WHERE run_id=? ORDER BY placed_at", (run_id,))
        return [dict(r) for r in rows]

    def open_orders(self, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM orders WHERE status IN ('PENDING','SUBMITTED','OPEN','PARTIAL')"
        params: tuple[Any, ...] = ()
        if run_id:
            sql += " AND run_id=?"
            params = (run_id,)
        return [dict(r) for r in self.db.fetchall(sql, params)]


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------


class FillRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, run_id: str, fill: Fill) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO fills
              (trade_id, order_id, run_id, instrument_key, side, quantity, price,
               fees, timestamp, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.trade_id,
                fill.order_id,
                run_id,
                fill.instrument.key,
                fill.side.value,
                fill.quantity,
                fill.price,
                fill.fees,
                _iso(fill.timestamp),
                _json(fill.raw or {}),
            ),
        )

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM fills WHERE run_id=? ORDER BY timestamp", (run_id,))
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------


class InstrumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_many(self, instruments: Iterable[Instrument]) -> int:
        rows = [
            (
                i.exchange.value,
                i.tradingsymbol,
                i.instrument_token,
                i.exchange_token,
                i.name,
                i.instrument_type.value if i.instrument_type else None,
                i.segment.value if i.segment else None,
                _iso(i.expiry),
                i.strike,
                i.tick_size,
                i.lot_size,
            )
            for i in instruments
        ]
        if not rows:
            return 0
        self.db.executemany(
            """
            INSERT OR REPLACE INTO instruments
              (exchange, tradingsymbol, instrument_token, exchange_token, name,
               instrument_type, segment, expiry, strike, tick_size, lot_size,
               last_refreshed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            rows,
        )
        return len(rows)

    def find_by_key(self, exchange: str, tradingsymbol: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM instruments WHERE exchange=? AND tradingsymbol=?",
            (exchange, tradingsymbol),
        )
        return dict(row) if row else None

    def find_by_token(self, instrument_token: int) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM instruments WHERE instrument_token=?",
            (instrument_token,),
        )
        return dict(row) if row else None

    def count(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS c FROM instruments")
        return int(row["c"]) if row else 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class IdempotencyRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def claim(self, key: str, run_id: str) -> bool:
        """Atomically claim a key. Returns True on success, False if duplicate."""
        try:
            self.db.execute(
                "INSERT INTO idempotency_keys(key, run_id) VALUES (?, ?)",
                (key, run_id),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def attach_order(self, key: str, order_id: str) -> None:
        self.db.execute(
            "UPDATE idempotency_keys SET order_id=? WHERE key=?",
            (order_id, key),
        )

    def lookup(self, key: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM idempotency_keys WHERE key=?", (key,))
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Audit / reconciliation
# ---------------------------------------------------------------------------


class AuditRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def log(
        self,
        category: str,
        event: str,
        *,
        run_id: str | None = None,
        payload: Any | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO audit_log(run_id, category, event, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, category, event, _json(payload) if payload is not None else None),
        )

    def list_recent(self, limit: int = 100, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            rows = self.db.fetchall(
                "SELECT * FROM audit_log WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            )
        else:
            rows = self.db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def record_reconcile(
        self,
        *,
        run_id: str | None,
        intent_id: str | None,
        order_id: str | None,
        broker_order_id: str | None,
        kind: str,
        delta: Any | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO reconciliation
              (run_id, intent_id, order_id, broker_order_id, kind, delta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, intent_id, order_id, broker_order_id, kind, _json(delta) if delta else None),
        )


# ---------------------------------------------------------------------------
# Token store (also accessible via auth.token_store for richer methods)
# ---------------------------------------------------------------------------


class TokenRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(
        self,
        api_key: str,
        access_token: str,
        *,
        user_id: str | None = None,
        user_name: str | None = None,
        login_time: str | None = None,
        raw: Any | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO kite_tokens
              (api_key, access_token, user_id, user_name, login_time, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (api_key, access_token, user_id, user_name, login_time, _json(raw or {})),
        )

    def get(self, api_key: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM kite_tokens WHERE api_key=?", (api_key,))
        return dict(row) if row else None

    def clear(self, api_key: str) -> None:
        self.db.execute("DELETE FROM kite_tokens WHERE api_key=?", (api_key,))


__all__ = [
    "AuditRepository",
    "FillRepository",
    "IdempotencyRepository",
    "InstrumentRepository",
    "OrderRepository",
    "RunRepository",
    "TokenRepository",
]
