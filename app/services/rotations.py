"""Two-crop rotation recommendations from a simplified FAO-56 daily water balance.

For each ordered pair of candidate crops (A then B, different botanical families) and each
planting date of A, both crops are simulated in every historical season (`water_balance`).
Planting dates and cycle lengths follow the regional GGCMI crop calendar (`crop_calendar`): A is
planted around its calendar date, and B (right after the harvest of A) only when that date is
close to its own calendar date. Each
crop gets its own risk (`crop_risk`). Pairs are ranked by the client priorities (`Priorities`):
the mean of the enabled 0–100 indicators (productivity and water security of the worse crop, soil
protection of the rotation); with every priority off, by the worse of the two individual risks.
There is deliberately no combined rotation risk. Climate is the historical record used as a set of
scenarios, not a forecast.
"""

import statistics
import threading
from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal, Protocol

from app.schemas.crop import CropDetail, WaterRequirements
from app.schemas.rotation import (
    CropCalendarInfo,
    CropRisk,
    CropWithRisk,
    CustomRotation,
    CustomRotationInput,
    MeanIsna,
    PlantingSweep,
    Priorities,
    Rotation,
    RotationAssumptions,
    RotationContext,
    RotationCrop,
    RotationRecommendationsResponse,
    RotationRequest,
    RotationScores,
    RotationSimulationRequest,
    RotationSimulationResponse,
    SoilInfo,
    SoilProperties,
    SoilProtection,
    Stage,
    SweepCrop,
    SweepPoint,
)
from app.schemas.rotation import Location as LocationSchema
from app.services.agro import MIN_SAFRAS_VALIDAS, DiaClima, decendio_idx
from app.services.crop_calendar import (
    CALENDAR_SOURCE,
    CropCalendar,
    apply_calendar,
    crop_calendars,
    scale_stage_lengths,
)
from app.services.ecocrop import CropRepository, is_simulable
from app.services.soil import SoilProfile, SoilType, saxton_rawls_2006, texture_class
from app.services.soil_protection import botanical_family, exact_value, soil_protection
from app.services.soilgrids_client import ORGANIC_MATTER_PER_CARBON, SoilEstimate
from app.services.water_balance import STAGES, CropSeasonResult, SoilCarryOver, simulate_phases

# TODO(validate): product choice, not an FAO norm; calibrate per crop and region (follow-up 2).
LOSS_THRESHOLD_PCT = 20.0
# TODO(validate): hypothesis, not measured; see scripts/rotation_sensitivity.py and compare with
# GWETROOT/SMAP (follow-up 7).
INITIAL_SOIL_WATER_FRACTION = 0.2
# Initial soil water fractions compared by scripts/rotation_sensitivity.py. Not run per request:
# each ranking takes about 2 s on 10 seasons.
SENSITIVITY_WATER_FRACTIONS = (0.2, 0.5, 0.8)
# TODO(validate): spin-up, season start and planting window are set for Mato Grosso; detect the
# onset of the rainy season per region instead (follow-up 3).
SPINUP_DAYS = 30
SEASON_START = (9, 1)  # month, day
SEASON_END = (8, 31)
# Planting window of crop A when it has no regional calendar (month, day).
PLANTING_WINDOW_A = ((10, 1), (11, 30))
PLANTING_STEP_DAYS = 10
# Planting dates within this distance of the calendar date are accepted: crop A is tried every
# PLANTING_STEP_DAYS inside ± this window, and crop B only when its date falls inside it.
# TODO(validate): product choice; GGCMI gives one average date per cell, while farmers spread
# planting over 1–2 months (e.g. second-season maize in MT from January to March).
PLANTING_TOLERANCE_DAYS = 30
# Free days between the harvest of A and the planting of B (B is planted on harvest + 6 days).
# TODO(validate): the original plan says "harvest + 5"; confirm whether 5 means the free days
# (current behavior) or the date offset.
PLANTING_GAP_DAYS = 5
MAX_ROTATIONS = 4
MIN_SEASONS_FOR_STABLE_RISK = 15
# Historical seasons simulated per request. ZARC uses 30 years (Portaria SPA/MAPA nº 326/2026,
# item 1.11), and so does the WMO climate normal.
ROTATION_SEASONS = 30
RANKING_CACHE_SIZE = 64

SOYBEAN, MAIZE, SORGHUM, MILLET, COTTON = 1150, 2175, 48747, 8418, 1160
COMMON_BEAN, COWPEA, SUNFLOWER, GROUNDNUT = 1668, 2153, 1191, 2199
# Candidate crops for the second-season rotation (EcoCrop codes with FAO-56 CROPWAT profiles).
# TODO(pending): the only agronomic rule is "different botanical family"; pests, diseases,
# nematodes, straw, nutrients, yield and economics are not considered (follow-up 10).
ROTATION_CROP_CODES: tuple[int, ...] = (
    SOYBEAN,
    MAIZE,
    SORGHUM,
    MILLET,
    COTTON,
    COMMON_BEAN,
    COWPEA,
    SUNFLOWER,
    GROUNDNUT,
)

