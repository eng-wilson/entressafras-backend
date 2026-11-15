"""Pure FAO-56 daily water balance used by the rotation risk model."""

import random
from datetime import date, timedelta

import pytest

from app.schemas.crop import (
    AllowableDepletion,
    CropCoefficient,
    RootDepth,
    StageLengths,
    WaterRequirements,
    YieldResponseFactor,
)
from app.services.agro import DiaClima
from app.services.soil import (
    MAX_EVAPORATION_COEFFICIENT,
    SURFACE_EVAPORATION_LIMITS,
    SoilType,
    calculate_available_water_capacity,
)
from app.services.water_balance import (
    DailyTrace,
    SoilWaterState,
    bare_soil_step,
    crop_step,
    daily_crop_parameters,
    evaporation_reduction_coefficient,
    simulate_phases,
    simulate_season,
)

START = date(2020, 9, 1)
CYCLE_DAYS = 80


def make_water_requirements(
    *,
    season_yield_response: float = 0.85,
    stage_yield_response: tuple[float, float, float, float] = (0.4, 0.6, 1.2, 0.3),
    name: str = "TEST",
) -> WaterRequirements:
    initial, development, mid_season, late_season = stage_yield_response
    return WaterRequirements(
        profile=name,
        profile_name=name,
        crop_coefficient=CropCoefficient(initial=0.4, mid_season=1.2, late_season=0.6),
        stage_length_days=StageLengths(
            initial=10, development=20, mid_season=30, late_season=20, total=CYCLE_DAYS
        ),
        root_depth_m=RootDepth(initial=0.3, max=1.0),
        allowable_depletion=AllowableDepletion(initial=0.5, mid_season=0.6, late_season=0.8),
        yield_response_factor=YieldResponseFactor(
            initial=initial,
            development=development,
            mid_season=mid_season,
            late_season=late_season,
            season=season_yield_response,
        ),
    )


def make_climate_days(
    days_count: int, rainfall_mm, reference_evapotranspiration_mm, start: date = START
) -> list[DiaClima]:
    """`rainfall_mm` and `reference_evapotranspiration_mm` are constants or callables of the
    day index."""
    climate_days = []
    for day_index in range(days_count):
        rainfall = rainfall_mm(day_index) if callable(rainfall_mm) else rainfall_mm
        reference_evapotranspiration = (
            reference_evapotranspiration_mm(day_index)
            if callable(reference_evapotranspiration_mm)
            else reference_evapotranspiration_mm
        )
        climate_days.append(
            DiaClima(
                data=start + timedelta(days=day_index),
                tmax=None,
                tmin=None,
                tdew=None,
                ur=None,
                vento=None,
                rad=None,
                chuva=rainfall,
                gwettop=None,
                gwetroot=None,
                et0=reference_evapotranspiration,
                et0_metodo=None,
            )
        )
    return climate_days


# --- Daily crop parameters ------------------------------------------------------------------


def test_daily_parameters_follow_stage_shapes() -> None:
    requirements = make_water_requirements()
    stage, first_day = daily_crop_parameters(requirements, 0)
    assert stage == "initial"
    assert first_day.crop_coefficient == 0.4
    assert first_day.depletion_fraction == 0.5
    assert first_day.root_depth_m == 0.3
    assert daily_crop_parameters(requirements, 9)[1].crop_coefficient == 0.4
    # development: linear initial -> mid-season value, reaching it on its last day (index 29)
    stage, mid_development = daily_crop_parameters(requirements, 19)
    assert stage == "development"
    assert mid_development.crop_coefficient == pytest.approx(0.4 + 0.8 * 10 / 20)
    assert mid_development.depletion_fraction == pytest.approx(0.5 + 0.1 * 10 / 20)
    assert daily_crop_parameters(requirements, 29)[1].crop_coefficient == pytest.approx(1.2)
    # roots grow over initial + development, then stay at the maximum depth
    assert daily_crop_parameters(requirements, 15)[1].root_depth_m == pytest.approx(
        0.3 + 0.7 * 15 / 30
    )
    assert daily_crop_parameters(requirements, 30)[1].root_depth_m == pytest.approx(1.0)
    stage, mid_season = daily_crop_parameters(requirements, 45)
    assert stage == "mid_season"
    assert mid_season.crop_coefficient == 1.2
    assert mid_season.depletion_fraction == 0.6
    assert mid_season.root_depth_m == 1.0
    stage, last_day = daily_crop_parameters(requirements, CYCLE_DAYS - 1)
    assert stage == "late_season"
    assert last_day.crop_coefficient == pytest.approx(0.6)
    assert last_day.depletion_fraction == pytest.approx(0.8)
    with pytest.raises(ValueError):
        daily_crop_parameters(requirements, CYCLE_DAYS)


