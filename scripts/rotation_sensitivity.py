"""Sensitivity of the rotation ranking to the soil and the initial soil water (20/50/80%).

Crops follow the GGCMI crop calendar of the location (planting dates and cycle lengths).

Prints the top 4 rotations for each soil (the three texture classes and the soil estimated from
ISRIC SoilGrids with Saxton & Rawls 2006) and initial water fraction, and whether the
recommended rotations change. Uses the POWER and SoilGrids seeds/caches first, then the network.

Usage: uv run python -m scripts.rotation_sensitivity [lat lon]   (default: Sorriso-MT)
"""

import asyncio
import sys
from datetime import date

import httpx

from app.core.config import get_settings
from app.services.crop_calendar import crop_calendars
from app.services.ecocrop import get_repository
from app.services.power_client import PowerClient, janela_safras
from app.services.rotations import (
    INITIAL_SOIL_WATER_FRACTION,
    ROTATION_CROP_CODES,
    ROTATION_SEASONS,
    SENSITIVITY_WATER_FRACTIONS,
    RankedRotation,
    next_agricultural_year,
    rank_rotations,
    top_sequences,
)
from app.services.soil import (
    SoilProfile,
    SoilType,
    zarc_ad_class,
    zarc_available_water_mm_per_cm,
)
from app.services.soilgrids_client import SoilGridsClient

SORRISO = (-12.55, -55.72)


def _describe(rank: int, rotation: RankedRotation) -> str:
    parts = []
    for rotation_crop in (rotation.first, rotation.second):
        risk = rotation_crop.crop.risk
        name = rotation_crop.crop.water_requirements.profile
        parts.append(
            f"{name} {rotation_crop.planting_date:%d/%m}→{rotation_crop.harvest_date:%d/%m} "
            f"risk {risk.risk_percentage}% loss {risk.estimated_yield_loss_pct}% "
            f"({risk.critical_stage})"
        )
    return f"  {rank}. " + "  |  ".join(parts)


async def main(lat: float, lon: float) -> None:
    settings = get_settings()
    window = janela_safras(n_safras=ROTATION_SEASONS)
    async with httpx.AsyncClient(timeout=settings.power_timeout_s) as http:
        client = PowerClient(
            http,
            base_url=settings.power_base_url,
            seed_dir=settings.power_seed_dir,
            cache_dir=settings.power_cache_dir,
        )
        series = await client.serie(lat, lon, window)
    async with httpx.AsyncClient(timeout=settings.soilgrids_timeout_s) as http:
        soilgrids = SoilGridsClient(
            http,
            base_url=settings.soilgrids_base_url,
            seed_dir=settings.soilgrids_seed_dir,
            cache_dir=settings.soilgrids_cache_dir,
            min_interval_s=settings.soilgrids_min_interval_s,
        )
        estimate = await soilgrids.estimate(lat, lon)
    season_years = list(range(window.inicio.year, window.fim.year))
    year = next_agricultural_year(date.today())
    repository = get_repository()
    crops = [crop for code in ROTATION_CROP_CODES if (crop := repository.get(code)) is not None]
    print(f"{lat}, {lon} · seasons {window.safras[0]}–{window.safras[-1]} · projected on {year}")
    top = estimate.zarc_layer_texture
    ad = zarc_available_water_mm_per_cm(top.sand_pct, top.silt_pct, top.clay_pct)
    print(
        f"SoilGrids at {estimate.sampled_lat}, {estimate.sampled_lon} "
        f"({estimate.distance_km:g} km): Saxton & Rawls "
        f"{estimate.profile.available_water_mm_per_m:.0f} mm/m; local check IN SPA/MAPA 1/2022 "
        f"(0–40 cm) {ad * 100:.0f} mm/m, {zarc_ad_class(ad)}"
    )

    calendars = crop_calendars(lat, lon)
    for code, calendar in calendars.items():
        name = repository.get(code).water_requirements.profile
        print(
            f"GGCMI {name}: planting {calendar.planting_date(year):%d/%m}, "
            f"{calendar.season_length_days} days ({calendar.data_source})"
        )

    soils: dict[str, SoilProfile] = {
        str(soil_type): SoilProfile.from_texture_class(soil_type) for soil_type in SoilType
    }
    soils["soilgrids"] = estimate.profile
    for name, soil in soils.items():
        tops = {}
        for fraction in SENSITIVITY_WATER_FRACTIONS:
            ranked = rank_rotations(
                crops, series.dias, season_years, soil, year, fraction, calendars=calendars
            )
            tops[fraction] = top_sequences(ranked)
            print(
                f"\n{name} ({soil.available_water_mm_per_m:.0f} mm/m) · "
                f"initial soil water {fraction:.0%}"
            )
            for rank, rotation in enumerate(ranked[:4], start=1):
                print(_describe(rank, rotation))
        reference = tops[INITIAL_SOIL_WATER_FRACTION]
        changed = [f"{f:.0%}" for f, top in tops.items() if top != reference]
        verdict = f"CHANGES with {', '.join(changed)}" if changed else "stable"
        print(f"→ {name}: top 4 {verdict}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        sys.exit("usage: uv run python -m scripts.rotation_sensitivity [lat lon]")
    lat, lon = (float(args[0]), float(args[1])) if args else SORRISO
    asyncio.run(main(lat, lon))