EXPERIMENTAL_WARNING = (
    "Experimental method (simplified FAO-56 daily water balance with seasonal Ky, FAO-33); "
    "not validated against the official ZARC agricultural zoning."
)
INITIAL_WATER_WARNING = (
    "Soil water at the start of the simulation is assumed to be "
    f"{INITIAL_SOIL_WATER_FRACTION:.0%} of the available water capacity (hypothesis, not "
    "measured)."
)
GRID_WARNING = (
    "Climate comes from a NASA POWER grid cell of about 50 km; local rainfall can differ."
)
# TODO(validate): one average date and length per 0.5° cell, not per cultivar (early, medium
# and late maturity groups differ by weeks); compare with ZARC cycles in Brazil (follow-up 4).
CALENDAR_WARNING = (
    "Planting dates and cycle lengths come from the GGCMI Phase 3 crop calendar (0.5° grid, "
    "regional averages from national statistics or MIRCA2000/SAGE); FAO-56 stage proportions "
    "are stretched to that cycle. Your cultivar's cycle may differ."
)
CYCLE_WARNING = (
    "No regional calendar for {crops}: FAO-56 reference cycle lengths and the default "
    "October–November planting window are used, not calibrated for the region."
)
# TODO(validate): fixed text backed by scripts/rotation_sensitivity.py on the Sorriso seed only
# with GGCMI calendars (top 4 changed with the initial water on the 80/150/160 mm/m classes, where
# top risks are close; stable on the SoilGrids estimate of 104 mm/m). Re-run for other regions.
SENSITIVITY_WARNING = (
    "Rotations with near-zero risk are practically tied: their order changes with the initial "
    "soil water assumption (20%, 50% or 80% of capacity), so treat close ranks as equivalent."
)
SOILGRIDS_WARNING = (
    "Soil estimated from the location: SoilGrids 250 m texture (machine-learning map) and the "
    "Saxton & Rawls (2006) pedotransfer function. Confirm with a soil analysis of the field."
)
SOIL_CLASS_WARNING = (
    "Soil water comes from a generic texture class, not from the field; omit soil_type to "
    "estimate it from the location."
)
SOIL_PROTECTION_WARNING = (
    "soil_protection is a planning index: 70 × share of the season with a planned living crop + "
    "30 × share of crop transitions that change botanical family (heuristic weights). It is not "
    "a measurement of soil health nor a forecast of organic matter."
)
PRIORITIES_WARNING = (
    "Rotations are ranked by the mean of the enabled priorities ({enabled}). Scores are absolute "
    "0–100 scales (0 = worst possible, 100 = best possible) and do not depend on the priorities: "
    "productivity is the mean preserved yield and water_security the share of seasons without a "
    "critical loss, both for the worse of the two crops."
)
NO_PRIORITIES_WARNING = (
    "Every priority is off: rotations are ranked by the worse individual water risk."
)
LONG_CYCLE_WARNING = (
    "The soil_protection priority favors long cycles that keep the soil covered until late in "
    "the season, which usually carry more water risk; combine it with productivity or "
    "water_security."
)
NO_HISTORY_WARNING = (
    "Without crop_history only the A→B transition is counted; recommended rotations always "
    "change family, so the diversity part of soil_protection is always full."
)
SOIL_PROPERTIES_WARNING = (
    "soil_properties are SoilGrids 250 m estimates (0–30 cm, machine-learning maps); they do "
    "not replace a laboratory soil analysis."
)
SOIL_PROPERTIES_UNAVAILABLE_WARNING = "Soil properties panel unavailable: {reason}."
CUSTOM_ROTATION_WARNING = (
    "Custom rotations are simulated as sent, without the recommendation filters (botanical "
    "family, temperature, regional calendar); issues are listed in their checks. Their rank is "
    "the position they would take among the recommended rotations. A one-crop rotation is ranked "
    "by the same indicators: the fallow after it lowers its soil_protection."
)
TEMPERATURE_FILTER_WARNING = (
    "Crops are pre-filtered by EcoCrop absolute temperature limits against the mean daily "
    "minimum/maximum over the cycle (coarse filter)."
)


def next_agricultural_year(today: date) -> int:
    """Start year of the next season to begin (seasons run Sep 1 – Aug 31)."""
    return today.year if today < date(today.year, *SEASON_START) else today.year + 1


def season_of(day: date) -> int:
    """Start year of the season (Sep 1 – Aug 31) that contains `day`."""
    return day.year if day >= date(day.year, *SEASON_START) else day.year - 1


def harvest_date(planting_date: date, crop: CropDetail) -> date:
    """Last day of the crop cycle (planting day counts as day 1)."""
    return planting_date + timedelta(days=_cycle_days(crop) - 1)


def second_planting_date(first_harvest_date: date) -> date:
    """Planting date of crop B, leaving PLANTING_GAP_DAYS free days after the harvest of A."""
    return first_harvest_date + timedelta(days=PLANTING_GAP_DAYS + 1)


def _cycle_days(crop: CropDetail) -> int:
    return _water_requirements(crop).stage_length_days.total


def _water_requirements(crop: CropDetail) -> WaterRequirements:
    if crop.water_requirements is None:
        raise ValueError(f"crop {crop.eco_port_code} has no water requirements")
    return crop.water_requirements


def first_planting_dates(
    agricultural_year: int, calendar: CropCalendar | None = None
) -> list[date]:
    """Planting dates of crop A, every PLANTING_STEP_DAYS.

    Around the calendar date (± PLANTING_TOLERANCE_DAYS, inside the season) when there is one,
    otherwise inside PLANTING_WINDOW_A.
    """
    if calendar is None:
        (first_month, first_day), (last_month, last_day) = PLANTING_WINDOW_A
        planting_date = date(agricultural_year, first_month, first_day)
        last_date = date(agricultural_year, last_month, last_day)
    else:
        reference = calendar.planting_date(agricultural_year)
        tolerance = timedelta(days=PLANTING_TOLERANCE_DAYS)
        planting_date = max(reference - tolerance, date(agricultural_year, *SEASON_START))
        last_date = min(reference + tolerance, date(agricultural_year + 1, *SEASON_END))
    dates = []
    while planting_date <= last_date:
        dates.append(planting_date)
        planting_date += timedelta(days=PLANTING_STEP_DAYS)
    return dates


def fits_calendar(planting_date: date, calendar: CropCalendar | None) -> bool:
    """Planting date close enough to the regional calendar date (always true without one)."""
    return calendar is None or calendar.days_from_planting(planting_date) <= PLANTING_TOLERANCE_DAYS


def calendar_info(
    crop: CropDetail, calendar: CropCalendar | None, agricultural_year: int
) -> CropCalendarInfo:
    if calendar is None:
        return CropCalendarInfo(source="FAO56_REFERENCE", cycle_days=_cycle_days(crop))
    return CropCalendarInfo(
        source=CALENDAR_SOURCE,
        cycle_days=_cycle_days(crop),
        reference_planting_date=calendar.planting_date(agricultural_year),
        data_source=calendar.data_source,
        grid_cell=LocationSchema(lat=calendar.cell_lat, lon=calendar.cell_lon),
    )


# --- Risk per crop --------------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def crop_risk(
    results: Sequence[CropSeasonResult], threshold_pct: float = LOSS_THRESHOLD_PCT
) -> CropRisk:
    """Aggregate one crop's simulated seasons into its individual water risk.

    Only valid seasons with a computable loss count. With fewer than MIN_SAFRAS_VALIDAS of them,
    every indicator is None (unknown, which is not the same as low risk).
    """
    valid = [
        result for result in results if result.is_valid and result.yield_loss_fraction is not None
    ]
    if len(valid) < MIN_SAFRAS_VALIDAS:
        return CropRisk(
            risk_percentage=None,
            estimated_yield_loss_pct=None,
            loss_threshold_pct=threshold_pct,
            critical_stage=None,
            mean_isna=MeanIsna(initial=None, development=None, mid_season=None, late_season=None),
            mean_season_isna=None,
            valid_years=len(valid),
            method="FAO56_SIMPLIFIED_KY",
        )

    losses_pct = [100 * result.yield_loss_fraction for result in valid]
    critical_seasons = sum(loss_pct > threshold_pct for loss_pct in losses_pct)
    mean_isna = {
        stage: _round(
            _mean(
                [
                    satisfaction
                    for result in valid
                    if (satisfaction := result.water_satisfaction_index.get(stage)) is not None
                ]
            ),
            3,
        )
        for stage in STAGES
    }
    return CropRisk(
        risk_percentage=round(100 * critical_seasons / len(valid), 1),
        estimated_yield_loss_pct=round(statistics.fmean(losses_pct), 1),
        loss_threshold_pct=threshold_pct,
        critical_stage=_most_common_stage(valid),
        mean_isna=MeanIsna(**mean_isna),
        mean_season_isna=_round(
            _mean(
                [
                    satisfaction
                    for result in valid
                    if (satisfaction := result.season_water_satisfaction_index) is not None
                ]
            ),
            3,
        ),
        valid_years=len(valid),
        method="FAO56_SIMPLIFIED_KY",
    )


