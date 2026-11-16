from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.agro import DiaClima, et0_dia
from app.services.climate_scenario import SCENARIOS, ClimateScenario, apply_scenario

ELEV, LAT = 370.0, -12.5


def _day(day: date, chuva: float | None = 5.0) -> DiaClima:
    values = dict(tmax=32.0, tmin=20.0, tdew=18.0, ur=70.0, vento=2.0, rad=20.0)
    et0, method = et0_dia(
        data=day,
        u2=values["vento"],
        rs=values["rad"],
        elev=ELEV,
        lat=LAT,
        **{k: values[k] for k in ("tmax", "tmin", "tdew", "ur")},
    )
    return DiaClima(
        data=day, chuva=chuva, gwettop=None, gwetroot=None, et0=et0, et0_metodo=method, **values
    )


def _series(start: date, n: int) -> list[DiaClima]:
    return [_day(start + timedelta(days=i), chuva=float(i)) for i in range(n)]


def _apply(days, **deltas) -> list[DiaClima]:
    scenario = ClimateScenario(id="warmer", label="test", **deltas)
    return apply_scenario(days, scenario, elev=ELEV, lat=LAT)


def test_historical_returns_the_same_series() -> None:
    days = _series(date(2020, 9, 1), 30)
    assert apply_scenario(days, SCENARIOS["historical"], elev=ELEV, lat=LAT) == days


def test_warming_raises_temperatures_and_et0() -> None:
    days = _series(date(2020, 9, 1), 30)
    warmed = _apply(days, delta_temp_c=2.0)
    for before, after in zip(days, warmed, strict=True):
        assert after.tmax == before.tmax + 2 and after.tmin == before.tmin + 2
        assert after.et0 > before.et0
        assert after.chuva == before.chuva and after.data == before.data


def test_rain_change_scales_rainfall_and_keeps_missing_values() -> None:
    days = _series(date(2021, 1, 1), 10)
    days[3] = replace(days[3], chuva=None)
    drier = _apply(days, rain_change_pct=-10.0)
    for before, after in zip(days, drier, strict=True):
        if before.chuva is None:
            assert after.chuva is None
        else:
            assert after.chuva == pytest.approx(before.chuva * 0.9)


def test_onset_delay_shifts_rainfall_only_from_mid_august_to_december() -> None:
    days = _series(date(2020, 8, 1), 200)  # Aug 2020 to Feb 2021
    by_date = {day.data: day for day in days}
    delayed = {day.data: day for day in _apply(days, onset_delay_days=10)}
    assert delayed[date(2020, 10, 15)].chuva == by_date[date(2020, 10, 5)].chuva
    assert delayed[date(2021, 1, 10)].chuva == by_date[date(2021, 1, 10)].chuva
    assert delayed[date(2020, 8, 10)].chuva == by_date[date(2020, 8, 10)].chuva


def test_onset_delay_uses_zero_before_the_start_of_the_series() -> None:
    days = _series(date(2020, 8, 15), 5)
    delayed = _apply(days, onset_delay_days=10)
    assert all(day.chuva == 0.0 for day in delayed)


def test_presets_start_with_historical_and_only_it_has_no_change() -> None:
    assert next(iter(SCENARIOS)) == "historical"
    assert [s.is_historical for s in SCENARIOS.values()] == [True, False, False]