# --- Single steps ---------------------------------------------------------------------------


def test_evaporation_reduction_is_one_until_readily_evaporable_then_falls_to_zero() -> None:
    limits = SURFACE_EVAPORATION_LIMITS[SoilType.MEDIUM]
    readily, total = limits.readily_evaporable_water_mm, limits.total_evaporable_water_mm
    assert evaporation_reduction_coefficient(0, SoilType.MEDIUM) == 1
    assert evaporation_reduction_coefficient(readily, SoilType.MEDIUM) == 1
    assert evaporation_reduction_coefficient((readily + total) / 2, SoilType.MEDIUM) == (
        pytest.approx(0.5)
    )
    assert evaporation_reduction_coefficient(total, SoilType.MEDIUM) == 0


def test_bare_soil_evaporation_is_stage_one_then_declines() -> None:
    state = SoilWaterState(root_zone_depletion_mm=0.0, surface_depletion_mm=0.0)
    daily_evaporation = []
    for _ in range(10):
        step = bare_soil_step(
            state,
            rainfall_mm=0.0,
            reference_evapotranspiration_mm=5.0,
            soil=SoilType.MEDIUM,
            root_depth_m=0.3,
        )
        daily_evaporation.append(step.evapotranspiration_mm)
        state = step.state
    assert daily_evaporation[0] == pytest.approx(MAX_EVAPORATION_COEFFICIENT * 5.0)
    assert all(
        later <= earlier
        for earlier, later in zip(daily_evaporation, daily_evaporation[1:], strict=False)
    )
    assert daily_evaporation[-1] < daily_evaporation[0]


def test_crop_step_has_no_stress_below_readily_available_water_and_drains_excess() -> None:
    _, parameters = daily_crop_parameters(make_water_requirements(), 45)
    available_water_mm = calculate_available_water_capacity(
        SoilType.MEDIUM, parameters.root_depth_m
    )
    wet = SoilWaterState(root_zone_depletion_mm=0.0, surface_depletion_mm=0.0)
    step = crop_step(wet, 20.0, 5.0, parameters, SoilType.MEDIUM)
    assert step.evapotranspiration_mm == pytest.approx(1.2 * 5.0)
    assert step.deep_drainage_mm == pytest.approx(20.0 - 6.0)
    assert step.state.root_zone_depletion_mm == 0
    dry = SoilWaterState(root_zone_depletion_mm=available_water_mm, surface_depletion_mm=0.0)
    dry_step = crop_step(dry, 0.0, 5.0, parameters, SoilType.MEDIUM)
    assert dry_step.evapotranspiration_mm == 0
    assert dry_step.state.root_zone_depletion_mm == pytest.approx(available_water_mm)


# --- Season simulation ----------------------------------------------------------------------


def test_total_drought_gives_zero_water_satisfaction_and_full_yield_response_loss() -> None:
    climate_days = make_climate_days(120, 0.0, 5.0)
    sowing_date = START + timedelta(days=10)
    [soybean_like] = simulate_season(
        climate_days,
        START,
        [(make_water_requirements(), sowing_date)],
        SoilType.MEDIUM,
        initial_water_fraction=0.0,
    )
    assert soybean_like.is_valid
    assert soybean_like.season_water_satisfaction_index == pytest.approx(0.0)
    assert soybean_like.yield_loss_fraction == pytest.approx(0.85)
    [maize_like] = simulate_season(
        climate_days,
        START,
        [(make_water_requirements(season_yield_response=1.25), sowing_date)],
        SoilType.MEDIUM,
        initial_water_fraction=0.0,
    )
    assert maize_like.yield_loss_fraction == 1.0


