import asyncio
import logging
from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.schemas.clima import (
    FONTE_SMAP,
    Celula,
    ClimaDecendios,
    ClimaDiario,
    ClimateScenarioInfo,
    Decendio,
    Dia,
    Ponto,
)
from app.services.agro import agregar_decendios, mesclar_smap
from app.services.climate_scenario import SCENARIOS
from app.services.power_client import (
    Janela,
    PowerClient,
    PowerIndisponivel,
    RespostaInvalida,
    SerieDiaria,
    janela_safras,
)
from app.services.smap_client import (
    SerieSmap,
    SmapClient,
    SmapIndisponivel,
    SmapRespostaInvalida,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/clima", tags=["clima"])


def get_power_client(request: Request) -> PowerClient:
    return request.app.state.power_client


def get_smap_client(request: Request) -> SmapClient | None:
    return getattr(request.app.state, "smap_client", None)


Client = Annotated[PowerClient, Depends(get_power_client)]
Smap = Annotated[SmapClient | None, Depends(get_smap_client)]
Lat = Annotated[float, Query(ge=-90, le=90, description="Latitude, graus decimais")]
Lon = Annotated[float, Query(ge=-180, le=180, description="Longitude, graus decimais")]


async def _serie(
    client: PowerClient, lat: float, lon: float, janela: Janela | None = None
) -> SerieDiaria:
    try:
        return await client.serie(lat, lon, janela)
    except PowerIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RespostaInvalida as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _smap(
    smap: SmapClient | None, lat: float, lon: float, janela: Janela
) -> SerieSmap | None:
    """Umidade SMAP; se faltar, a resposta sai sem ela em vez de falhar."""
    if smap is None:
        return None
    try:
        return await smap.serie(lat, lon, janela)
    except (SmapIndisponivel, SmapRespostaInvalida) as exc:
        log.warning("SMAP indisponível para %s, %s: %s", lat, lon, exc)
        return None


def _celula(serie: SerieDiaria) -> Celula:
    return Celula(lat=serie.lat, lon=serie.lon, elev=serie.elev)


@router.get("/decendios", response_model=ClimaDecendios)
async def clima_decendios(lat: Lat, lon: Lon, client: Client, smap: Smap) -> ClimaDecendios:
    """Climatologia das 10 últimas safras completas (set–ago) em 36 decêndios.

    Inclui a umidade do solo SMAP L4 (Earth Engine) quando disponível; senão, `sm_*` = null.
    """
    janela = janela_safras()
    serie, umidade = await asyncio.gather(
        _serie(client, lat, lon, janela), _smap(smap, lat, lon, janela)
    )
    dias = mesclar_smap(serie.dias, umidade.dias) if umidade else serie.dias
    return ClimaDecendios(
        ponto=Ponto(lat=lat, lon=lon),
        celula=_celula(serie),
        safras=janela.safras,
        pixel_smap=Ponto(lat=umidade.lat, lon=umidade.lon) if umidade else None,
        fonte_umidade_solo=FONTE_SMAP if umidade else None,
        decendios=[Decendio(**asdict(r)) for r in agregar_decendios(dias)],
    )


@router.get("/diario", response_model=ClimaDiario)
async def clima_diario(
    lat: Lat,
    lon: Lon,
    client: Client,
    inicio: Annotated[date | None, Query(description="AAAA-MM-DD; padrão: início")] = None,
    fim: Annotated[date | None, Query(description="AAAA-MM-DD; padrão: fim")] = None,
) -> ClimaDiario:
    """Série diária bruta (com ET0), recortada da janela das 10 últimas safras."""
    janela = janela_safras()
    inicio = inicio or janela.inicio
    fim = fim or janela.fim
    if inicio > fim:
        raise HTTPException(status_code=422, detail="inicio depois de fim")
    if inicio < janela.inicio or fim > janela.fim:
        raise HTTPException(
            status_code=422,
            detail=f"intervalo fora da janela disponível ({janela.inicio} a {janela.fim})",
        )
    serie = await _serie(client, lat, lon)
    return ClimaDiario(
        ponto=Ponto(lat=lat, lon=lon),
        celula=_celula(serie),
        dias=[Dia(**asdict(d)) for d in serie.dias if inicio <= d.data <= fim],
    )


@router.get("/scenarios", response_model=list[ClimateScenarioInfo])
async def climate_scenarios() -> list[ClimateScenarioInfo]:
    """Climate scenarios accepted by `climate_scenario` in /rotations (delta method).

    `historical` is the observed record; the others perturb it (+°C, rainfall change, later
    rainy season onset). They are "what if" scenarios, not a forecast of the next season.
    """
    return [ClimateScenarioInfo(**asdict(scenario)) for scenario in SCENARIOS.values()]