def _most_common_stage(results: Sequence[CropSeasonResult]) -> Stage | None:
    """Mode of the critical stage across seasons; ties go to the earlier stage."""
    counts = Counter(result.critical_stage for result in results if result.critical_stage)
    if not counts:
        return None
    return max(STAGES, key=lambda stage: (counts[stage], -STAGES.index(stage)))


# --- Candidates and ranking -----------------------------------------------------------------


def rank_key(
    risk_a: CropRisk,
    risk_b: CropRisk | None,
    planting_date: date,
    crop_codes: tuple[int, ...],
) -> tuple:
    """Lowest worst individual risk first; unknown risk sorts last. No combined risk is exposed.

    `risk_b` is None for a one-crop custom rotation.
    """
    risks = [risk for risk in (risk_a, risk_b) if risk is not None]
    risk_percentages = [risk.risk_percentage for risk in risks]
    yield_losses = [risk.estimated_yield_loss_pct for risk in risks]
    if None in risk_percentages or None in yield_losses:
        return (1, 0.0, 0.0, 0.0, planting_date, crop_codes)
    return (
        0,
        max(risk_percentages),
        max(yield_losses),
        sum(yield_losses),
        planting_date,
        crop_codes,
    )


@dataclass(frozen=True)
class RankedRotation:
    ranking_key: tuple
    first: RotationCrop
    second: RotationCrop | None  # None only for a one-crop custom rotation

    @property
    def crops(self) -> tuple[RotationCrop, ...]:
        return (self.first,) if self.second is None else (self.first, self.second)

    @property
    def crop_codes(self) -> tuple[int, ...]:
        return tuple(rotation_crop.crop.eco_port_code for rotation_crop in self.crops)


class _Climate:
    """Historical daily climate sorted by date, with fast date-range slicing."""

    def __init__(self, climate_days: Sequence[DiaClima]) -> None:
        self.days = sorted(climate_days, key=lambda climate_day: climate_day.data)
        self.dates = [climate_day.data for climate_day in self.days]

    def between(self, first_day: date, last_day: date) -> list[DiaClima]:
        return self.days[bisect_left(self.dates, first_day) : bisect_right(self.dates, last_day)]


def _shift_to_season(projected_date: date, agricultural_year: int, season_year: int) -> date:
    """Same day and month in another season (Feb 29 becomes Feb 28)."""
    year = projected_date.year - agricultural_year + season_year
    try:
        return projected_date.replace(year=year)
    except ValueError:
        return projected_date.replace(year=year, day=28)


def _mean_temperatures(days: Sequence[DiaClima]) -> tuple[float | None, float | None]:
    minimums = [day.tmin for day in days if day.tmin is not None]
    maximums = [day.tmax for day in days if day.tmax is not None]
    return _mean(minimums), _mean(maximums)


def _temperature_suitable(
    crop: CropDetail,
    planting_date: date,
    climate: _Climate,
    agricultural_year: int,
    season_years: Sequence[int],
) -> bool:
    """Coarse EcoCrop filter: mean daily Tmin/Tmax over the cycle within the absolute limits.

    TODO(validate): the plan compared daily extremes, but in Sorriso the October daily Tmax
    reaches 42 °C, above soybean's 38 °C absolute maximum, which would exclude soybean exactly
    where it is grown. EcoCrop limits are meant for monthly means, so the cycle mean is used.
    """
    cycle_days = []
    for season_year in season_years:
        start = _shift_to_season(planting_date, agricultural_year, season_year)
        cycle_days.extend(climate.between(start, harvest_date(start, crop)))
    mean_minimum, mean_maximum = _mean_temperatures(cycle_days)
    limits = crop.temperature
    if limits.absolute_min is not None and mean_minimum is not None:
        if mean_minimum < limits.absolute_min:
            return False
    if limits.absolute_max is not None and mean_maximum is not None:
        if mean_maximum > limits.absolute_max:
            return False
    return True


@dataclass(frozen=True)
class _FirstCropSeason:
    result: CropSeasonResult
    harvest_date: date
    carry_over: SoilCarryOver  # soil water at the end of the harvest day


def _simulate_first_crop(
    crop: CropDetail,
    planting_date: date,
    climate: _Climate,
    agricultural_year: int,
    season_years: Sequence[int],
    soil: SoilProfile | SoilType,
    initial_water_fraction: float,
) -> list[_FirstCropSeason]:
    """Spin-up fallow + crop A in every season. Independent of crop B, so it is shared."""
    seasons = []
    for season_year in season_years:
        planting = _shift_to_season(planting_date, agricultural_year, season_year)
        harvest = harvest_date(planting, crop)
        simulation_start = max(
            date(season_year, *SEASON_START), planting - timedelta(days=SPINUP_DAYS)
        )
        [result], carry_over = simulate_phases(
            climate.between(simulation_start, harvest),
            simulation_start,
            [(_water_requirements(crop), planting)],
            soil,
            initial_water_fraction,
        )
        seasons.append(_FirstCropSeason(result, harvest, carry_over))
    return seasons


def _simulate_second_crop(
    crop: CropDetail,
    first_seasons: Sequence[_FirstCropSeason],
    climate: _Climate,
    soil: SoilProfile | SoilType,
    days_after_harvest: int = PLANTING_GAP_DAYS + 1,
) -> list[CropSeasonResult]:
    """Fallow gap + crop B, continuing from the soil water left by crop A (no reset).

    B is planted `days_after_harvest` days after the harvest of A (default: the standard gap).
    """
    results = []
    for first in first_seasons:
        simulation_start = first.harvest_date + timedelta(days=1)
        planting = first.harvest_date + timedelta(days=days_after_harvest)
        [result], _ = simulate_phases(
            climate.between(simulation_start, harvest_date(planting, crop)),
            simulation_start,
            [(_water_requirements(crop), planting)],
            soil,
            first.carry_over,
        )
        results.append(result)
    return results


def _rotation_crop(
    crop: CropDetail,
    risk: CropRisk,
    planting_date: date,
    calendar: CropCalendar | None,
    agricultural_year: int,
) -> RotationCrop:
    return RotationCrop(
        crop=CropWithRisk(**crop.model_dump(), risk=risk),
        planting_date=planting_date,
        harvest_date=harvest_date(planting_date, crop),
        calendar=calendar_info(crop, calendar, agricultural_year),
    )


