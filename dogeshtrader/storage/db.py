"""Database helper - thin wrapper around sqlite3 with safe defaults."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_DEFAULT_PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA temp_store=MEMORY;",
    "PRAGMA busy_timeout=5000;",
)


class Database:
    """Per-process SQLite handle.

    sqlite3 connections are *not* thread-safe by default; we serialise access
    through a re-entrant lock so engines that share a Database from a worker
    thread (e.g. callback server) don't trample each other.
    """

    def __init__(self, path: str | Path, *, in_memory: bool = False) -> None:
        if in_memory:
            self.path = Path(":memory:")
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            # tighten permissions if the file is new
            try:
                if self.path.exists():
                    os.chmod(self.path, 0o600)
            except OSError:
                # best-effort; non-fatal on Windows or restricted filesystems
                pass
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_pragmas()

    def _init_pragmas(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for pragma in _DEFAULT_PRAGMAS:
                try:
                    cur.execute(pragma)
                except sqlite3.OperationalError:
                    # WAL is not available on :memory: prior to certain
                    # sqlite versions; ignore failures.
                    pass
            cur.close()

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, seq: list[tuple[Any, ...]] | list[dict[str, Any]]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(sql, seq)
            self._conn.commit()
            cur.close()

    def fetchall(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def fetchone(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = ["Database"]
