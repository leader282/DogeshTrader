"""Kite postback (webhook) handler.

See https://kite.trade/docs/connect/v3/postbacks/

Kite sends a ``checksum`` field that must match:
``sha256(order_id + order_timestamp + api_secret)``.
Strictly verify before trusting the payload.

The framework prefers WebSocket order updates for individual developer use
(see ``adapters.kite.websocket``); this module is provided for operators who
host DogeshTrader behind a public URL and want the postback as a fallback.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request


def build_postback_router(*, api_secret: str, on_payload: Any) -> APIRouter:
    """Build a FastAPI router that verifies and dispatches Kite postbacks.

    Parameters
    ----------
    api_secret:
        Kite API secret used to verify the checksum.
    on_payload:
        Callable invoked with the verified payload dict.
    """
    router = APIRouter()

    @router.post("/kite/postback")
    async def kite_postback(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not _verify(payload, api_secret):
            raise HTTPException(status_code=400, detail="invalid checksum")
        try:
            on_payload(payload)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"status": "ok"}

    return router


def build_postback_app(*, api_secret: str, on_payload: Any) -> FastAPI:
    app = FastAPI(title="DogeshTrader Kite postback handler")
    app.include_router(build_postback_router(api_secret=api_secret, on_payload=on_payload))
    return app


def _verify(payload: dict[str, Any], api_secret: str) -> bool:
    order_id = str(payload.get("order_id") or "")
    order_ts = str(payload.get("order_timestamp") or "")
    received = str(payload.get("checksum") or "")
    if not order_id or not order_ts or not received:
        return False
    expected = hashlib.sha256(f"{order_id}{order_ts}{api_secret}".encode()).hexdigest()
    return hmac.compare_digest(expected, received)


__all__ = ["build_postback_app", "build_postback_router"]
