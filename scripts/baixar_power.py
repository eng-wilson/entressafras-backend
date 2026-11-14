"""Pré-baixa a série do NASA POWER de um ponto para app/data/power/ (semente versionada).

A semente deixa a demo funcionar sem rede e evita repetir consultas à NASA.

Uso: uv run python -m scripts.baixar_power [lat lon]   (padrão: Sorriso-MT, -12.55 -55.72)
"""

import asyncio
import json
import sys

import httpx

from app.core.config import get_settings
from app.services.power_client import PowerClient, celula, interpretar, janela_safras

SORRISO = (-12.55, -55.72)


async def main(lat: float, lon: float) -> None:
    settings = get_settings()
    janela = janela_safras()
    print(f"NASA POWER {lat}, {lon} → célula {celula(lat, lon)} · {janela.inicio} a {janela.fim}")
    async with httpx.AsyncClient(timeout=settings.power_timeout_s) as http:
        client = PowerClient(
            http,
            base_url=settings.power_base_url,
            seed_dir=settings.power_seed_dir,
            cache_dir=settings.power_cache_dir,
        )
        chave, bruto = await client.bruto(lat, lon, janela)
    destino = settings.power_seed_dir / chave
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(bruto, separators=(",", ":")), encoding="utf-8")

    serie = interpretar(bruto, *celula(lat, lon))
    lacunas = {
        campo: sum(getattr(d, campo) is None for d in serie.dias)
        for campo in ("tmax", "tmin", "tdew", "rad", "vento", "chuva", "gwetroot")
    }
    metodos: dict[str | None, int] = {}
    for d in serie.dias:
        metodos[d.et0_metodo] = metodos.get(d.et0_metodo, 0) + 1
    print(f"{destino}: {len(serie.dias)} dias, elevação {serie.elev} m")
    print(f"lacunas: {lacunas}")
    print(f"métodos de ET0: {metodos}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        sys.exit("uso: uv run python -m scripts.baixar_power [lat lon]")
    lat, lon = (float(args[0]), float(args[1])) if args else SORRISO
    asyncio.run(main(lat, lon))
