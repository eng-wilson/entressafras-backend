from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.schemas.clima import Celula, ClimaDecendios, ClimaDiario, Decendio, Dia, Ponto
from app.services.agro import agregar_decendios
from app.services.power_client import (
    PowerClient,
    PowerIndisponivel,
    RespostaInvalida,
    SerieDiaria,
    janela_safras,
)

router = APIRouter(prefix="/clima", tags=["clima"])


def get_power_client(request: Request) -> PowerClient:
    return request.app.state.power_client


Client = Annotated[PowerClient, Depends(get_power_client)]
Lat = Annotated[float, Query(ge=-90, le=90, description="Latitude, graus decimais")]
Lon = Annotated[float, Query(ge=-180, le=180, description="Longitude, graus decimais")]


async def _serie(client: PowerClient, lat: float, lon: float) -> SerieDiaria:
    try:
        return await client.serie(lat, lon)
    except PowerIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RespostaInvalida as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _celula(serie: SerieDiaria) -> Celula:
    return Celula(lat=serie.lat, lon=serie.lon, elev=serie.elev)


@router.get("/decendios", response_model=ClimaDecendios)
async def clima_decendios(lat: Lat, lon: Lon, client: Client) -> ClimaDecendios:
    """Climatologia das 10 últimas safras completas (set–ago) em 36 decêndios."""
    serie = await _serie(client, lat, lon)
    return ClimaDecendios(
        ponto=Ponto(lat=lat, lon=lon),
        celula=_celula(serie),
        safras=janela_safras().safras,
        decendios=[Decendio(**asdict(r)) for r in agregar_decendios(serie.dias)],
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