def evaluate_rotation(
    crop_a: CropDetail,
    crop_b: CropDetail,
    first_planting: date,
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    soil: SoilProfile | SoilType,
    initial_water_fraction: float = INITIAL_SOIL_WATER_FRACTION,
    calendars: Mapping[int, CropCalendar] | None = None,
    agricultural_year: int | None = None,
) -> RankedRotation:
    """Risk of one sequence on one planting date, with no family, temperature, season or
    calendar-date filter (calendars only set the cycle lengths)."""
    calendars = calendars or {}
    calendar_a = calendars.get(crop_a.eco_port_code)
    calendar_b = calendars.get(crop_b.eco_port_code)
    crop_a, crop_b = apply_calendar(crop_a, calendar_a), apply_calendar(crop_b, calendar_b)
    climate = _Climate(climate_days)
    if agricultural_year is None:
        agricultural_year = season_of(first_planting)
    first_seasons = _simulate_first_crop(
        crop_a,
        first_planting,
        climate,
        agricultural_year,
        season_years,
        soil,
        initial_water_fraction,
    )
    risk_a = crop_risk([season.result for season in first_seasons])
    risk_b = crop_risk(_simulate_second_crop(crop_b, first_seasons, climate, soil))
    planting_b = second_planting_date(harvest_date(first_planting, crop_a))
    return RankedRotation(
        ranking_key=rank_key(
            risk_a, risk_b, first_planting, (crop_a.eco_port_code, crop_b.eco_port_code)
        ),
        first=_rotation_crop(crop_a, risk_a, first_planting, calendar_a, agricultural_year),
        second=_rotation_crop(crop_b, risk_b, planting_b, calendar_b, agricultural_year),
    )


def rank_rotations(
    crops: Sequence[CropDetail],
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    soil: SoilProfile | SoilType,
    agricultural_year: int,
    initial_water_fraction: float = INITIAL_SOIL_WATER_FRACTION,
    calendars: Mapping[int, CropCalendar] | None = None,
) -> list[RankedRotation]:
    """Best planting date of every viable ordered pair (A → B), sorted from lowest risk.

    Ranking by water risk only (every priority off); see `rotation_candidates`.
    """
    candidates = rotation_candidates(
        crops,
        climate_days,
        season_years,
        soil,
        agricultural_year,
        initial_water_fraction,
        calendars,
    )
    return _best_per_pair(sorted(candidates, key=lambda ranked: ranked.ranking_key))


class _HasCropCodes(Protocol):
    @property
    def crop_codes(self) -> tuple[int, ...]: ...


def _best_per_pair[T: _HasCropCodes](ordered: Iterable[T]) -> list[T]:
    """First item of each crop pair, keeping the order."""
    seen: set[tuple[int, ...]] = set()
    best = []
    for item in ordered:
        if (pair := item.crop_codes) not in seen:
            seen.add(pair)
            best.append(item)
    return best


def rotation_candidates(
    crops: Sequence[CropDetail],
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    soil: SoilProfile | SoilType,
    agricultural_year: int,
    initial_water_fraction: float = INITIAL_SOIL_WATER_FRACTION,
    calendars: Mapping[int, CropCalendar] | None = None,
) -> list[RankedRotation]:
    """Every viable ordered pair (A → B) on every viable planting date of A, unsorted.

    Dates are in `agricultural_year`; each one is simulated on the same day and month of every
    historical season in `season_years` (start years of Sep–Aug seasons). `calendars` (by
    EcoCrop code) set the cycle length and planting dates of each crop; crops without one use
    the FAO-56 cycle and PLANTING_WINDOW_A.
    """
    calendars = calendars or {}
    climate = _Climate(climate_days)
    season_end = date(agricultural_year + 1, *SEASON_END)
    candidates = [
        apply_calendar(crop, calendars.get(crop.eco_port_code))
        for crop in crops
        if crop.family and is_simulable(crop)
    ]
    suitable: dict[tuple[int, date], bool] = {}

    def is_suitable(crop: CropDetail, planting_date: date) -> bool:
        key = (crop.eco_port_code, planting_date)
        if key not in suitable:
            suitable[key] = _temperature_suitable(
                crop, planting_date, climate, agricultural_year, season_years
            )
        return suitable[key]

    rotations: list[RankedRotation] = []
    for crop_a in candidates:
        calendar_a = calendars.get(crop_a.eco_port_code)
        for first_planting in first_planting_dates(agricultural_year, calendar_a):
            if not is_suitable(crop_a, first_planting):
                continue
            planting_b = second_planting_date(harvest_date(first_planting, crop_a))
            first_seasons: list[_FirstCropSeason] | None = None
            risk_a: CropRisk | None = None
            for crop_b in candidates:
                crop_codes = (crop_a.eco_port_code, crop_b.eco_port_code)
                calendar_b = calendars.get(crop_b.eco_port_code)
                if crop_a.family == crop_b.family or harvest_date(planting_b, crop_b) > season_end:
                    continue
                if not fits_calendar(planting_b, calendar_b):
                    continue
                if not is_suitable(crop_b, planting_b):
                    continue
                if first_seasons is None or risk_a is None:
                    first_seasons = _simulate_first_crop(
                        crop_a,
                        first_planting,
                        climate,
                        agricultural_year,
                        season_years,
                        soil,
                        initial_water_fraction,
                    )
                    risk_a = crop_risk([season.result for season in first_seasons])
                risk_b = crop_risk(_simulate_second_crop(crop_b, first_seasons, climate, soil))
                rotations.append(
                    RankedRotation(
                        ranking_key=rank_key(risk_a, risk_b, first_planting, crop_codes),
                        first=_rotation_crop(
                            crop_a, risk_a, first_planting, calendar_a, agricultural_year
                        ),
                        second=_rotation_crop(
                            crop_b, risk_b, planting_b, calendar_b, agricultural_year
                        ),
                    )
                )
    return rotations


# --- Priorities -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredRotation:
    ranked: RankedRotation
    soil_protection: SoilProtection
    scores: RotationScores
    indicators: Mapping[str, float | None]  # unrounded scores, by priority name

    @property
    def crop_codes(self) -> tuple[int, ...]:
        return self.ranked.crop_codes


def _worst(values: Sequence[float | None]) -> float | None:
    return None if None in values else min(values)  # type: ignore[type-var]


def _mean_or_none(values: Sequence[float | None]) -> float | None:
    return None if not values or None in values else statistics.fmean(values)  # type: ignore[arg-type]