def test_daily_rainfall_above_reference_evapotranspiration_gives_no_loss() -> None:
    climate_days = make_climate_days(120, 10.0, 5.0)
    sowing_date = START + timedelta(days=10)
    [result] = simulate_season(
        climate_days,
        START,
        [(make_water_requirements(), sowing_date)],
        SoilType.SANDY,
        initial_water_fraction=0.2,
    )
    assert result.season_water_satisfaction_index == pytest.approx(1.0)
    assert all(v == pytest.approx(1.0) for v in result.water_satisfaction_index.values())
    assert result.yield_loss_fraction == pytest.approx(0.0)
    assert result.critical_stage is None
    assert result.harvest_date == sowing_date + timedelta(days=CYCLE_DAYS - 1)


def test_sandy_soil_loses_more_than_clayey() -> None:
    climate_days = make_climate_days(150, lambda day: 30.0 if day % 12 == 0 else 0.0, 5.0)
    sowings = [(make_water_requirements(), START + timedelta(days=20))]
    [sandy] = simulate_season(climate_days, START, sowings, SoilType.SANDY, 0.2)
    [clayey] = simulate_season(climate_days, START, sowings, SoilType.CLAYEY, 0.2)
    assert sandy.yield_loss_fraction > clayey.yield_loss_fraction


def test_critical_stage_is_largest_yield_response_times_deficit() -> None:
    sowing_date = START + timedelta(days=10)
    mid_season_days = range(10 + 30, 10 + 60)  # mid-season days in the series
    climate_days = make_climate_days(120, lambda day: 0.0 if day in mid_season_days else 10.0, 5.0)
    [result] = simulate_season(
        climate_days, START, [(make_water_requirements(), sowing_date)], SoilType.MEDIUM, 0.2
    )
    assert result.critical_stage == "mid_season"
    assert result.water_satisfaction_index["mid_season"] < 1
    # Same deficit, but with no mid-season yield response the critical stage moves elsewhere.
    insensitive_mid_season = make_water_requirements(stage_yield_response=(0.4, 0.6, 0.0, 0.3))
    [result] = simulate_season(
        climate_days, START, [(insensitive_mid_season, sowing_date)], SoilType.MEDIUM, 0.2
    )
    assert result.critical_stage != "mid_season"


@pytest.mark.parametrize("soil", list(SoilType))
def test_depletions_stay_within_bounds(soil: SoilType) -> None:
    rng = random.Random(42)
    rainfall = [rng.choice([0.0, 0.0, 0.0, rng.uniform(0, 60)]) for _ in range(250)]
    reference_evapotranspiration = [rng.uniform(2, 7) for _ in range(250)]
    climate_days = make_climate_days(
        250, lambda day: rainfall[day], lambda day: reference_evapotranspiration[day]
    )
    first_sowing = START + timedelta(days=30)
    second_sowing = first_sowing + timedelta(days=CYCLE_DAYS + 5)
    sowings = [
        (make_water_requirements(), first_sowing),
        (make_water_requirements(season_yield_response=1.25), second_sowing),
    ]
    trace: list[DailyTrace] = []
    simulate_season(climate_days, START, sowings, soil, 0.2, trace=trace)
    total_evaporable = SURFACE_EVAPORATION_LIMITS[soil].total_evaporable_water_mm
    assert trace
    for day in trace:
        assert -1e-9 <= day.root_zone_depletion_mm <= day.available_water_mm + 1e-9
        assert -1e-9 <= day.surface_depletion_mm <= total_evaporable + 1e-9


