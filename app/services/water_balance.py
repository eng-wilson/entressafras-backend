"""Daily soil water balance, simplified FAO-56 (single reservoir), with no I/O.

References: Allen et al. (1998), FAO-56, ch. 6 (crop coefficient curve), ch. 7 (bare-soil
evaporation, eq. 71–74), ch. 8 (water stress coefficient, eq. 84–85); Doorenbos & Kassam (1979),
FAO-33 (yield response factor).

FAO-56 symbols, for reference: Kc = crop coefficient, p = depletion fraction, Zr = root depth,
TAW/RAW = total/readily available water, Dr = root-zone depletion, De = surface-layer depletion,
Ks = water stress coefficient, Kr = evaporation reduction coefficient, REW/TEW = readily/total
evaporable water, ET0 = reference evapotranspiration, ETc/ETr = crop/actual evapotranspiration,
ISNA = water satisfaction index (ETr/ETc), Ky = yield response factor.

Simplifications (documented as MVP limitations):
- One root-zone reservoir. Bare-soil evaporation is taken from that same reservoir, and the
  surface depletion is tracked only to get the evaporation reduction coefficient.
- When the root depth changes (root growth, or a switch between crop and fallow), the relative
  root-zone depletion is kept. This means the soil that is added or dropped has the same
  moisture as the current root zone. The resulting change is reported as
  `resize_depletion_change_mm` so the balance can be audited.
- No runoff, no capillary rise, no irrigation.

TODO(validate): single reservoir, no runoff and no capillary rise; a two-layer model with
curve-number runoff is follow-up 6. Simulated depletion is not yet checked against
GWETROOT/SMAP (follow-up 7).
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.schemas.crop import WaterRequirements
from app.schemas.rotation import Stage
from app.services.agro import DiaClima
from app.services.soil import (
    MAX_EVAPORATION_COEFFICIENT,
    SoilProfile,
    SoilType,
    as_soil_profile,
)

STAGES: tuple[Stage, ...] = ("initial", "development", "mid_season", "late_season")
MAX_MISSING_DAYS_FRACTION = 0.05
FALLOW = "fallow"


# --- Daily crop parameters ------------------------------------------------------------------


@dataclass(frozen=True)
class DailyCropParameters:
    crop_coefficient: float  # Kc
    depletion_fraction: float  # p
    root_depth_m: float  # Zr


def _stage_curve(
    start_value: float, mid_value: float, end_value: float, stage: Stage, stage_progress: float
) -> float:
    """Kc-shaped curve: flat, linear rise, flat, linear fall. `stage_progress` ∈ (0, 1]."""
    if stage == "initial":
        return start_value
    if stage == "development":
        return start_value + (mid_value - start_value) * stage_progress
    if stage == "mid_season":
        return mid_value
    return mid_value + (end_value - mid_value) * stage_progress


def daily_crop_parameters(
    water_requirements: WaterRequirements, day_index: int
) -> tuple[Stage, DailyCropParameters]:
    """Stage and crop parameters on `day_index` (0 = sowing day). Slopes follow FAO-56 eq. 66."""
    lengths = water_requirements.stage_length_days
    stage_lengths = (lengths.initial, lengths.development, lengths.mid_season, lengths.late_season)
    cycle_days = sum(stage_lengths)
    if not 0 <= day_index < cycle_days:
        raise ValueError(f"day_index {day_index} outside the {cycle_days}-day cycle")
    stage_index, day_in_stage = 0, day_index
    while day_in_stage >= stage_lengths[stage_index]:
        day_in_stage -= stage_lengths[stage_index]
        stage_index += 1
    stage = STAGES[stage_index]
    stage_progress = (day_in_stage + 1) / stage_lengths[stage_index]

    coefficient = water_requirements.crop_coefficient
    depletion = water_requirements.allowable_depletion
    roots = water_requirements.root_depth_m
    root_growth_days = lengths.initial + lengths.development
    root_depth_m = roots.initial + (roots.max - roots.initial) * min(
        1.0, day_index / root_growth_days
    )
    return stage, DailyCropParameters(
        crop_coefficient=_stage_curve(
            coefficient.initial,
            coefficient.mid_season,
            coefficient.late_season,
            stage,
            stage_progress,
        ),
        depletion_fraction=_stage_curve(
            depletion.initial, depletion.mid_season, depletion.late_season, stage, stage_progress
        ),
        root_depth_m=root_depth_m,
    )


# --- Single-day steps -----------------------------------------------------------------------


@dataclass(frozen=True)
class SoilWaterState:
    root_zone_depletion_mm: float  # Dr
    surface_depletion_mm: float  # De, evaporation layer Ze = 0.10 m


@dataclass(frozen=True)
class DailyStepResult:
    state: SoilWaterState
    evapotranspiration_mm: float  # actual crop ET (ETr), or soil evaporation on fallow days
    deep_drainage_mm: float


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def evaporation_reduction_coefficient(
    surface_depletion_mm: float, soil: SoilProfile | SoilType
) -> float:
    """Kr, FAO-56 eq. 74: 1 while the surface is wet, then falls linearly to 0."""
    limits = as_soil_profile(soil).surface_evaporation
    readily, total = limits.readily_evaporable_water_mm, limits.total_evaporable_water_mm
    if surface_depletion_mm <= readily:
        return 1.0
    return _clamp((total - surface_depletion_mm) / (total - readily), 0.0, 1.0)


def _apply_water_budget(
    state: SoilWaterState,
    rainfall_mm: float,
    evapotranspiration_mm: float,
    available_water_mm: float,
    soil: SoilProfile,
) -> DailyStepResult:
    """Water budget for one day; evapotranspiration must already be capped at available water."""
    root_zone_depletion = state.root_zone_depletion_mm - rainfall_mm + evapotranspiration_mm
    deep_drainage = max(0.0, -root_zone_depletion)
    total_evaporable = soil.surface_evaporation.total_evaporable_water_mm
    surface_depletion = _clamp(
        state.surface_depletion_mm - rainfall_mm + evapotranspiration_mm, 0.0, total_evaporable
    )
    new_state = SoilWaterState(
        root_zone_depletion_mm=_clamp(root_zone_depletion, 0.0, available_water_mm),
        surface_depletion_mm=surface_depletion,
    )
    return DailyStepResult(new_state, evapotranspiration_mm, deep_drainage)


def _water_left_mm(state: SoilWaterState, rainfall_mm: float, available_water_mm: float) -> float:
    return max(0.0, available_water_mm - state.root_zone_depletion_mm + rainfall_mm)


def bare_soil_step(
    state: SoilWaterState,
    rainfall_mm: float,
    reference_evapotranspiration_mm: float,
    soil: SoilProfile | SoilType,
    root_depth_m: float,
) -> DailyStepResult:
    """Fallow day: two-stage evaporation E = Kr · Ke max · ET0 (FAO-56 ch. 7)."""
    soil = as_soil_profile(soil)
    available_water_mm = soil.available_water_mm(root_depth_m)
    evaporation_mm = (
        evaporation_reduction_coefficient(state.surface_depletion_mm, soil)
        * MAX_EVAPORATION_COEFFICIENT
        * reference_evapotranspiration_mm
    )
    evaporation_mm = _clamp(
        evaporation_mm, 0.0, _water_left_mm(state, rainfall_mm, available_water_mm)
    )
    return _apply_water_budget(state, rainfall_mm, evaporation_mm, available_water_mm, soil)


def crop_step(
    state: SoilWaterState,
    rainfall_mm: float,
    reference_evapotranspiration_mm: float,
    parameters: DailyCropParameters,
    soil: SoilProfile | SoilType,
) -> DailyStepResult:
    """Crop day: ETr = Ks · Kc · ET0, with Ks from the previous day's depletion (eq. 84–85)."""
    soil = as_soil_profile(soil)
    available_water_mm = soil.available_water_mm(parameters.root_depth_m)
    readily_available_water_mm = parameters.depletion_fraction * available_water_mm
    if available_water_mm <= 0:
        water_stress_coefficient = 0.0
    elif state.root_zone_depletion_mm <= readily_available_water_mm:
        water_stress_coefficient = 1.0
    else:
        water_stress_coefficient = _clamp(
            (available_water_mm - state.root_zone_depletion_mm)
            / ((1 - parameters.depletion_fraction) * available_water_mm),
            0.0,
            1.0,
        )
    actual_evapotranspiration_mm = _clamp(
        water_stress_coefficient * parameters.crop_coefficient * reference_evapotranspiration_mm,
        0.0,
        _water_left_mm(state, rainfall_mm, available_water_mm),
    )
    return _apply_water_budget(
        state, rainfall_mm, actual_evapotranspiration_mm, available_water_mm, soil
    )