def rotation_indicators(
    ranked: RankedRotation, protection: SoilProtection
) -> dict[str, float | None]:
    """Unrounded absolute 0–100 indicators (worse crop for productivity and water security)."""
    risks = [rotation_crop.crop.risk for rotation_crop in ranked.crops]
    return {
        "productivity": _worst(
            [
                None
                if risk.estimated_yield_loss_pct is None
                else 100 - risk.estimated_yield_loss_pct
                for risk in risks
            ]
        ),
        "water_security": _worst(
            [None if risk.risk_percentage is None else 100 - risk.risk_percentage for risk in risks]
        ),
        "soil_protection": exact_value(protection),
    }


def rotation_scores(indicators: Mapping[str, float | None]) -> RotationScores:
    """Displayed scores: the same whatever priorities are enabled (they only set the order)."""
    return RotationScores(
        productivity=_round(indicators["productivity"], 1),
        water_security=_round(indicators["water_security"], 1),
        soil_protection=round(indicators["soil_protection"] or 0.0, 1),
        overall=_round(_mean_or_none(list(indicators.values())), 1),
    )


def priority_value(indicators: Mapping[str, float | None], priorities: Priorities) -> float | None:
    """Mean of the enabled indicators: sorts the rotations, not returned to the client."""
    return _mean_or_none([indicators[name] for name in priorities.enabled()])


def _priority_key(scored: ScoredRotation, priorities: Priorities) -> tuple:
    """Highest enabled mean first, unknown last, then water risk; only risk with none enabled."""
    if not priorities.enabled():
        return (scored.ranked.ranking_key,)
    value = priority_value(scored.indicators, priorities)
    return (value is None, -(value or 0.0), scored.ranked.ranking_key)


def select_rotations(
    candidates: Sequence[RankedRotation],
    priorities: Priorities,
    history_families: Sequence[str],
    agricultural_year: int,
) -> list[ScoredRotation]:
    """Best planting date of every crop pair by the priorities, sorted from best."""
    scored = [score_rotation(ranked, history_families, agricultural_year) for ranked in candidates]
    scored.sort(key=lambda item: _priority_key(item, priorities))
    return _best_per_pair(scored)


def score_rotation(
    ranked: RankedRotation, history_families: Sequence[str], agricultural_year: int
) -> ScoredRotation:
    """Soil protection, indicators and displayed scores of one simulated rotation."""
    protection = rotation_soil_protection(
        ranked,
        history_families,
        date(agricultural_year, *SEASON_START),
        date(agricultural_year + 1, *SEASON_END),
    )
    indicators = rotation_indicators(ranked, protection)
    return ScoredRotation(ranked, protection, rotation_scores(indicators), indicators)


def rotation_soil_protection(
    ranked: RankedRotation,
    history_families: Sequence[str],
    season_start: date,
    season_end: date,
) -> SoilProtection:
    planned = [
        (rotation_crop.planting_date, rotation_crop.harvest_date, rotation_crop.crop.family or "")
        for rotation_crop in ranked.crops
    ]
    return soil_protection(planned, history_families, season_start, season_end)


def _rotation(rank: int, scored: ScoredRotation) -> Rotation:
    ranked = scored.ranked
    first_code, second_code = ranked.crop_codes
    return Rotation(
        id=f"{first_code}-{second_code}-{ranked.first.planting_date:%Y-%m-%d}",
        rank=rank,
        crops=list(ranked.crops),
        soil_protection=scored.soil_protection,
        scores=scored.scores,
    )


def history_families(repository: CropRepository, crop_history: Sequence[int]) -> list[str]:
    """EcoCrop families of the previous crops; ValueError for unknown codes or no family."""
    families = []
    for code in crop_history:
        crop = repository.get(code)
        if crop is None or not crop.family:
            raise ValueError(f"crop_history: unknown crop or crop without family: {code}")
        families.append(crop.family)
    return families


def top_sequences(ranked: Sequence[RankedRotation]) -> list[tuple[tuple[int, ...], date]]:
    """Crop pairs and planting dates of the recommended rotations, to compare rankings."""
    return [
        (rotation.crop_codes, rotation.first.planting_date) for rotation in ranked[:MAX_ROTATIONS]
    ]


class _RankingCache:
    """Small thread-safe LRU of simulated candidates (requests run in a thread pool)."""

    def __init__(self, max_size: int) -> None:
        self._items: OrderedDict[Hashable, list[RankedRotation]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: Hashable | None) -> list[RankedRotation] | None:
        if key is None:
            return None
        with self._lock:
            ranked = self._items.get(key)
            if ranked is not None:
                self._items.move_to_end(key)
            return ranked

    def put(self, key: Hashable | None, ranked: list[RankedRotation]) -> None:
        if key is None:
            return
        with self._lock:
            self._items[key] = ranked
            self._items.move_to_end(key)
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_ranking_cache = _RankingCache(RANKING_CACHE_SIZE)


def soil_info_from_texture_class(soil_type: SoilType) -> SoilInfo:
    profile = SoilProfile.from_texture_class(soil_type)
    return SoilInfo(
        source="REQUEST_TEXTURE_CLASS",
        texture_class=profile.texture_class,
        available_water_mm_per_m=profile.available_water_mm_per_m,
    )


def soil_info_from_estimate(estimate: SoilEstimate) -> SoilInfo:
    texture = estimate.root_zone_texture
    limits = saxton_rawls_2006(texture)
    profile = estimate.profile
    return SoilInfo(
        source="SOILGRIDS_SAXTON_RAWLS_2006",
        texture_class=profile.texture_class,
        available_water_mm_per_m=round(profile.available_water_mm_per_m, 1),
        sand_pct=round(texture.sand_pct, 1),
        silt_pct=round(texture.silt_pct, 1),
        clay_pct=round(texture.clay_pct, 1),
        organic_matter_pct=round(texture.organic_matter_pct, 2),
        field_capacity=round(limits.field_capacity, 3),
        wilting_point=round(limits.wilting_point, 3),
        sampled_location=LocationSchema(lat=estimate.sampled_lat, lon=estimate.sampled_lon),
        distance_km=estimate.distance_km,
    )


def soil_properties_from_estimate(estimate: SoilEstimate) -> SoilProperties:
    texture = estimate.topsoil_texture
    return SoilProperties(
        organic_carbon_g_per_kg=round(
            10 * texture.organic_matter_pct / ORGANIC_MATTER_PER_CARBON, 1
        ),
        sand_pct=round(texture.sand_pct, 1),
        silt_pct=round(texture.silt_pct, 1),
        clay_pct=round(texture.clay_pct, 1),
        texture_class=texture_class(texture),
        sampled_location=LocationSchema(lat=estimate.sampled_lat, lon=estimate.sampled_lon),
        distance_km=estimate.distance_km,
    )


