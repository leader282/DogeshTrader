"""Typed configuration loader.

Two layers:
* ``AppSettings``  - environment-level settings (paths, mode, env vars).
* ``RiskConfig``   - per-deployment risk limits.

YAML files take precedence, environment variables override individual fields
where applicable.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dogeshtrader.core.enums import Mode
from dogeshtrader.core.errors import ConfigError

# ---------------------------------------------------------------------------
# Application settings (env + YAML)
# ---------------------------------------------------------------------------


class CostsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "india_equity_simple"  # or "bps_only" / "india_equity_detailed"
    brokerage_per_order: float = 20.0
    bps_fallback: float = 3.0


class SlippageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "fixed_bps"  # "fixed_bps" | "fixed_ticks" | "volume_aware"
    bps: float = 2.0
    ticks: int = 1


class AppConfig(BaseModel):
    """YAML-backed application config."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = "DogeshTrader"
    mode: Mode = Mode.PAPER
    timezone: str = "Asia/Kolkata"
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/dogeshtrader.sqlite")
    reports_dir: Path = Path("./data/reports")
    redirect_url: str = "http://127.0.0.1:8000/kite/callback"
    postback_url: str | None = None
    live_trading_enabled: bool = False
    order_tag_prefix: str = "dogesh_"
    default_quote_mode: str = "quote"
    order_rate_per_second: int = 5
    order_rate_burst: int = 8
    costs: CostsConfig = Field(default_factory=CostsConfig)
    slippage: SlippageConfig = Field(default_factory=SlippageConfig)

    @field_validator("order_tag_prefix")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        if not v or len(v) > 12:
            raise ValueError("order_tag_prefix must be 1..12 chars (Kite tag limit is 20)")
        return v


class AppSettings(BaseSettings):
    """Process-level settings (env vars + .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_user_id: str = ""
    kite_postback_secret: str = ""
    dogeshtrader_env: str = "local"
    dogeshtrader_config: str = "configs/app.example.yaml"
    dogeshtrader_risk_config: str = "configs/risk.example.yaml"
    dogesh_console_url: str = ""
    dogesh_console_token: str = ""


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_order_quantity: int = 100
    max_order_notional: float = 100_000
    max_position_notional: float = 250_000
    max_daily_loss: float = 5_000
    max_open_orders: int = 10
    require_market_protection: bool = True
    default_market_protection_pct: float = 1.0
    allowed_instruments: list[str] = Field(default_factory=list)
    blocked_instruments: list[str] = Field(default_factory=list)
    session_start: time = time(9, 15)
    session_end: time = time(15, 30)
    flatten_before_close_minutes: int = 5
    no_new_orders_in_last_minutes: int = 5
    max_consecutive_rejections: int = 3
    data_feed_disconnect_grace_seconds: int = 30
    max_orders_per_minute: int = 60
    # Percent-of-equity limits from the intraday policy. Zero disables a check
    # so existing configs keep the absolute rupee caps above.
    risk_per_trade_pct: float = 0.0
    daily_loss_pct: float = 0.0
    monthly_loss_pct: float = 0.0
    max_concurrent_positions: int = 0
    max_trades_per_day: int = 0
    block_entries_on_bad_data: bool = False
    stale_candle_multiple: float = 2.0

    @field_validator("session_start", "session_end", mode="before")
    @classmethod
    def _parse_time(cls, v: Any) -> Any:
        if isinstance(v, str):
            hh, mm = v.split(":")
            return time(int(hh), int(mm))
        return v


# ---------------------------------------------------------------------------
# Strategy config (loaded by the CLI per-run)
# ---------------------------------------------------------------------------


class InstrumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: str
    tradingsymbol: str


class ResearchWindow(BaseModel):
    """Out-of-sample split for ``dogesh research``.

    ``split`` is the first out-of-sample session (YYYY-MM-DD). Bars before
    that date are the in-sample window.
    """

    model_config = ConfigDict(extra="forbid")
    split: str


class BacktestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: str = Field(alias="from")
    to: str
    warmup_candles: int = 0


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    strategy: str
    strategy_id: str = Field(min_length=1)
    initial_cash: float = 100_000
    instruments: list[InstrumentRef]
    interval: str = "5minute"
    params: dict[str, Any] = Field(default_factory=dict)
    backtest: BacktestWindow | None = None
    research: ResearchWindow | None = None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config is not valid YAML: {p}: {e}") from e
    if not isinstance(loaded, dict):
        raise ConfigError(f"config root must be a mapping: {p}")
    return loaded


def _load[M: BaseModel](model: type[M], path: str | Path) -> M:
    data = _read_yaml(path)
    try:
        return model(**data)
    except ValidationError as e:
        raise ConfigError(f"invalid {model.__name__} in {path}: {e}") from e


def load_app_config(path: str | Path | None = None) -> AppConfig:
    settings = AppSettings()
    return _load(
        AppConfig, path or os.environ.get("DOGESHTRADER_CONFIG") or settings.dogeshtrader_config
    )


def load_risk_config(path: str | Path | None = None) -> RiskConfig:
    settings = AppSettings()
    return _load(
        RiskConfig,
        path or os.environ.get("DOGESHTRADER_RISK_CONFIG") or settings.dogeshtrader_risk_config,
    )


def load_strategy_config(path: str | Path) -> StrategyConfig:
    return _load(StrategyConfig, path)


def get_settings() -> AppSettings:
    return AppSettings()


__all__ = [
    "AppConfig",
    "AppSettings",
    "BacktestWindow",
    "CostsConfig",
    "InstrumentRef",
    "ResearchWindow",
    "RiskConfig",
    "SlippageConfig",
    "StrategyConfig",
    "get_settings",
    "load_app_config",
    "load_risk_config",
    "load_strategy_config",
]