def resize_root_zone(
    state: SoilWaterState, previous_available_water_mm: float, new_available_water_mm: float
) -> SoilWaterState:
    """Change the root zone while keeping the relative depletion (depletion / available water)."""
    # TODO(validate): the original plan kept the absolute deficit, `min(Dr, TAW_new)`. Keeping
    # the ratio assumes the added/dropped soil has the root zone's moisture; check which one
    # better matches field data before validating the method.
    if previous_available_water_mm <= 0:
        return SoilWaterState(new_available_water_mm, state.surface_depletion_mm)
    scale = new_available_water_mm / previous_available_water_mm
    return SoilWaterState(state.root_zone_depletion_mm * scale, state.surface_depletion_mm)


# --- Season simulation ----------------------------------------------------------------------


@dataclass(frozen=True)
class DailyTrace:
    day: date
    phase: str  # "fallow" or the CROPWAT profile
    available_water_mm: float
    rainfall_mm: float
    reference_evapotranspiration_mm: float
    evapotranspiration_mm: float
    deep_drainage_mm: float
    resize_depletion_change_mm: float  # change in depletion from resizing, before the step
    root_zone_depletion_mm: float  # end of day
    surface_depletion_mm: float  # end of day


@dataclass(frozen=True)
class CropSeasonResult:
    profile: str
    sowing_date: date
    harvest_date: date
    is_valid: bool
    missing_days: int
    crop_evapotranspiration_mm: dict[Stage, float] = field(default_factory=dict)  # ETc
    actual_evapotranspiration_mm: dict[Stage, float] = field(default_factory=dict)  # ETr
    water_satisfaction_index: dict[Stage, float | None] = field(default_factory=dict)  # ISNA
    season_crop_evapotranspiration_mm: float = 0.0
    season_actual_evapotranspiration_mm: float = 0.0
    season_water_satisfaction_index: float | None = None
    yield_loss_fraction: float | None = None  # 0–1, Ky_season · (1 − ΣETr/ΣETc), FAO-33
    critical_stage: Stage | None = None  # stage with the largest Ky_stage · (1 − ISNA_stage)