@dataclass(frozen=True)
class RankingContext:
    """Everything one request ranks against: soil, calendars, climate and the scored candidates."""

    year: int
    soil: SoilInfo
    profile: SoilProfile
    crops: list[CropDetail]
    calendars: dict[int, CropCalendar]
    climate: _Climate
    season_years: Sequence[int]
    previous_families: list[str]
    ranked: list[ScoredRotation]  # best planting date of each crop pair, best first


def ranking_context(
    repository: CropRepository,
    request: RotationContext,
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    today: date,
    climate_key: Hashable | None = None,
    soil: SoilInfo | None = None,
) -> RankingContext:
    """Simulate (or reuse from the cache) every candidate and rank them by the priorities.

    `climate_key` identifies the climate series (e.g. the POWER cell). When given, the simulated
    candidates are cached: they depend only on the climate, soil, crop calendars, agricultural
    year and seasons (a few seconds to compute on 30 seasons). Priorities and crop_history only
    re-score them.
    """
    year = request.agricultural_year or next_agricultural_year(today)
    previous_families = history_families(repository, request.crop_history)
    if soil is None:
        if request.soil_type is None:
            raise ValueError("soil is required when the request has no soil_type")
        soil = soil_info_from_texture_class(request.soil_type)
    profile = SoilProfile(soil.texture_class, soil.available_water_mm_per_m)
    crops = [crop for code in ROTATION_CROP_CODES if (crop := repository.get(code)) is not None]
    calendars = crop_calendars(request.location.lat, request.location.lon)
    cache_key = (
        None
        if climate_key is None
        else (climate_key, profile, tuple(sorted(calendars.items())), year, tuple(season_years))
    )
    candidates = _ranking_cache.get(cache_key)
    if candidates is None:
        candidates = rotation_candidates(
            crops, climate_days, season_years, profile, year, calendars=calendars
        )
        _ranking_cache.put(cache_key, candidates)
    # Priorities and crop_history only re-score the cached candidates (no new simulation).
    ranked = select_rotations(candidates, request.priorities, previous_families, year)
    return RankingContext(
        year=year,
        soil=soil,
        profile=profile,
        crops=crops,
        calendars=calendars,
        climate=_Climate(climate_days),
        season_years=season_years,
        previous_families=previous_families,
        ranked=ranked,
    )


def _warnings(
    context: RankingContext,
    request: RotationContext,
    soil_properties: SoilProperties | None = None,
    soil_properties_error: str | None = None,
    custom: bool = False,
) -> list[str]:
    soil, priorities = context.soil, request.priorities
    without_calendar = [
        crop.water_requirements.profile_name
        for crop in context.crops
        if crop.eco_port_code not in context.calendars and crop.water_requirements is not None
    ]
    warnings = [
        EXPERIMENTAL_WARNING,
        INITIAL_WATER_WARNING,
        GRID_WARNING,
        CALENDAR_WARNING,
        TEMPERATURE_FILTER_WARNING,
        SENSITIVITY_WARNING,
        SOILGRIDS_WARNING if soil.source == "SOILGRIDS_SAXTON_RAWLS_2006" else SOIL_CLASS_WARNING,
        SOIL_PROTECTION_WARNING,
    ]
    enabled = priorities.enabled()
    if enabled:
        warnings.append(PRIORITIES_WARNING.format(enabled=", ".join(enabled)))
    else:
        warnings.append(NO_PRIORITIES_WARNING)
    if priorities.soil_protection:
        warnings.append(LONG_CYCLE_WARNING)
    if not request.crop_history:
        warnings.append(NO_HISTORY_WARNING)
    if soil_properties is not None:
        warnings.append(SOIL_PROPERTIES_WARNING)
    elif soil_properties_error:
        warnings.append(SOIL_PROPERTIES_UNAVAILABLE_WARNING.format(reason=soil_properties_error))
    if without_calendar:
        warnings.append(CYCLE_WARNING.format(crops=", ".join(without_calendar)))
    seasons = len(context.season_years)
    if seasons < MIN_SEASONS_FOR_STABLE_RISK:
        warnings.append(
            f"Risk is based on only {seasons} historical seasons; each season moves "
            f"risk_percentage by about {100 / max(1, seasons):.0f} points."
        )
    if custom:
        warnings.append(CUSTOM_ROTATION_WARNING)
    return warnings


def _assumptions() -> RotationAssumptions:
    return RotationAssumptions(
        loss_threshold_pct=LOSS_THRESHOLD_PCT,
        initial_soil_water_fraction=INITIAL_SOIL_WATER_FRACTION,
        spinup_days=SPINUP_DAYS,
        planting_gap_days=PLANTING_GAP_DAYS,
        planting_tolerance_days=PLANTING_TOLERANCE_DAYS,
    )


def recommendations(
    repository: CropRepository,
    request: RotationRequest,
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    today: date,
    climate_key: Hashable | None = None,
    soil: SoilInfo | None = None,
    soil_properties: SoilProperties | None = None,
    soil_properties_error: str | None = None,
) -> RotationRecommendationsResponse:
    """Top rotations for the request, ranked by its priorities, with assumptions and warnings.

    The request custom_rotations are simulated and placed in the same ranking (see
    `ranking_context` for the cache). CustomRotationError for an invalid custom rotation.
    """
    context = ranking_context(
        repository, request, climate_days, season_years, today, climate_key, soil
    )
    custom = [
        custom_rotation(
            context, resolve_custom_rotation(repository, item, context), request.priorities
        )
        for item in request.custom_rotations
    ]
    warnings = _warnings(
        context, request, soil_properties, soil_properties_error, custom=bool(custom)
    )
    if not context.ranked:
        warnings.append("No viable rotation was found for this location and season.")

    return RotationRecommendationsResponse(
        agricultural_year=context.year,
        soil_type=context.soil.texture_class,
        soil=context.soil,
        soil_properties=soil_properties,
        priorities=request.priorities,
        rotations=[
            _rotation(rank, rotation)
            for rank, rotation in enumerate(context.ranked[:MAX_ROTATIONS], start=1)
        ],
        custom_rotations=custom,
        assumptions=_assumptions(),
        warnings=warnings,
    )


# --- Custom rotations -----------------------------------------------------------------------

# Requested cycles are accepted within this share of the reference (regional or FAO-56) cycle:
# beyond it, stretching the FAO-56 stage proportions no longer describes a real cultivar.
CUSTOM_CYCLE_RANGE = (0.5, 1.5)
# Default planting date of the first crop when it has no regional calendar (month, day).
DEFAULT_FIRST_PLANTING = PLANTING_WINDOW_A[0]
SEASON_MONTHS = ("Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug")


class CustomRotationError(ValueError):
    """A custom rotation that cannot be simulated (unknown crop, impossible dates)."""


