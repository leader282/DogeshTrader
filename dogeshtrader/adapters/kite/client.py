"""KiteClient - the operator-facing facade over kiteconnect.

Construction is lazy: importing this module does *not* require ``kiteconnect``
to be installed. The KiteConnect instance is built only when ``.kite`` is
accessed.
"""

from __future__ import annotations

from typing import Any

from dogeshtrader.config import AppSettings
from dogeshtrader.core.errors import AuthError, BrokerError
from dogeshtrader.logging_config import get_logger

_log = get_logger(__name__)


class KiteClient:
    """Lazy facade around the official ``kiteconnect.KiteConnect`` client."""

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str,
        api_secret: str | None = None,
    ) -> None:
        if not api_key:
            raise AuthError("api_key required")
        if not access_token:
            raise AuthError("access_token required - login first")
        self.api_key = api_key
        self._access_token = access_token
        self._api_secret = api_secret
        self._kite: Any | None = None

    @classmethod
    def from_settings(cls, settings: AppSettings, access_token: str) -> "KiteClient":
        return cls(
            api_key=settings.kite_api_key,
            access_token=access_token,
            api_secret=settings.kite_api_secret or None,
        )

    @property
    def kite(self) -> Any:
        if self._kite is None:
            try:
                from kiteconnect import KiteConnect
            except ImportError as e:
                raise BrokerError(
                    "kiteconnect package not installed; install dogeshtrader[live] to use LIVE/DRY_RUN"
                ) from e
            kc = KiteConnect(api_key=self.api_key)
            kc.set_access_token(self._access_token)
            self._kite = kc
        return self._kite

    @property
    def access_token(self) -> str:
        return self._access_token

    # ------------------------------------------------------------------
    # Lazy adapter accessors. Each returns a dedicated wrapper class.
    # ------------------------------------------------------------------

    def instruments_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.instruments import KiteInstrumentsAdapter

        return KiteInstrumentsAdapter(self.kite)

    def market_data_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.market_data import KiteMarketDataAdapter

        return KiteMarketDataAdapter(self.kite)

    def orders_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.orders import KiteOrdersAdapter

        return KiteOrdersAdapter(self.kite)

    def portfolio_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.portfolio import KitePortfolioAdapter

        return KitePortfolioAdapter(self.kite)

    def margins_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.margins import KiteMarginsAdapter

        return KiteMarginsAdapter(self.kite)

    def gtt_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.gtt import KiteGTTAdapter

        return KiteGTTAdapter(self.kite)

    def websocket_adapter(self) -> Any:
        from dogeshtrader.adapters.kite.websocket import KiteWebSocketAdapter

        return KiteWebSocketAdapter(api_key=self.api_key, access_token=self._access_token)


__all__ = ["KiteClient"]
