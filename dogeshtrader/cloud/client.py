"""HTTPS client for the hosted console.

The agent only ever calls out; the console never connects to this machine.
Every request carries ``Authorization: Bearer <agent token>``.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from dogeshtrader import __version__
from dogeshtrader.config import AppSettings
from dogeshtrader.core.errors import ConfigError, DogeshTraderError

#: Rows per request. The console rejects bodies above ~4.5 MB.
MAX_ROWS_PER_REQUEST = 5_000

BATCH_KEYS = ("runs", "orders", "fills", "trades", "equity", "signals", "candles")

logging.getLogger("httpx").setLevel(logging.WARNING)


class ConsoleError(DogeshTraderError):
    """The console refused a request or could not be reached."""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class ConsoleClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url or not token:
            raise ConfigError("DOGESH_CONSOLE_URL and DOGESH_CONSOLE_TOKEN must be set in .env")
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": f"dogeshtrader/{__version__}",
            },
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: AppSettings | None = None) -> ConsoleClient:
        s = settings or AppSettings()
        return cls(s.dogesh_console_url, s.dogesh_console_token)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ConsoleClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._send(path, payload)
        except httpx.HTTPStatusError as e:
            resp = e.response
        except httpx.TransportError as e:
            raise ConsoleError(f"could not reach the console at {self._http.base_url} ({e})") from e
        if not resp.is_success:
            detail = resp.headers.get("location") or resp.text[:500]
            raise ConsoleError(f"console returned HTTP {resp.status_code} for {path}: {detail}")
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as e:
            raise ConsoleError(f"console sent a non-JSON reply for {path}") from e
        return data

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.5, max=10),
        reraise=True,
    )
    def _send(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        resp = self._http.post(path, json=payload)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    def heartbeat(self, live_state: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"hostname": socket.gethostname(), "version": __version__}
        if live_state is not None:
            payload["live_state"] = live_state
        return self._post("/api/agent/v1/heartbeat", payload)

    def ingest(self, batch: dict[str, Any]) -> dict[str, int]:
        """Send a batch, split so no single request is too large. Returns row counts."""
        totals = dict.fromkeys(BATCH_KEYS, 0)
        for part in split_batch(batch):
            counts = self._post("/api/agent/v1/ingest", part).get("counts", {})
            for key in BATCH_KEYS:
                totals[key] += int(counts.get(key, 0))
        return totals


def split_batch(batch: dict[str, Any], limit: int = MAX_ROWS_PER_REQUEST) -> list[dict[str, Any]]:
    """Chunk a batch so each part has at most ``limit`` rows.

    Runs always go in the first part because every other row references them.
    Signal replacement goes with the first part too, before any new signals.
    """
    first: dict[str, Any] = {
        "runs": list(batch.get("runs", [])),
        "replace_signals_for": list(batch.get("replace_signals_for", [])),
    }
    parts = [first]
    room = limit
    for key in BATCH_KEYS[1:]:
        rows = list(batch.get(key, []))
        while rows:
            if room == 0:
                parts.append({})
                room = limit
            take, rows = rows[:room], rows[room:]
            parts[-1].setdefault(key, []).extend(take)
            room -= len(take)
    return parts


__all__ = ["BATCH_KEYS", "MAX_ROWS_PER_REQUEST", "ConsoleClient", "ConsoleError", "split_batch"]