@dataclass(frozen=True)
class CustomPlan:
    """A custom rotation with every date resolved and the requested cycles applied."""

    crop_a: CropDetail
    planting_a: date
    calendar_a: CropCalendar | None
    # The second crop is optional: a one-crop rotation leaves the rest of the season fallow.
    crop_b: CropDetail | None = None
    planting_b: date | None = None
    calendar_b: CropCalendar | None = None

    @property
    def harvest_a(self) -> date:
        return harvest_date(self.planting_a, self.crop_a)

    @property
    def last_harvest(self) -> date:
        if self.crop_b is None or self.planting_b is None:
            return self.harvest_a
        return harvest_date(self.planting_b, self.crop_b)

    @property
    def days_after_harvest(self) -> int:
        """Days from the harvest of A to the planting of B."""
        if self.planting_b is None:
            raise ValueError("one-crop rotation has no second planting")
        return (self.planting_b - self.harvest_a).days

    @property
    def crop_codes(self) -> tuple[int, ...]:
        crops = (self.crop_a,) if self.crop_b is None else (self.crop_a, self.crop_b)
        return tuple(crop.eco_port_code for crop in crops)

    def planted_crops(self) -> list[tuple[CropDetail, date, CropCalendar | None]]:
        planted = [(self.crop_a, self.planting_a, self.calendar_a)]
        if self.crop_b is not None and self.planting_b is not None:
            planted.append((self.crop_b, self.planting_b, self.calendar_b))
        return planted

    def with_second_planting(self, planting_b: date) -> "CustomPlan":
        return replace(self, planting_b=planting_b)


def with_cycle(crop: CropDetail, cycle_days: int) -> CropDetail:
    """Copy of the crop with its FAO-56 stages stretched to `cycle_days`."""
    requirements = _water_requirements(crop)
    if requirements.stage_length_days.total == cycle_days:
        return crop
    stages = scale_stage_lengths(requirements.stage_length_days, cycle_days)
    return crop.model_copy(
        update={"water_requirements": requirements.model_copy(update={"stage_length_days": stages})}
    )


def _custom_crop(
    repository: CropRepository,
    code: int,
    planting: date,
    harvest: date | None,
    calendar: CropCalendar | None,
) -> CropDetail:
    crop = repository.get(code)
    if crop is None:
        raise CustomRotationError(f"custom rotation: unknown crop {code}")
    if not is_simulable(crop):
        raise CustomRotationError(
            f"custom rotation: crop {code} ({_crop_name(crop)}) has no FAO-56 water profile and "
            "cannot be simulated; list simulable crops with GET /crops?simulable=true"
        )
    crop = apply_calendar(crop, calendar)
    if harvest is None:
        return crop
    if harvest <= planting:
        raise CustomRotationError(
            f"custom rotation: crop {code} harvest_date must be after its planting_date"
        )
    cycle = (harvest - planting).days + 1
    reference = _cycle_days(crop)
    low, high = CUSTOM_CYCLE_RANGE
    if not low * reference <= cycle <= high * reference:
        raise CustomRotationError(
            f"custom rotation: crop {code} cycle of {cycle} days is outside "
            f"{low:.0%}–{high:.0%} of its reference cycle ({reference} days)"
        )
    return with_cycle(crop, cycle)


def resolve_custom_rotation(
    repository: CropRepository, rotation: CustomRotationInput, context: RankingContext
) -> CustomPlan:
    """Dates and cycles of a custom rotation; CustomRotationError when it cannot be simulated."""
    year = context.year
    season_start = date(year, *SEASON_START)
    season_end = date(year + 1, *SEASON_END)
    first, *rest = rotation.crops
    calendar_a = context.calendars.get(first.code)

    planting_a = first.planting_date or (
        calendar_a.planting_date(year) if calendar_a else date(year, *DEFAULT_FIRST_PLANTING)
    )
    if not season_start <= planting_a <= season_end:
        raise CustomRotationError(
            f"custom rotation: the first crop must be planted inside the agricultural year "
            f"({season_start} to {season_end})"
        )
    crop_a = _custom_crop(repository, first.code, planting_a, first.harvest_date, calendar_a)
    if not rest:
        return CustomPlan(crop_a, planting_a, calendar_a)
    harvest_a = harvest_date(planting_a, crop_a)

    [second] = rest
    calendar_b = context.calendars.get(second.code)
    planting_b = second.planting_date or second_planting_date(harvest_a)
    if planting_b <= harvest_a:
        raise CustomRotationError(
            f"custom rotation: the second crop must be planted after the harvest of the first "
            f"({harvest_a})"
        )
    if planting_b > season_end:
        raise CustomRotationError(
            f"custom rotation: the second crop must be planted by the end of the agricultural "
            f"year ({season_end})"
        )
    crop_b = _custom_crop(repository, second.code, planting_b, second.harvest_date, calendar_b)
    return CustomPlan(crop_a, planting_a, calendar_a, crop_b, planting_b, calendar_b)


def _crop_name(crop: CropDetail) -> str:
    return crop.common_names[0] if crop.common_names else crop.scientific_name


def _temperature_ok(context: RankingContext, crop: CropDetail, planting: date) -> bool:
    return _temperature_suitable(
        crop, planting, context.climate, context.year, context.season_years
    )


def custom_checks(context: RankingContext, plan: CustomPlan) -> list[str]:
    """Recommendation filters the custom rotation fails, as messages for the farmer."""
    checks = []
    family_a = plan.crop_a.family
    if plan.crop_b is not None and family_a and family_a == plan.crop_b.family:
        checks.append(
            f"Both crops are {botanical_family(family_a)}: no botanical family change, which "
            "favors carry-over of pests, diseases and nematodes."
        )
    for crop, planting, calendar in plan.planted_crops():
        name = _crop_name(crop)
        if not fits_calendar(planting, calendar) and calendar is not None:
            checks.append(
                f"{name}: planting on {planting} is {calendar.days_from_planting(planting)} days "
                f"from the regional calendar date ({calendar.planting_date(context.year)}); "
                f"recommendations accept up to {PLANTING_TOLERANCE_DAYS}."
            )
        if not _temperature_ok(context, crop, planting):
            checks.append(
                f"{name}: mean temperature over the cycle is outside its EcoCrop absolute limits."
            )
    season_end = date(context.year + 1, *SEASON_END)
    if plan.last_harvest > season_end:
        last_crop = plan.planted_crops()[-1][0]
        checks.append(
            f"{_crop_name(last_crop)} is harvested on {plan.last_harvest}, after the end of the "
            f"agricultural year ({season_end})."
        )
    return checks


