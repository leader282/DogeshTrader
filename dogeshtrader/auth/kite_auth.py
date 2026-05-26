"""Kite Connect authentication primitives.

Reference: https://kite.trade/docs/connect/v3/user/

Login flow:

1. Operator opens https://kite.trade/connect/login?api_key=<key>&v=3 .
2. After login, Kite redirects to the configured ``redirect_url`` with
   ``request_token`` in the query string.
3. Exchange the request_token for an access_token by hitting
   POST /session/token with checksum = sha256(api_key + request_token + api_secret).
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlencode

from dogeshtrader.core.errors import AuthError

KITE_LOGIN_BASE = "https://kite.zerodha.com/connect/login"
KITE_SESSION_TOKEN = "https://api.kite.trade/session/token"


def build_login_url(api_key: str, *, version: str = "3") -> str:
    """Generate the URL the operator should open in their browser."""
    if not api_key:
        raise AuthError("api_key is empty; set KITE_API_KEY in your .env")
    return f"{KITE_LOGIN_BASE}?{urlencode({'api_key': api_key, 'v': version})}"


def compute_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """SHA-256 of api_key + request_token + api_secret (Kite's documented scheme)."""
    if not (api_key and request_token and api_secret):
        raise AuthError("missing api_key / request_token / api_secret")
    payload = f"{api_key}{request_token}{api_secret}".encode()
    return hashlib.sha256(payload).hexdigest()


def exchange_request_token(
    api_key: str,
    request_token: str,
    api_secret: str,
    *,
    http: Any | None = None,
) -> dict[str, Any]:
    """Swap ``request_token`` for an ``access_token``.

    Returns the raw response payload so callers can persist any fields they
    care about (user_id, login_time, public_token, etc.).

    Parameters
    ----------
    http:
        Optional httpx-like client - useful for tests. Defaults to httpx.
    """
    checksum = compute_checksum(api_key, request_token, api_secret)
    body = {
        "api_key": api_key,
        "request_token": request_token,
        "checksum": checksum,
    }

    if http is None:
        import httpx

        http = httpx.Client(timeout=15.0)
        owns_http = True
    else:
        owns_http = False

    try:
        response = http.post(KITE_SESSION_TOKEN, data=body)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
    except Exception as e:
        raise AuthError(f"failed to exchange request_token: {e}") from e
    finally:
        if owns_http and hasattr(http, "close"):
            http.close()

    if payload.get("status") != "success" or "data" not in payload:
        raise AuthError(f"kite session/token rejected: {payload}")
    return payload["data"]


__all__ = ["build_login_url", "compute_checksum", "exchange_request_token"]
