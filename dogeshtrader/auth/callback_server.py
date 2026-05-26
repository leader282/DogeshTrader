"""Local FastAPI callback server.

Steps the operator runs:

    $ dogesh auth callback-server --port 8000

The server listens for the redirect from Kite, captures the ``request_token``,
exchanges it for an ``access_token`` and persists everything via TokenStore.
The server then exits after a single successful auth (so it doesn't sit on
port 8000 longer than necessary).
"""

from __future__ import annotations

import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from dogeshtrader.auth.kite_auth import exchange_request_token
from dogeshtrader.auth.token_store import TokenStore
from dogeshtrader.config import AppSettings
from dogeshtrader.logging_config import get_logger

_log = get_logger(__name__)


def build_callback_app(
    *,
    token_store: TokenStore,
    settings: AppSettings,
    on_success: threading.Event | None = None,
    callback_path: str = "/kite/callback",
) -> FastAPI:
    """Build a fresh FastAPI app bound to the supplied dependencies.

    Returns the app and exposes ``state.received`` -> latest payload, useful
    for tests.
    """
    app = FastAPI(title="DogeshTrader Kite callback")
    app.state.received = None

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _index_html(callback_path)

    @app.get(callback_path, response_class=HTMLResponse)
    def callback(
        request_token: str | None = Query(None, alias="request_token"),
        status: str | None = Query(None),
        action: str | None = Query(None),
    ) -> str:
        if not request_token:
            raise HTTPException(status_code=400, detail="missing request_token")
        if not (settings.kite_api_key and settings.kite_api_secret):
            raise HTTPException(
                status_code=400,
                detail="KITE_API_KEY / KITE_API_SECRET not configured in environment",
            )
        try:
            payload: dict[str, Any] = exchange_request_token(
                settings.kite_api_key,
                request_token,
                settings.kite_api_secret,
            )
        except Exception as e:
            _log.error("auth_exchange_failed", error=str(e))
            raise HTTPException(status_code=400, detail=str(e)) from e
        token_store.save(payload, api_key=settings.kite_api_key)
        app.state.received = payload
        if on_success is not None:
            on_success.set()
        return _success_html(payload)

    return app


class CallbackServer:
    """Convenience wrapper that runs the FastAPI app via uvicorn in-process."""

    def __init__(
        self,
        *,
        token_store: TokenStore,
        settings: AppSettings,
        host: str = "127.0.0.1",
        port: int = 8000,
        callback_path: str = "/kite/callback",
    ) -> None:
        self.token_store = token_store
        self.settings = settings
        self.host = host
        self.port = port
        self.callback_path = callback_path
        self.completed = threading.Event()
        self.app = build_callback_app(
            token_store=token_store,
            settings=settings,
            on_success=self.completed,
            callback_path=callback_path,
        )

    def run_until_token(self, *, max_seconds: int = 600) -> bool:
        """Block until a token is captured or ``max_seconds`` elapses.

        Returns ``True`` if a token was captured, ``False`` on timeout.
        """
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="dogesh-callback", daemon=True)
        thread.start()

        try:
            captured = self.completed.wait(timeout=max_seconds)
        finally:
            server.should_exit = True
            thread.join(timeout=5.0)
        return captured


def _index_html(callback_path: str) -> str:
    return f"""<!doctype html>
<html><head><title>DogeshTrader</title></head>
<body style="font-family: sans-serif; padding: 32px;">
  <h1>DogeshTrader Kite callback server</h1>
  <p>Open the Kite login URL printed by <code>dogesh auth login-url</code>.</p>
  <p>After successful login Kite redirects to <code>{callback_path}</code>.</p>
</body></html>
"""


def _success_html(payload: dict[str, Any]) -> str:
    user = payload.get("user_name") or payload.get("user_id") or "user"
    return f"""<!doctype html>
<html><head><title>DogeshTrader auth complete</title></head>
<body style="font-family: sans-serif; padding: 32px;">
  <h1>Authenticated as {user}</h1>
  <p>Token saved. You can close this tab and return to the terminal.</p>
</body></html>
"""


__all__ = ["CallbackServer", "build_callback_app"]
