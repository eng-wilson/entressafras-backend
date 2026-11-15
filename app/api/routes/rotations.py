import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes.clima import Client
from app.api.routes.clima import _serie as fetch_power_series
from app.schemas.rotation import (
    RotationRecommendationsResponse,
    RotationRequest,
    SoilInfo,
    SoilProperties,
)
from app.services.ecocrop import CropRepository, get_repository
from app.services.power_client import janela_safras
from app.services.rotations import (
    ROTATION_SEASONS,
    history_families,
    recommendations,
    soil_info_from_estimate,
    soil_info_from_texture_class,
    soil_properties_from_estimate,
)
from app.services.soilgrids_client import (
    SoilGridsClient,
    SoilGridsInvalidResponse,
    SoilGridsNoData,
    SoilGridsUnavailable,
)

router = APIRouter(prefix="/rotations", tags=["rotations"])

SEND_SOIL_TYPE = "send soil_type (sandy, medium or clayey) to skip the soil estimate"


def get_today() -> date:
    return date.today()


def get_soilgrids_client(request: Request) -> SoilGridsClient:
    return request.app.state.soilgrids_client


Repository = Annotated[CropRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]
SoilGrids = Annotated[SoilGridsClient, Depends(get_soilgrids_client)]


SoilErrors = (SoilGridsNoData, SoilGridsUnavailable, SoilGridsInvalidResponse)


async def _soil(
    soilgrids: SoilGridsClient, request: RotationRequest
) -> tuple[SoilInfo, SoilProperties | None, str | None]:
    """Soil for the simulation, plus the soil properties panel (or why it is missing).

    With soil_type the SoilGrids estimate only feeds the panel, so its failure is not an error.
    """
    if request.soil_type is not None:
        soil = soil_info_from_texture_class(request.soil_type)
        try:
            estimate = await soilgrids.estimate(request.location.lat, request.location.lon)
        except SoilErrors as exc:
            return soil, None, str(exc)
        return soil, soil_properties_from_estimate(estimate), None
    try:
        estimate = await soilgrids.estimate(request.location.lat, request.location.lon)
    except SoilGridsNoData as exc:
        raise HTTPException(status_code=422, detail=f"{exc}; {SEND_SOIL_TYPE}") from exc
    except SoilGridsUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"{exc}; {SEND_SOIL_TYPE}") from exc
    except SoilGridsInvalidResponse as exc:
        raise HTTPException(status_code=502, detail=f"{exc}; {SEND_SOIL_TYPE}") from exc
    return soil_info_from_estimate(estimate), soil_properties_from_estimate(estimate), None


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
    """
    try:
        history_families(repository, request.crop_history)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    window = janela_safras(today, ROTATION_SEASONS)
    series, (soil, soil_properties, soil_properties_error) = await asyncio.gather(
        fetch_power_series(client, request.location.lat, request.location.lon, window),
        _soil(soilgrids, request),
    )
    season_years = list(range(window.inicio.year, window.fim.year))
    # CPU-bound (about 6 s on 30 seasons, then cached per cell and soil): keep it off the loop.
    return await run_in_threadpool(
        recommendations,
        repository,
        request,
        series.dias,
        season_years,
        today,
        (series.lat, series.lon),
        soil,
        soil_properties,
        soil_properties_error,
    )
