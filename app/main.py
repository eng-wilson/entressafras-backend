import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.services.power_client import PowerClient
from app.services.smap_client import SmapClient, SmapIndisponivel, buscar_ee, inicializar_ee

settings = get_settings()
log = logging.getLogger(__name__)


async def _checar_earth_engine() -> str:
    """Autentica no start para o erro aparecer no log do deploy, não na 1ª requisição."""
    if not settings.ee_configurado:
        return "nao_configurado"
    try:
        await asyncio.to_thread(
            inicializar_ee,
            settings.ee_project,
            settings.ee_service_account_key,
            settings.ee_service_account_json,
        )
    except SmapIndisponivel as exc:
        log.error("Earth Engine indisponível; SMAP só a partir de semente/cache: %s", exc)
        return "erro"
    return "ok"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=settings.power_timeout_s) as http:
        app.state.power_client = PowerClient(
            http,
            base_url=settings.power_base_url,
            seed_dir=settings.power_seed_dir,
            cache_dir=settings.power_cache_dir,
        )
        app.state.ee_status = await _checar_earth_engine()
        app.state.smap_client = SmapClient(
            seed_dir=settings.smap_seed_dir,
            cache_dir=settings.smap_cache_dir,
            buscar=buscar_ee(
                settings.smap_asset,
                settings.ee_project,
                settings.ee_service_account_key,
                settings.ee_service_account_json,
            )
            if settings.ee_configurado
            else None,
        )
        yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": f"{settings.app_name} is running"}
