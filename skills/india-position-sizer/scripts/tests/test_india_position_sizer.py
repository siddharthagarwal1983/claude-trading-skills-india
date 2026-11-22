"""Tests for the India (NSE cash, delivery) position sizer."""

import json

import india_position_sizer as ips
import pytest


def params(**overrides):
    base = {"account_size": 1_000_000, "entry_price": 1000.0, "stop_price": 950.0, "risk_pct": 1.0}
    base.update(overrides)
    return ips.SizingParameters(**base)


# --------------------------------------------------------------------------- costs


def test_delivery_costs_for_round_trip_at_stop():
    costs = ips.delivery_costs(shares=100, entry_price=1000.0, exit_price=950.0)
    # Buy ₹1,00,000: STT 100, exchange 2.97, SEBI 0.10, stamp 15, GST 18% of 3.07
    assert costs["buy"]["stt"] == pytest.approx(100.0)
    assert costs["buy"]["stamp_duty"] == pytest.approx(15.0)
    assert costs["buy"]["exchange_txn"] == pytest.approx(2.97)
    assert costs["buy"]["gst"] == pytest.approx(0.18 * (2.97 + 0.10))
    # Sell ₹95,000: STT 95, no stamp duty, DP charge once
    assert costs["sell"]["stt"] == pytest.approx(95.0)
    assert costs["sell"]["stamp_duty"] == 0
    assert costs["sell"]["dp_charge"] == pytest.approx(15.93)
    assert costs["total"] == pytest.approx(costs["buy"]["total"] + costs["sell"]["total"])


def test_brokerage_attracts_gst_on_both_legs():
    with_fee = ips.delivery_costs(10, 500.0, 480.0, brokerage_per_order=20.0)
    without = ips.delivery_costs(10, 500.0, 480.0)
    assert with_fee["total"] - without["total"] == pytest.approx(2 * 20.0 * 1.18)


# --------------------------------------------------------------------------- fixed fractional


def test_fixed_fractional_without_costs_matches_plain_formula():
    result = ips.calculate_position(params(include_costs=False))
    assert result["final_recommended_shares"] == 200  # ₹10,000 / ₹50
    assert result["final_risk_inr"] == pytest.approx(10_000.0)
    assert result["currency"] == "INR"


def test_costs_shrink_the_position_and_total_risk_fits_the_budget():
    result = ips.calculate_position(params())
    assert result["final_recommended_shares"] == 191
    assert result["final_risk_inr"] <= 10_000.0
    assert result["final_risk_inr"] == pytest.approx(
        191 * 50 + result["costs_at_stop"]["total"], abs=0.01
    )
    assert result["final_risk_pct"] <= 1.0
    assert result["price_risk_inr"] == pytest.approx(9550.0)


def test_whole_shares_only():
    result = ips.calculate_position(params(account_size=333_333, include_costs=False))
    assert isinstance(result["final_recommended_shares"], int)


def test_sme_lot_size_rounds_down_to_whole_lots():
    result = ips.calculate_position(
        params(account_size=500_000, entry_price=100.0, stop_price=95.0, lot_size=1200)
    )
    shares = result["final_recommended_shares"]
    assert shares % 1200 == 0 and shares == 0  # ₹5,000 budget buys < 1 lot after costs
    small_lot = ips.calculate_position(
        params(account_size=500_000, entry_price=100.0, stop_price=95.0, lot_size=200)
    )
    assert small_lot["final_recommended_shares"] == 800


def test_stop_must_be_below_entry():
    with pytest.raises(ValueError, match="below entry"):
        ips.calculate_position(params(stop_price=1000.0))


def test_risk_budget_smaller_than_fixed_charges_gives_zero_shares():
    result = ips.calculate_position(params(account_size=1_000, risk_pct=1.0))
    assert result["final_recommended_shares"] == 0
    assert result["final_risk_inr"] == 0


# --------------------------------------------------------------------------- ATR


def test_atr_stop_rounds_down_to_tick():
    result = ips.calculate_position(
        params(stop_price=None, atr=20.03, atr_multiplier=2.0, include_costs=False)
    )
    assert result["parameters"]["stop_price"] == pytest.approx(959.90)  # 1000 - 40.06 -> tick
    assert result["final_recommended_shares"] == 249  # floor(10000 / 40.10)


def test_atr_stop_uses_nse_tick_band_above_1000():
    result = ips.calculate_position(
        params(
            entry_price=1234.0, stop_price=None, atr=20.03, atr_multiplier=2.0, include_costs=False
        )
    )
    # 1234 - 40.06 = 1193.94 -> ₹0.10 tick above ₹1,000 -> 1193.90
    assert result["parameters"]["stop_price"] == pytest.approx(1193.9)
    assert result["parameters"]["tick_size"] == 0.10
    assert result["parameters"]["tick_source"] == "nse_band"


