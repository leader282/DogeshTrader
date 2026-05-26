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
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dogeshtrader.core.enums import Mode

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


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"config root must be a mapping: {p}")
    return loaded


def load_app_config(path: str | Path | None = None) -> AppConfig:
    settings = AppSettings()
    config_path = Path(
        path or os.environ.get("DOGESHTRADER_CONFIG") or settings.dogeshtrader_config
    )
    data = _read_yaml(config_path)
    return AppConfig(**data)


def load_risk_config(path: str | Path | None = None) -> RiskConfig:
    settings = AppSettings()
    config_path = Path(
        path or os.environ.get("DOGESHTRADER_RISK_CONFIG") or settings.dogeshtrader_risk_config
    )
    data = _read_yaml(config_path)
    return RiskConfig(**data)


def load_strategy_config(path: str | Path) -> StrategyConfig:
    data = _read_yaml(path)
    return StrategyConfig(**data)


def get_settings() -> AppSettings:
    return AppSettings()


__all__ = [
    "AppConfig",
    "AppSettings",
    "BacktestWindow",
    "CostsConfig",
    "InstrumentRef",
    "RiskConfig",
    "SlippageConfig",
    "StrategyConfig",
    "get_settings",
    "load_app_config",
    "load_risk_config",
    "load_strategy_config",
]
