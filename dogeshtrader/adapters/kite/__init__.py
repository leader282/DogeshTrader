"""Kite Connect adapter package.

The adapter is split into multiple modules so a) we can stub them individually
in tests and b) operators can disable parts they don't use (e.g. WebSocket).

Entry points:
* :class:`KiteClient` - thin wrapper that hands out specialised adapters and
  authenticated kiteconnect.KiteConnect instance.
"""

from __future__ import annotations

from dogeshtrader.adapters.kite.client import KiteClient

__all__ = ["KiteClient"]