def test_atr_stop_tick_band_changes_result():
    # 2,000 - 2 x 12.37 = 1975.26: the old ₹0.05 tick gave 1975.25, which is not a valid
    # NSE price above ₹1,000; the ₹0.10 band gives 1975.20
    result = ips.calculate_position(
        params(
            entry_price=2000.0, stop_price=None, atr=12.37, atr_multiplier=2.0, include_costs=False
        )
    )
    assert result["parameters"]["stop_price"] == pytest.approx(1975.2)


def test_atr_stop_uses_paisa_tick_below_250():
    result = ips.calculate_position(
        params(
            entry_price=200.0, stop_price=None, atr=3.333, atr_multiplier=2.0, include_costs=False
        )
    )
    assert result["parameters"]["stop_price"] == pytest.approx(193.33)
    assert result["parameters"]["tick_size"] == 0.01


def test_explicit_tick_size_still_wins():
    result = ips.calculate_position(
        params(
            entry_price=2000.0,
            stop_price=None,
            atr=12.37,
            atr_multiplier=2.0,
            include_costs=False,
            tick_size=0.05,
        )
    )
    assert result["parameters"]["stop_price"] == pytest.approx(1975.25)
    assert result["parameters"]["tick_source"] == "user"


@pytest.mark.parametrize(
    "price,tick",
    [
        (249.99, 0.01),
        (250, 0.05),
        (1000, 0.05),
        (1000.05, 0.10),
        (5000.5, 0.5),
        (10001, 1.0),
        (20005, 5.0),
    ],
)
def test_nse_tick_size_bands(price, tick):
    assert ips.nse_tick_size(price) == tick


def test_tick_bands_match_shared_india_data_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "scripts" / "india_data" / "india_data.py"
    if not path.exists():
        pytest.skip("shared India data module not present (standalone install)")
    spec = importlib.util.spec_from_file_location("india_data_for_sizer_parity", path)
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    for price in (
        0.5,
        99.99,
        249.99,
        250,
        999.95,
        1000,
        1000.1,
        4999,
        5000,
        5001,
        9999,
        10000,
        10001,
        19999,
        20000,
        20001,
        50000,
    ):
        assert ips.nse_tick_size(price) == shared.nse_tick_size(price), price


def test_atr_stop_must_stay_positive():
    with pytest.raises(ValueError, match="ATR"):
        ips.calculate_position(params(stop_price=None, atr=600.0, atr_multiplier=2.0))


# --------------------------------------------------------------------------- constraints


def test_max_position_pct_binds():
    result = ips.calculate_position(params(max_position_pct=10.0))
    assert result["final_recommended_shares"] == 100  # ₹1,00,000 / ₹1,000
    assert result["binding_constraint"] == "max_position_pct"


def test_sector_limit_uses_remaining_room():
    result = ips.calculate_position(
        params(max_sector_pct=25.0, current_sector_exposure=20.0, sector="Financial Services")
    )
    assert result["final_recommended_shares"] == 50  # 5% of ₹10 lakh / ₹1,000
    assert result["binding_constraint"] == "max_sector_pct"


# --------------------------------------------------------------------------- Kelly budget


def test_kelly_budget_mode():
    result = ips.calculate_position(
        ips.SizingParameters(account_size=1_000_000, win_rate=0.55, avg_win=2.5, avg_loss=1.0)
    )
    assert result["mode"] == "budget"
    assert result["recommended_risk_budget"] == pytest.approx(185_000.0)


# --------------------------------------------------------------------------- formatting + CLI


@pytest.mark.parametrize(
    "value,expected",
    [
        (1234567.5, "₹12,34,567.50"),
        (999, "₹999.00"),
        (100000, "₹1,00,000.00"),
        (-2500.256, "-₹2,500.26"),
        (0, "₹0.00"),
    ],
)
def test_format_inr_uses_indian_grouping(value, expected):
    assert ips.format_inr(value) == expected


def test_markdown_report_mentions_rupees_and_costs():
    report = ips.generate_markdown_report(ips.calculate_position(params()))
    assert "₹" in report and "$" not in report
    assert "STT" in report


def test_cli_writes_reports(tmp_path, capsys):
    rc = ips.main(
        [
            "--account-size",
            "1000000",
            "--entry",
            "1000",
            "--stop",
            "950",
            "--risk-pct",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.startswith("india_position_sizer_") and f.endswith(".json") for f in files)
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data["final_recommended_shares"] == 191
    assert "191 shares" in capsys.readouterr().out


def test_cli_rejects_mixed_modes(capsys):
    with pytest.raises(SystemExit) as exc:
        ips.main(["--account-size", "1000", "--risk-pct", "1", "--win-rate", "0.5"])
    assert exc.value.code == 2


def test_cli_reports_value_errors_on_stderr(tmp_path, capsys):
    rc = ips.main(
        [
            "--account-size",
            "1000000",
            "--entry",
            "100",
            "--stop",
            "120",
            "--risk-pct",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1
    assert "Error" in capsys.readouterr().err
