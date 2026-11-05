"""Strategy discovery through entry points and typed parameter validation."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest
from pydantic import BaseModel

from dogeshtrader.core.errors import StrategyError
from dogeshtrader.core.models import Candle, OrderIntent, StrategyContext
from dogeshtrader.strategy import registry
from dogeshtrader.strategy.base import Strategy


class _PluginParams(BaseModel, extra="forbid"):
    lookback: int = 10


class PluginStrategy(Strategy):
    Params = _PluginParams

    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        return []


class BrokenImport:
    def __init__(self) -> None:  # pragma: no cover - never constructed
        raise AssertionError


@pytest.fixture()
def fake_plugins(monkeypatch: pytest.MonkeyPatch) -> list[EntryPoint]:
    eps = [
        EntryPoint(
            name="third_party_plugin",
            value=f"{__name__}:PluginStrategy",
            group=registry.ENTRY_POINT_GROUP,
        ),
        EntryPoint(
            name="broken_plugin",
            value="nonexistent_pkg_for_tests.module:Thing",
            group=registry.ENTRY_POINT_GROUP,
        ),
    ]
    monkeypatch.setattr(registry, "entry_points", lambda group: eps)
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    return eps


def test_entry_point_plugin_is_discovered_and_registered(fake_plugins: list[EntryPoint]) -> None:
    cls = registry.get_strategy("third_party_plugin")
    assert cls is PluginStrategy
    assert "third_party_plugin" in registry.available_strategies()


def test_plugin_import_failure_is_a_strategy_error(fake_plugins: list[EntryPoint]) -> None:
    with pytest.raises(StrategyError, match="broken_plugin"):
        registry.get_strategy("broken_plugin")


def test_unknown_strategy_raises(fake_plugins: list[EntryPoint]) -> None:
    with pytest.raises(StrategyError, match="not found"):
        registry.get_strategy("nope_not_here")


def test_name_collision_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_REGISTRY", {})
    registry.register("dup")(PluginStrategy)

    class Other(PluginStrategy):
        pass

    with pytest.raises(StrategyError, match="collision"):
        registry.register("dup")(Other)


def test_register_module_wraps_import_errors() -> None:
    with pytest.raises(StrategyError):
        registry.register_module("nonexistent_pkg_for_tests")


def test_typed_params_apply_defaults() -> None:
    strat = PluginStrategy({})
    assert strat.params == {"lookback": 10}
    assert isinstance(strat.settings, _PluginParams)


def test_typed_params_reject_unknown_keys() -> None:
    with pytest.raises(StrategyError, match="invalid params"):
        PluginStrategy({"lookbak": 5})


def test_typed_params_reject_wrong_types() -> None:
    with pytest.raises(StrategyError, match="invalid params"):
        PluginStrategy({"lookback": "ten"})


def test_untyped_strategy_keeps_raw_params() -> None:
    class Loose(Strategy):
        def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
            return []

    assert Loose({"anything": 1}).get_param("anything") == 1