@dataclass
class _CropAccumulator:
    crop_evapotranspiration_mm: dict[Stage, float] = field(
        default_factory=lambda: dict.fromkeys(STAGES, 0.0)
    )
    actual_evapotranspiration_mm: dict[Stage, float] = field(
        default_factory=lambda: dict.fromkeys(STAGES, 0.0)
    )
    missing_days: int = 0


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _season_result(
    water_requirements: WaterRequirements, sowing_date: date, totals: _CropAccumulator
) -> CropSeasonResult:
    cycle_days = water_requirements.stage_length_days.total
    crop_evapotranspiration, actual_evapotranspiration = (
        totals.crop_evapotranspiration_mm,
        totals.actual_evapotranspiration_mm,
    )
    water_satisfaction = {
        stage: _ratio(actual_evapotranspiration[stage], crop_evapotranspiration[stage])
        for stage in STAGES
    }
    season_crop_evapotranspiration, season_actual_evapotranspiration = (
        sum(crop_evapotranspiration.values()),
        sum(actual_evapotranspiration.values()),
    )
    season_water_satisfaction = _ratio(
        season_actual_evapotranspiration, season_crop_evapotranspiration
    )

    # TODO(pending): seasonal Ky underestimates losses when the deficit is concentrated in a
    # sensitive stage (e.g. flowering); use multiplicative per-stage Ky, Jensen/Rao (follow-up 1).
    yield_response = water_requirements.yield_response_factor
    yield_loss = None
    if yield_response.season is not None and season_water_satisfaction is not None:
        yield_loss = _clamp(yield_response.season * (1 - season_water_satisfaction), 0.0, 1.0)

    stage_yield_response: dict[Stage, float | None] = {
        "initial": yield_response.initial,
        "development": yield_response.development,
        "mid_season": yield_response.mid_season,
        "late_season": yield_response.late_season,
    }
    deficit_scores = {
        stage: factor * (1 - satisfaction)
        for stage in STAGES
        if (factor := stage_yield_response[stage]) is not None
        and (satisfaction := water_satisfaction[stage]) is not None
    }
    critical_stage = max(deficit_scores, key=deficit_scores.__getitem__, default=None)
    if critical_stage is not None and deficit_scores[critical_stage] <= 1e-9:
        critical_stage = None

    return CropSeasonResult(
        profile=water_requirements.profile,
        sowing_date=sowing_date,
        harvest_date=sowing_date + timedelta(days=cycle_days - 1),
        is_valid=totals.missing_days <= MAX_MISSING_DAYS_FRACTION * cycle_days,
        missing_days=totals.missing_days,
        crop_evapotranspiration_mm=crop_evapotranspiration,
        actual_evapotranspiration_mm=actual_evapotranspiration,
        water_satisfaction_index=water_satisfaction,
        season_crop_evapotranspiration_mm=season_crop_evapotranspiration,
        season_actual_evapotranspiration_mm=season_actual_evapotranspiration,
        season_water_satisfaction_index=season_water_satisfaction,
        yield_loss_fraction=yield_loss,
        critical_stage=critical_stage,
    )


