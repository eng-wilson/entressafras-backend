"""Pre-download the ISRIC SoilGrids answers for a location into app/data/soilgrids/ (seed).

Saves every point the client reads until it finds soil data (the requested point and, when it
is built-up, the ring points up to the first valid one), so the rotation route works offline.
SoilGrids fair use is 5 calls/min: calls are spaced by `soilgrids_min_interval_s`.

Usage: uv run python -m scripts.download_soilgrids [lat lon]   (default: Sorriso-MT)
"""

import asyncio
import json
import sys

import httpx

from app.core.config import get_settings
from app.services.soil import saxton_rawls_2006, zarc_ad_class, zarc_available_water_mm_per_cm
from app.services.soilgrids_client import SoilGridsClient, parse, search_points

SORRISO = (-12.55, -55.72)


async def main(lat: float, lon: float) -> None:
    settings = get_settings()
    seed_dir = settings.soilgrids_seed_dir
    async with httpx.AsyncClient(timeout=settings.soilgrids_timeout_s) as http:
        client = SoilGridsClient(
            http,
            base_url=settings.soilgrids_base_url,
            seed_dir=seed_dir,
            cache_dir=settings.soilgrids_cache_dir,
            min_interval_s=settings.soilgrids_min_interval_s,
        )
        for point_lat, point_lon, distance_km in search_points(lat, lon):
            key, raw = await client.raw(point_lat, point_lon)
            seed_dir.mkdir(parents=True, exist_ok=True)
            (seed_dir / key).write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
            has_data = parse(raw).has_data
            print(f"{seed_dir / key}: {distance_km:g} km, data={has_data}")
            if has_data:
                break
        estimate = await client.estimate(lat, lon)

    root = estimate.root_zone_texture
    top = estimate.zarc_layer_texture
    ad = zarc_available_water_mm_per_cm(top.sand_pct, top.silt_pct, top.clay_pct)
    print(
        f"sampled {estimate.sampled_lat}, {estimate.sampled_lon} ({estimate.distance_km:g} km)\n"
        f"0-100 cm: sand {root.sand_pct:.1f}% silt {root.silt_pct:.1f}% clay {root.clay_pct:.1f}% "
        f"OM {root.organic_matter_pct:.2f}% → {estimate.profile.texture_class}, "
        f"Saxton & Rawls {saxton_rawls_2006(root).available_water_mm_per_m:.0f} mm/m\n"
        f"0-40 cm (local check, IN SPA/MAPA 1/2022): {ad * 100:.0f} mm/m, {zarc_ad_class(ad)}"
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        sys.exit("usage: uv run python -m scripts.download_soilgrids [lat lon]")
    lat, lon = (float(args[0]), float(args[1])) if args else SORRISO
    asyncio.run(main(lat, lon))
