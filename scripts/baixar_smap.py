"""Pré-baixa a umidade do solo SMAP L4 (Earth Engine) de um ponto para app/data/smap/ (semente).

Precisa de EE_PROJECT (projeto Cloud registrado no Earth Engine) no .env ou no ambiente, e de
credencial: `earthengine authenticate` ou EE_SERVICE_ACCOUNT_KEY.

Uso: uv run python -m scripts.baixar_smap [lat lon]   (padrão: Sorriso-MT, -12.55 -55.72)
"""

import asyncio
import json
import sys

from app.core.config import get_settings
from app.services.power_client import janela_safras
from app.services.smap_client import SmapClient, buscar_ee, interpretar, pixel

SORRISO = (-12.55, -55.72)


async def main(lat: float, lon: float) -> None:
    settings = get_settings()
    if not (settings.ee_project or settings.ee_service_account_key):
        sys.exit("defina EE_PROJECT (e, no servidor, EE_SERVICE_ACCOUNT_KEY)")
    janela = janela_safras()
    print(f"SMAP {lat}, {lon} → pixel {pixel(lat, lon)} · {janela.inicio} a {janela.fim}")
    client = SmapClient(
        seed_dir=settings.smap_seed_dir,
        cache_dir=settings.smap_cache_dir,
        buscar=buscar_ee(settings.smap_asset, settings.ee_project, settings.ee_service_account_key),
    )
    chave, bruto = await client.bruto(lat, lon, janela)
    destino = settings.smap_seed_dir / chave
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(bruto, separators=(",", ":")), encoding="utf-8")

    serie = interpretar(bruto)
    esperados = (janela.fim - janela.inicio).days + 1
    lacunas = {
        banda: sum(v[banda] is None for v in serie.dias.values()) for banda in bruto["bandas"]
    }
    print(f"{destino}: {len(serie.dias)} de {esperados} dias, pixel EE {serie.lat}, {serie.lon}")
    print(f"dias descartados (incompletos): {esperados - len(serie.dias)}")
    print(f"lacunas: {lacunas}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        sys.exit("uso: uv run python -m scripts.baixar_smap [lat lon]")
    lat, lon = (float(args[0]), float(args[1])) if args else SORRISO
    asyncio.run(main(lat, lon))