@dataclass(frozen=True)
class SoilCarryOver:
    """Soil water at the end of a simulation, to continue it later without any reset."""

    state: SoilWaterState
    available_water_mm: float  # TAW of the root zone that `state` refers to


def simulate_season(
    climate_days: Sequence[DiaClima],
    simulation_start: date,
    sowings: Sequence[tuple[WaterRequirements, date]],
    soil: SoilProfile | SoilType,
    initial_water_fraction: float,
    trace: list[DailyTrace] | None = None,
) -> list[CropSeasonResult]:
    """Run fallow → crop A → fallow → crop B … from `simulation_start` to the last harvest.

    The soil starts with `initial_water_fraction` of the available water in the first crop's
    initial root zone. Depletion carries over between phases and is never reset. Missing rainfall
    is filled with 0 and missing reference ET with the mean of the valid days in the simulated
    period. A crop with more than 5% of its cycle days missing is flagged `is_valid=False`.
    """
    results, _ = simulate_phases(
        climate_days, simulation_start, sowings, soil, initial_water_fraction, trace
    )
    return results


def simulate_phases(
    climate_days: Sequence[DiaClima],
    simulation_start: date,
    sowings: Sequence[tuple[WaterRequirements, date]],
    soil: SoilProfile | SoilType,
    initial: float | SoilCarryOver,
    trace: list[DailyTrace] | None = None,
) -> tuple[list[CropSeasonResult], SoilCarryOver]:
    """Same as `simulate_season`, but can resume from a previous run and returns its end state.

    `initial` is either the initial water fraction or the `SoilCarryOver` of a run that ended the
    day before `simulation_start`. Splitting a run this way gives the same results as one long run
    whenever no climate data is missing (only the fill value of missing ET0 can differ).
    """
    if not sowings:
        raise ValueError("at least one sowing is required")
    soil = as_soil_profile(soil)
    if not isinstance(initial, SoilCarryOver) and not 0 <= initial <= 1:
        raise ValueError(f"initial_water_fraction must be in [0, 1], got {initial}")
    previous_phase_end = simulation_start - timedelta(days=1)
    for water_requirements, sowing_date in sowings:
        if sowing_date <= previous_phase_end:
            raise ValueError(
                f"sowing {sowing_date} of {water_requirements.profile} overlaps the previous phase"
            )
        cycle_days = water_requirements.stage_length_days.total
        previous_phase_end = sowing_date + timedelta(days=cycle_days - 1)
    simulation_end = previous_phase_end

    climate_by_date = {
        climate_day.data: climate_day
        for climate_day in climate_days
        if simulation_start <= climate_day.data <= simulation_end
    }
    valid_reference_evapotranspiration = [
        climate_day.et0 for climate_day in climate_by_date.values() if climate_day.et0 is not None
    ]
    mean_reference_evapotranspiration = (
        statistics.fmean(valid_reference_evapotranspiration)
        if valid_reference_evapotranspiration
        else 0.0
    )

    if isinstance(initial, SoilCarryOver):
        state, available_water_mm = initial.state, initial.available_water_mm
    else:
        first_crop = sowings[0][0]
        available_water_mm = soil.available_water_mm(first_crop.root_depth_m.initial)
        total_evaporable = soil.surface_evaporation.total_evaporable_water_mm
        initial_depletion_fraction = 1 - initial
        state = SoilWaterState(
            root_zone_depletion_mm=initial_depletion_fraction * available_water_mm,
            surface_depletion_mm=initial_depletion_fraction * total_evaporable,
        )

    totals_by_crop = [_CropAccumulator() for _ in sowings]
    crop_index = 0
    day = simulation_start
    while day <= simulation_end:
        water_requirements, sowing_date = sowings[crop_index]
        climate_day = climate_by_date.get(day)
        rainfall_mm = climate_day.chuva if climate_day else None
        reference_evapotranspiration_mm = climate_day.et0 if climate_day else None
        is_crop_day = day >= sowing_date
        if is_crop_day and (rainfall_mm is None or reference_evapotranspiration_mm is None):
            totals_by_crop[crop_index].missing_days += 1
        rainfall_mm = rainfall_mm if rainfall_mm is not None else 0.0
        reference_evapotranspiration_mm = (
            reference_evapotranspiration_mm
            if reference_evapotranspiration_mm is not None
            else mean_reference_evapotranspiration
        )

        if is_crop_day:
            day_in_cycle = (day - sowing_date).days
            stage, parameters = daily_crop_parameters(water_requirements, day_in_cycle)
            root_depth_m = parameters.root_depth_m
        else:
            root_depth_m = water_requirements.root_depth_m.initial
        new_available_water_mm = soil.available_water_mm(root_depth_m)
        resized_state = resize_root_zone(state, available_water_mm, new_available_water_mm)
        resize_change_mm = resized_state.root_zone_depletion_mm - state.root_zone_depletion_mm
        state, available_water_mm = resized_state, new_available_water_mm

        if is_crop_day:
            step = crop_step(state, rainfall_mm, reference_evapotranspiration_mm, parameters, soil)
            totals = totals_by_crop[crop_index]
            totals.crop_evapotranspiration_mm[stage] += (
                parameters.crop_coefficient * reference_evapotranspiration_mm
            )
            totals.actual_evapotranspiration_mm[stage] += step.evapotranspiration_mm
        else:
            step = bare_soil_step(
                state, rainfall_mm, reference_evapotranspiration_mm, soil, root_depth_m
            )
        state = step.state

        if trace is not None:
            trace.append(
                DailyTrace(
                    day=day,
                    phase=water_requirements.profile if is_crop_day else FALLOW,
                    available_water_mm=available_water_mm,
                    rainfall_mm=rainfall_mm,
                    reference_evapotranspiration_mm=reference_evapotranspiration_mm,
                    evapotranspiration_mm=step.evapotranspiration_mm,
                    deep_drainage_mm=step.deep_drainage_mm,
                    resize_depletion_change_mm=resize_change_mm,
                    root_zone_depletion_mm=state.root_zone_depletion_mm,
                    surface_depletion_mm=state.surface_depletion_mm,
                )
            )
        if is_crop_day and day_in_cycle == water_requirements.stage_length_days.total - 1:
            crop_index += 1
        day += timedelta(days=1)

    results = [
        _season_result(water_requirements, sowing_date, totals)
        for (water_requirements, sowing_date), totals in zip(sowings, totals_by_crop, strict=True)
    ]
    return results, SoilCarryOver(state, available_water_mm)
