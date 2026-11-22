"""Pure trade-price, risk and sizing tests for the India breakout planner."""

import india_breakout_risk as risk
import pytest
from _india_data import delivery_leg_charges

# --------------------------------------------------------------------------- prices


def test_derive_prices_round_onto_nse_ticks():
    signal, worst, stop = risk.derive_trade_prices(435.95, 404.0)
    assert signal == 436.4  # 436.39 rounded UP to the ₹0.05 tick
    assert worst == 444.65  # 444.669 rounded DOWN (never chase past the cap)
    assert stop == 399.95  # 399.96 rounded DOWN


def test_derive_prices_use_each_price_band():
    signal, worst, stop = risk.derive_trade_prices(1234.56, 1150.0)
    assert signal == 1235.8  # ₹0.10 tick above ₹1,000
    assert worst == 1259.2
    assert stop == 1138.5
    signal, _, stop = risk.derive_trade_prices(123.45, 110.0)
    assert signal == 123.58  # ₹0.01 tick below ₹250
    assert stop == 108.9


def test_worst_entry_never_below_signal():
    signal, worst, _ = risk.derive_trade_prices(
        100.0, 90.0, pivot_buffer_pct=0.0, max_chase_pct=0.0
    )
    assert worst == signal == 100.0


@pytest.mark.parametrize(
    "pivot,low,kwargs",
    [
        (0, 90, {}),
        (100, 0, {}),
        (100, 90, {"pivot_buffer_pct": 3.0, "max_chase_pct": 2.0}),
        (100, 102, {}),
    ],
)
def test_derive_prices_validation(pivot, low, kwargs):
    with pytest.raises(ValueError):
        risk.derive_trade_prices(pivot, low, **kwargs)


def test_risks_and_r_multiples():
    assert risk.calculate_risks(436.4, 444.65, 399.95) == (8.35, 10.05)
    targets = risk.calculate_r_multiples(444.65, 399.95)
    assert targets == {"1.0R": 489.35, "2.0R": 534.05, "3.0R": 578.75}


@pytest.mark.parametrize(
    "score,band,mult",
    [
        (95, "textbook", 1.75),
        (85, "strong", 1.0),
        (72.5, "good", 0.75),
        (65, "developing", 0.0),
        (40, "weak", 0.0),
    ],
)
def test_rating_bands_match_us_planner(score, band, mult):
    assert risk.get_rating_band(score) == band
    assert risk.get_sizing_multiplier(band) == mult


# --------------------------------------------------------------------------- sizing


def total_risk(shares, worst, stop):
    buy = delivery_leg_charges(shares * worst, buy=True)["total"]
    sell = delivery_leg_charges(shares * stop, buy=False)["total"]
    return shares * (worst - stop) + buy + sell


def test_size_counts_round_trip_charges_inside_the_budget():
    out = risk.size_position(
        worst_entry=444.65,
        stop_loss=399.95,
        account_size=1_000_000,
        risk_pct=0.5,
        sizing_multiplier=1.0,
        max_position_pct=100,
        max_sector_pct=100,
    )
    budget = 5000.0
    n = out["shares"]
    assert total_risk(n, 444.65, 399.95) <= budget < total_risk(n + 1, 444.65, 399.95)
    assert n < int(budget / (444.65 - 399.95))  # charges cost some shares
    assert out["risk_inr"] == pytest.approx(total_risk(n, 444.65, 399.95), abs=0.02)
    assert out["binding_constraint"] is None
    assert out["buy_charges"] > 0


def test_size_zero_multiplier():
    out = risk.size_position(
        worst_entry=100, stop_loss=90, account_size=100_000, risk_pct=1, sizing_multiplier=0
    )
    assert out["shares"] == 0
    assert out["binding_constraint"] == "sizing_multiplier_zero"


def test_max_position_binds():
    out = risk.size_position(
        worst_entry=100, stop_loss=99, account_size=1_000_000, risk_pct=1, sizing_multiplier=1
    )
    assert out["shares"] == 1000  # 10% of ₹10 lakh at ₹100
    assert out["binding_constraint"] == "max_position_pct"


def test_sector_room_binds():
    out = risk.size_position(
        worst_entry=100,
        stop_loss=99,
        account_size=1_000_000,
        risk_pct=1,
        sizing_multiplier=1,
        max_position_pct=50,
        max_sector_pct=30,
        current_sector_exposure_pct=28,
    )
    assert out["shares"] == 200
    assert out["binding_constraint"] == "max_sector_pct"


def test_turnover_cap_binds_for_illiquid_stock():
    out = risk.size_position(
        worst_entry=100,
        stop_loss=95,
        account_size=10_000_000,
        risk_pct=1,
        sizing_multiplier=1,
        max_position_pct=100,
        max_sector_pct=100,
        avg_turnover_cr=0.5,  # ₹50 lakh a day
        max_adv_pct=1.0,
    )
    assert out["shares"] == 500  # 1% of ₹50 lakh = ₹50,000 / ₹100
    assert out["binding_constraint"] == "max_adv_pct"


def test_cash_binds_and_includes_buy_charges():
    out = risk.size_position(
        worst_entry=100,
        stop_loss=90,
        account_size=1_000_000,
        risk_pct=2,
        sizing_multiplier=1,
        cash_available=10_000,
    )
    n = out["shares"]
    assert out["binding_constraint"] == "cash"
    cost = n * 100 + delivery_leg_charges(n * 100, buy=True)["total"]
    nxt = (n + 1) * 100 + delivery_leg_charges((n + 1) * 100, buy=True)["total"]
    assert cost <= 10_000 < nxt


def test_size_rejects_stop_at_or_above_entry():
    with pytest.raises(ValueError):
        risk.size_position(
            worst_entry=100, stop_loss=100, account_size=1e6, risk_pct=1, sizing_multiplier=1
        )
