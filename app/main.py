from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.services.power_client import PowerClient

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=settings.power_timeout_s) as http:
        app.state.power_client = PowerClient(
            http,
            base_url=settings.power_base_url,
            seed_dir=settings.power_seed_dir,
            cache_dir=settings.power_cache_dir,
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