def test_water_balance_closes_and_second_crop_inherits_the_first() -> None:
    rng = random.Random(7)
    climate_days = make_climate_days(230, lambda _: rng.choice([0.0, 25.0]), 5.0)
    first_sowing = START + timedelta(days=30)
    second_sowing = first_sowing + timedelta(days=CYCLE_DAYS + 5)
    sowings = [
        (make_water_requirements(), first_sowing),
        (make_water_requirements(), second_sowing),
    ]
    trace: list[DailyTrace] = []
    simulate_season(climate_days, START, sowings, SoilType.MEDIUM, 0.2, trace=trace)

    initial_available_water_mm = calculate_available_water_capacity(SoilType.MEDIUM, 0.3)
    previous_depletion = 0.8 * initial_available_water_mm
    for day in trace:
        expected = (
            previous_depletion
            + day.resize_depletion_change_mm
            - day.rainfall_mm
            + day.evapotranspiration_mm
            + day.deep_drainage_mm
        )
        assert day.root_zone_depletion_mm == pytest.approx(expected, abs=1e-9)
        previous_depletion = day.root_zone_depletion_mm
    assert trace[0].day == START
    assert trace[-1].day == second_sowing + timedelta(days=CYCLE_DAYS - 1)
    # No reset at the second sowing: resizing keeps the relative depletion left by the first
    # crop and the fallow gap.
    index = next(i for i, day in enumerate(trace) if day.day == second_sowing)
    day_before, first_day = trace[index - 1], trace[index]
    depletion_after_resize = (
        day_before.root_zone_depletion_mm + first_day.resize_depletion_change_mm
    )
    assert depletion_after_resize / first_day.available_water_mm == pytest.approx(
        day_before.root_zone_depletion_mm / day_before.available_water_mm
    )


def test_missing_days_up_to_five_percent_are_filled() -> None:
    requirements = make_water_requirements()
    sowing_date = START + timedelta(days=10)
    # 4 of the 80 cycle days without reference evapotranspiration (5%)
    climate_days = make_climate_days(100, 10.0, lambda day: None if 20 <= day < 24 else 5.0)
    [result] = simulate_season(
        climate_days, START, [(requirements, sowing_date)], SoilType.MEDIUM, 0.2
    )
    assert result.is_valid
    assert result.season_crop_evapotranspiration_mm == pytest.approx(
        sum(
            daily_crop_parameters(requirements, day)[1].crop_coefficient * 5.0
            for day in range(CYCLE_DAYS)
        )
    )
    # 5 dates absent from the series (6.25%)
    climate_days = [
        climate_day
        for index, climate_day in enumerate(make_climate_days(100, 10.0, 5.0))
        if not 20 <= index < 25
    ]
    [result] = simulate_season(
        climate_days, START, [(requirements, sowing_date)], SoilType.MEDIUM, 0.2
    )
    assert not result.is_valid


def test_invalid_sowings_are_rejected() -> None:
    climate_days = make_climate_days(300, 5.0, 5.0)
    requirements = make_water_requirements()
    with pytest.raises(ValueError):
        simulate_season(
            climate_days, START, [(requirements, START - timedelta(days=1))], SoilType.MEDIUM, 0.2
        )
    first_sowing = START + timedelta(days=10)
    overlapping = [(requirements, first_sowing), (requirements, first_sowing + timedelta(days=50))]
    with pytest.raises(ValueError):
        simulate_season(climate_days, START, overlapping, SoilType.MEDIUM, 0.2)
    with pytest.raises(ValueError):
        simulate_season(climate_days, START, [], SoilType.MEDIUM, 0.2)


def test_split_run_with_carry_over_matches_single_run() -> None:
    rng = random.Random(3)
    climate_days = make_climate_days(
        230, lambda _: rng.choice([0.0, 0.0, 30.0]), lambda _: rng.uniform(3, 6)
    )
    first_sowing = START + timedelta(days=30)
    first_harvest = first_sowing + timedelta(days=CYCLE_DAYS - 1)
    second_sowing = first_harvest + timedelta(days=6)
    first, second = make_water_requirements(), make_water_requirements(season_yield_response=1.25)
    joint = simulate_season(
        climate_days, START, [(first, first_sowing), (second, second_sowing)], SoilType.SANDY, 0.2
    )
    [first_result], carry_over = simulate_phases(
        climate_days, START, [(first, first_sowing)], SoilType.SANDY, 0.2
    )
    [second_result], _ = simulate_phases(
        climate_days,
        first_harvest + timedelta(days=1),
        [(second, second_sowing)],
        SoilType.SANDY,
        carry_over,
    )
    assert first_result == joint[0]
    assert second_result.yield_loss_fraction == pytest.approx(joint[1].yield_loss_fraction)
    assert second_result.actual_evapotranspiration_mm == pytest.approx(
        joint[1].actual_evapotranspiration_mm
    )
