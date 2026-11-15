import asyncio
import logging
from datetime import date
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes.clima import Client
from app.core.config import get_settings
from app.schemas.rotation import (
    RotationContext,
    RotationRecommendationsResponse,
    RotationRequest,
    RotationSimulationRequest,
    RotationSimulationResponse,
    SoilInfo,
    SoilProperties,
)
from app.services.ecocrop import CropRepository, get_repository
from app.services.fallback import distance_km
from app.services.power_client import (
    Janela,
    PowerClient,
    PowerIndisponivel,
    RespostaInvalida,
    SerieDiaria,
    janela_safras,
)
from app.services.rotations import (
    ROTATION_SEASONS,
    CustomRotationError,
    history_families,
    recommendations,
    simulate_rotation,
    soil_info_from_estimate,
    soil_info_from_texture_class,
    soil_properties_from_estimate,
)
from app.services.soil import SoilType
from app.services.soilgrids_client import (
    SoilEstimate,
    SoilGridsClient,
    SoilGridsInvalidResponse,
    SoilGridsNoData,
    SoilGridsUnavailable,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/rotations", tags=["rotations"])

POWER_FALLBACK_WARNING = (
    "NASA POWER did not answer ({reason}); climate from the nearest cached cell at {lat:g}, "
    "{lon:g} ({distance:.0f} km away, seasons {start}–{end}). Risks describe that cell."
)
SOILGRIDS_FALLBACK_WARNING = (
    "SoilGrids did not answer for this location ({reason}); soil from the nearest cached point "
    "({distance:g} km away). Send soil_type if it does not describe the field."
)
NO_SOIL_WARNING = (
    "Soil estimate unavailable ({reason}) and no cached soil to fall back on; assumed medium "
    "texture. Send soil_type for a better estimate."
)


def get_today() -> date:
    return date.today()


def get_soilgrids_client(request: Request) -> SoilGridsClient:
    return request.app.state.soilgrids_client


Repository = Annotated[CropRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]
SoilGrids = Annotated[SoilGridsClient, Depends(get_soilgrids_client)]


SoilErrors = (SoilGridsNoData, SoilGridsUnavailable, SoilGridsInvalidResponse)


def _wait_budget_s() -> float:
    return get_settings().external_wait_budget_s


class SoilInputs(NamedTuple):
    soil: SoilInfo
    properties: SoilProperties | None  # the soil properties panel
    properties_error: str | None  # why the panel is missing
    warning: str | None  # set when the soil comes from a fallback


class Inputs(NamedTuple):
    series: SerieDiaria
    season_years: list[int]
    soil: SoilInputs
    warnings: list[str]  # fallbacks used to answer without an external source


async def _soil_estimate(
    soilgrids: SoilGridsClient, lat: float, lon: float
) -> tuple[SoilEstimate | None, str | None, str | None]:
    """(estimate, fallback warning, failure reason): live within the budget, else the cache."""
    budget = _wait_budget_s()
    try:
        return await asyncio.wait_for(soilgrids.estimate(lat, lon), budget), None, None
    except TimeoutError:
        soilgrids.mark_unavailable()
        reason = f"no answer within {budget:g} s"
    except SoilErrors as exc:
        reason = str(exc)
    log.warning("SoilGrids failed for %s, %s (%s); using the cached fallback", lat, lon, reason)
    fallback = soilgrids.nearest_cached(lat, lon)
    if fallback is None:
        return None, None, reason
    warning = SOILGRIDS_FALLBACK_WARNING.format(reason=reason, distance=fallback.distance_km)
    return fallback, warning, None


async def _soil(
    soilgrids: SoilGridsClient, request: RotationContext, need_properties: bool = True
) -> SoilInputs:
    """Soil for the simulation, the soil properties panel and the fallback warning, if any.

    Never fails: when SoilGrids is slow or down, the nearest cached point (any distance) is
    used, and without any cached point the request soil_type, or a medium texture. With
    soil_type the estimate only feeds the panel, so it is skipped when not needed.
    """
    lat, lon = request.location.lat, request.location.lon
    if request.soil_type is not None and not need_properties:
        return SoilInputs(soil_info_from_texture_class(request.soil_type), None, None, None)
    estimate, warning, reason = await _soil_estimate(soilgrids, lat, lon)
    if estimate is None:
        soil = soil_info_from_texture_class(request.soil_type or SoilType.MEDIUM)
        if request.soil_type is None:
            warning = NO_SOIL_WARNING.format(reason=reason)
        return SoilInputs(soil, None, reason, warning)
    soil = (
        soil_info_from_texture_class(request.soil_type)
        if request.soil_type is not None
        else soil_info_from_estimate(estimate)
    )
    return SoilInputs(soil, soil_properties_from_estimate(estimate), None, warning)


async def _climate(
    client: PowerClient, lat: float, lon: float, window: Janela
) -> tuple[SerieDiaria, list[int], str | None]:
    """POWER series, its season years and the fallback warning (nearest cached cell), if any."""
    budget = _wait_budget_s()
    try:
        series = await asyncio.wait_for(client.serie(lat, lon, window), budget)
        return series, list(range(window.inicio.year, window.fim.year)), None
    except TimeoutError:
        client.mark_unavailable()
        reason = f"no answer within {budget:g} s"
    except (PowerIndisponivel, RespostaInvalida) as exc:
        reason = str(exc)
    log.warning("POWER failed for %s, %s (%s); using the nearest cached cell", lat, lon, reason)
    series = client.nearest_cached(lat, lon, window)
    if series is None:  # the repository ships seeded cells, so this needs a broken deploy
        raise HTTPException(
            status_code=503, detail=f"NASA POWER unavailable ({reason}) and no cached climate"
        )
    start, end = series.inicio.year, series.fim.year
    warning = POWER_FALLBACK_WARNING.format(
        reason=reason,
        lat=series.lat,
        lon=series.lon,
        distance=distance_km(lat, lon, series.lat, series.lon),
        start=start,
        end=end,
    )
    return series, list(range(start, end)), warning


@router.post("/recommendations", response_model=RotationRecommendationsResponse)
async def recommend_rotations(
    request: RotationRequest,
    repository: Repository,
    client: Client,
    soilgrids: SoilGrids,
    today: Today,
) -> RotationRecommendationsResponse:
    """Up to 4 two-crop sequences for the next season, each crop with its own water risk.

    Every candidate is simulated with a simplified FAO-56 daily water balance over the last
    complete historical seasons of the NASA POWER cell (scenarios, not a forecast). Without
    `soil_type`, the soil water capacity is estimated from the location (ISRIC SoilGrids).
    Each rotation also gets an informational soil protection index (planned living cover and
    botanical family changes, with the optional `crop_history`), and the response carries the
    SoilGrids topsoil properties (pH, texture, organic carbon) when available.
    Optional `custom_rotations` are simulated and placed in the same ranking (returned apart,
    the recommended `rotations` do not change).
    """
    inputs = await _inputs(request, repository, client, soilgrids, today)
    series, soil = inputs.series, inputs.soil
    # CPU-bound (about 1.5 s on 30 seasons, then cached per cell and soil): keep it off the loop.
    try:
        response = await run_in_threadpool(
            recommendations,
            repository,
            request,
            series.dias,
            inputs.season_years,
            today,
            (series.lat, series.lon),
            soil.soil,
            soil.properties,
            soil.properties_error,
        )
    except CustomRotationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.warnings.extend(inputs.warnings)
    return response


@router.post("/simulate", response_model=RotationSimulationResponse)
async def simulate(
    request: RotationSimulationRequest,
    repository: Repository,
    client: Client,
    soilgrids: SoilGrids,
    today: Today,
) -> RotationSimulationResponse:
    """Test a client-defined two-crop rotation before adding it to the ranking.

    Returns the water risk of each crop on the given dates (omitted dates follow the regional
    calendar; a harvest date sets a custom cycle length), the position the rotation would take
    among the recommended ones and the agronomic `checks` it fails. With `sweep` (default), it
    also returns the same rotation with the planting date on every decêndio of the season, so a
    client can show the best and worst dates while the farmer drags them without new requests.
    """
    inputs = await _inputs(
        request, repository, client, soilgrids, today, need_soil_properties=False
    )
    series = inputs.series
    try:
        response = await run_in_threadpool(
            simulate_rotation,
            repository,
            request,
            series.dias,
            inputs.season_years,
            today,
            (series.lat, series.lon),
            inputs.soil.soil,
        )
    except CustomRotationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.warnings.extend(inputs.warnings)
    return response


async def _inputs(
    request: RotationContext,
    repository: CropRepository,
    client: PowerClient,
    soilgrids: SoilGridsClient,
    today: date,
    need_soil_properties: bool = True,
) -> Inputs:
    """Climate series, historical season years and soil of a rotation request.

    POWER and SoilGrids are queried in parallel, each within `external_wait_budget_s`; past it
    (or on error) the nearest cached data is used and named in `warnings`.
    """
    try:
        history_families(repository, request.crop_history)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    window = janela_safras(today, ROTATION_SEASONS)
    (series, season_years, climate_warning), soil = await asyncio.gather(
        _climate(client, request.location.lat, request.location.lon, window),
        _soil(soilgrids, request, need_soil_properties),
    )
    warnings = [warning for warning in (climate_warning, soil.warning) if warning]
    return Inputs(series, season_years, soil, warnings)