def evaluate_plan(
    context: RankingContext,
    plan: CustomPlan,
    first_seasons: Sequence[_FirstCropSeason] | None = None,
) -> RankedRotation:
    """Water risk of each crop of a custom rotation (`first_seasons` reuses crop A's run)."""
    if first_seasons is None:
        first_seasons = _simulate_first_crop(
            plan.crop_a,
            plan.planting_a,
            context.climate,
            context.year,
            context.season_years,
            context.profile,
            INITIAL_SOIL_WATER_FRACTION,
        )
    risk_a = crop_risk([season.result for season in first_seasons])
    first = _rotation_crop(plan.crop_a, risk_a, plan.planting_a, plan.calendar_a, context.year)
    if plan.crop_b is None or plan.planting_b is None:
        return RankedRotation(
            ranking_key=rank_key(risk_a, None, plan.planting_a, plan.crop_codes),
            first=first,
            second=None,
        )
    risk_b = crop_risk(
        _simulate_second_crop(
            plan.crop_b, first_seasons, context.climate, context.profile, plan.days_after_harvest
        )
    )
    return RankedRotation(
        ranking_key=rank_key(risk_a, risk_b, plan.planting_a, plan.crop_codes),
        first=first,
        second=_rotation_crop(plan.crop_b, risk_b, plan.planting_b, plan.calendar_b, context.year),
    )


def rank_position(
    scored: ScoredRotation, ranked: Sequence[ScoredRotation], priorities: Priorities
) -> int:
    """Position the rotation would take among `ranked` (1 = ahead of all of them)."""
    key = _priority_key(scored, priorities)
    return 1 + sum(_priority_key(other, priorities) < key for other in ranked)


def custom_rotation(
    context: RankingContext, plan: CustomPlan, priorities: Priorities
) -> CustomRotation:
    """Simulate a custom rotation and place it among the recommended ones."""
    scored = score_rotation(evaluate_plan(context, plan), context.previous_families, context.year)
    rank = rank_position(scored, context.ranked, priorities)
    codes = "-".join(str(code) for code in plan.crop_codes)
    return CustomRotation(
        id=f"custom-{codes}-{plan.planting_a:%Y-%m-%d}",
        rank=rank,
        ranked_among=len(context.ranked) + 1,
        would_be_recommended=rank <= MAX_ROTATIONS,
        crops=list(scored.ranked.crops),
        soil_protection=scored.soil_protection,
        scores=scored.scores,
        checks=custom_checks(context, plan),
    )


def decendio_starts(agricultural_year: int) -> list[date]:
    """First day of each of the 36 decêndios (1st, 11th, 21st) from Sep 1 to Aug 21."""
    starts = []
    for offset in range(12):
        month = (SEASON_START[0] - 1 + offset) % 12 + 1
        year = agricultural_year if month >= SEASON_START[0] else agricultural_year + 1
        starts.extend(date(year, month, day) for day in (1, 11, 21))
    return starts


def decendio_label(index: int) -> str:
    month = SEASON_MONTHS[index // 3]
    return f"{('1-10', '11-20', '21-end')[index % 3]} {month}"


def _sweep_crop(
    context: RankingContext,
    rotation_crop: RotationCrop,
    crop: CropDetail,
    calendar: CropCalendar | None,
) -> SweepCrop:
    risk = rotation_crop.crop.risk
    return SweepCrop(
        code=crop.eco_port_code,
        planting_date=rotation_crop.planting_date,
        harvest_date=rotation_crop.harvest_date,
        risk_percentage=risk.risk_percentage,
        estimated_yield_loss_pct=risk.estimated_yield_loss_pct,
        in_calendar=fits_calendar(rotation_crop.planting_date, calendar),
        temperature_ok=_temperature_ok(context, crop, rotation_crop.planting_date),
    )


def planting_sweep(
    context: RankingContext,
    plan: CustomPlan,
    priorities: Priorities,
    moved_crop: Literal["first", "second"],
) -> PlantingSweep:
    """The rotation with the planting date of one crop on the start of every decêndio.

    "first" moves only A: B keeps its planned dates, and points where A would be harvested on
    or after the planting of B are left out. The result depends on A's cycle and B's dates, not
    on A's planting date. "second" keeps A and moves B after its harvest (needs a second crop).
    Points that end after Aug 31 are left out.
    """
    season_end = date(context.year + 1, *SEASON_END)
    first_seasons: list[_FirstCropSeason] | None = None
    points: list[SweepPoint] = []
    keys: list[tuple[tuple, int]] = []
    for start in decendio_starts(context.year):
        if moved_crop == "first":
            candidate, shared = replace(plan, planting_a=start), None
            if candidate.planting_b is not None and candidate.harvest_a >= candidate.planting_b:
                continue
        else:
            if start <= plan.harvest_a:
                continue
            candidate = plan.with_second_planting(start)
            if first_seasons is None:
                first_seasons = _simulate_first_crop(
                    plan.crop_a,
                    plan.planting_a,
                    context.climate,
                    context.year,
                    context.season_years,
                    context.profile,
                    INITIAL_SOIL_WATER_FRACTION,
                )
            shared = first_seasons
        if candidate.last_harvest > season_end:
            continue
        scored = score_rotation(
            evaluate_plan(context, candidate, shared), context.previous_families, context.year
        )
        index = decendio_idx(start)
        points.append(
            SweepPoint(
                decendio_idx=index,
                label=decendio_label(index),
                crops=[
                    _sweep_crop(context, rotation_crop, crop, calendar)
                    for rotation_crop, (crop, _, calendar) in zip(
                        scored.ranked.crops, candidate.planted_crops(), strict=True
                    )
                ],
                scores=scored.scores,
            )
        )
        keys.append((_priority_key(scored, priorities), index))
    return PlantingSweep(
        moved_crop=moved_crop,
        points=points,
        best_decendio_idx=min(keys)[1] if keys else None,
        worst_decendio_idx=max(keys)[1] if keys else None,
    )


def simulate_rotation(
    repository: CropRepository,
    request: RotationSimulationRequest,
    climate_days: Sequence[DiaClima],
    season_years: Sequence[int],
    today: date,
    climate_key: Hashable | None = None,
    soil: SoilInfo | None = None,
) -> RotationSimulationResponse:
    """One custom rotation, its rank among the recommendations and, optionally, its sweeps."""
    context = ranking_context(
        repository, request, climate_days, season_years, today, climate_key, soil
    )
    plan = resolve_custom_rotation(repository, request.rotation, context)
    priorities = request.priorities
    return RotationSimulationResponse(
        agricultural_year=context.year,
        soil_type=context.soil.texture_class,
        soil=context.soil,
        priorities=priorities,
        rotation=custom_rotation(context, plan, priorities),
        first_planting_sweep=(
            planting_sweep(context, plan, priorities, "first") if request.sweep else None
        ),
        second_planting_sweep=(
            planting_sweep(context, plan, priorities, "second")
            if request.sweep and plan.crop_b is not None
            else None
        ),
        assumptions=_assumptions(),
        warnings=_warnings(context, request, custom=True),
    )
