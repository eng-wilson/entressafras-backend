"""Reference crop economics: the versioned seed and the build script's pure functions."""

import pytest

from app.services.crop_economics import (
    load_crop_economics,
    revenue_ceiling,
    rotation_gross_revenue,
    water_adjusted_revenue,
)
from app.services.rotations import (
    COMMON_BEAN,
    COWPEA,
    MILLET,
    ROTATION_CROP_CODES,
    SORGHUM,
    SUNFLOWER,
)
from scripts.build_crop_economics import (
    ANNUAL,
    PRICE_ELEMENT,
    collect_faostat,
    latest_mean,
    latest_pinksheet,
)


def test_every_rotation_crop_has_price_yield_and_revenue() -> None:
    economics = load_crop_economics()
    assert set(ROTATION_CROP_CODES) <= set(economics)
    for code in ROTATION_CROP_CODES:
        item = economics[code]
        assert item.farm_gate_price.value > 0 and item.farm_gate_price.unit == "USD/t"
        assert item.crop_yield.value > 0 and item.crop_yield.unit == "kg/ha"
        expected = item.crop_yield.value / 1000 * item.farm_gate_price.value
        assert item.gross_revenue_usd_ha == pytest.approx(expected, abs=0.1)
        assert item.farm_gate_price.source.startswith("FAOSTAT")
        assert item.crop_yield.source.startswith("FAOSTAT")


@pytest.mark.parametrize("code", [MILLET, COWPEA])
def test_crops_without_their_own_series_are_flagged_as_proxy(code: int) -> None:
    item = load_crop_economics()[code]
    for value in (item.farm_gate_price, item.crop_yield):
        assert value.method == "proxy"
        assert value.proxy_of and value.note


@pytest.mark.parametrize("code", [SORGHUM, MILLET, COMMON_BEAN, COWPEA, SUNFLOWER])
def test_crops_without_an_international_benchmark(code: int) -> None:
    assert load_crop_economics()[code].international_price is None


def test_rotation_gross_revenue_needs_every_crop() -> None:
    economics = load_crop_economics()
    soy, maize = economics[ROTATION_CROP_CODES[0]], economics[ROTATION_CROP_CODES[1]]
    total = rotation_gross_revenue([soy, maize])
    assert total == pytest.approx(soy.gross_revenue_usd_ha + maize.gross_revenue_usd_ha, abs=0.1)
    assert rotation_gross_revenue([soy, None]) is None
    assert rotation_gross_revenue([]) is None


def _row(year: int, value: str, months: str = ANNUAL, area: str = "Brazil") -> dict:
    return {
        "Area": area,
        "Item": "Sorghum",
        "Element": PRICE_ELEMENT,
        "Year": str(year),
        "Months": months,
        "Value": value,
    }


def test_collect_faostat_keeps_annual_values_of_wanted_series() -> None:
    rows = [
        _row(2023, "120"),
        _row(2024, "130"),
        _row(2024, "999", months="January"),
        _row(2024, "999", area="Chad"),
        _row(2022, ""),
    ]
    series = collect_faostat(rows, {("Brazil", "Sorghum", PRICE_ELEMENT)})
    assert series == {("Brazil", "Sorghum", PRICE_ELEMENT): {2023: 120.0, 2024: 130.0}}


def test_latest_mean_averages_the_latest_years_with_data() -> None:
    assert latest_mean({2019: 10.0, 2022: 20.0, 2023: 30.0, 2024: 40.0}, 3) == (2022, 2024, 30.0)
    assert latest_mean({2024: 5.0}, 3) == (2024, 2024, 5.0)
    with pytest.raises(ValueError):
        latest_mean({}, 3)


def test_latest_pinksheet_skips_missing_values_and_drops_stale_series() -> None:
    periods = ["2025M10", "2025M11", "2025M12"]
    assert latest_pinksheet(periods, [440.0, 450.5, "…"], "2025M12") == ("2025-11", 450.5)
    assert latest_pinksheet(["2020M01", "2025M12"], [100.0, "…"], "2025M12") is None
    assert latest_pinksheet(periods, ["…", "…", float("nan")], "2025M12") is None


def test_revenue_ceiling_is_the_best_rotation_without_loss() -> None:
    economics = load_crop_economics()
    soy, maize = economics[ROTATION_CROP_CODES[0]], economics[ROTATION_CROP_CODES[1]]
    sorghum = economics[SORGHUM]
    pairs = [(soy, maize), (soy, sorghum), (maize, None)]
    expected = max(
        soy.gross_revenue_usd_ha + maize.gross_revenue_usd_ha,
        soy.gross_revenue_usd_ha + sorghum.gross_revenue_usd_ha,
    )
    assert revenue_ceiling(pairs) == pytest.approx(expected, abs=0.05)
    assert revenue_ceiling([(soy,)]) == pytest.approx(soy.gross_revenue_usd_ha, abs=0.05)


def test_revenue_ceiling_is_none_without_rotations_with_data() -> None:
    economics = load_crop_economics()
    assert revenue_ceiling([]) is None
    assert revenue_ceiling([(economics[SORGHUM], None)]) is None


def test_water_adjusted_revenue_discounts_each_crop() -> None:
    economics = load_crop_economics()
    soy, maize = economics[ROTATION_CROP_CODES[0]], economics[ROTATION_CROP_CODES[1]]
    expected = soy.gross_revenue_usd_ha * 0.9 + maize.gross_revenue_usd_ha * 0.75
    assert water_adjusted_revenue([(soy, 10.0), (maize, 25.0)]) == pytest.approx(expected)
    assert water_adjusted_revenue([(soy, 0.0)]) == pytest.approx(soy.gross_revenue_usd_ha)
    assert water_adjusted_revenue([(soy, None), (maize, 0.0)]) is None
    assert water_adjusted_revenue([(None, 0.0)]) is None
    assert water_adjusted_revenue([]) is None
