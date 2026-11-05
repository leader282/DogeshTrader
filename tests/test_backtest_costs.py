from __future__ import annotations

from dogeshtrader.backtest.costs import (
    BpsCostModel,
    IndiaEquitySimpleCostModel,
    build_cost_model,
)


def test_build_cost_model_maps_app_cost_aliases_for_india_equity() -> None:
    model = build_cost_model("india_equity_simple", bps=3.0, fee_per_order=7.5)

    assert isinstance(model, IndiaEquitySimpleCostModel)
    assert model.brokerage_intraday == 7.5


def test_build_cost_model_uses_bps_kwargs_for_bps_model() -> None:
    model = build_cost_model("bps_only", bps=1.5, fee_per_order=2.5)

    assert isinstance(model, BpsCostModel)
    assert model.bps == 1.5
    assert model.fee_per_order == 2.5
