"""Build app/data/ggcmi/ from the GGCMI Phase 3 crop calendar (global, 0.5°, rainfed).

Source: Jägermeyr et al. (2021), Nature Food 2:873–885; data on Zenodo,
https://doi.org/10.5281/zenodo.5062513 (v1.01, CC BY 4.0). Downloads one NetCDF per crop into
.cache/ggcmi/ and stores planting day, season length and data source of every crop in a single
compressed numpy file, so the API needs neither netCDF4 nor the network.

Usage: uv run --with netCDF4 python -m scripts.build_ggcmi_calendar
"""

import asyncio
from pathlib import Path

import httpx
import numpy as np

from app.core.config import PROJECT_DIR
from app.services.crop_calendar import CALENDAR_FILE, GGCMI_CROPS, MISSING

ZENODO = "https://zenodo.org/records/5062513/files/{crop}_rf_ggcmi_crop_calendar_phase3_v1.01.nc4"
CACHE_DIR = PROJECT_DIR / ".cache" / "ggcmi"


async def download(crop: str) -> Path:
    path = CACHE_DIR / f"{crop}_rf.nc4"
    if not path.is_file():
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
            resp = await http.get(ZENODO.format(crop=crop), params={"download": 1})
            resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return path


def _grid(dataset, name: str, dtype) -> np.ndarray:
    values = np.ma.masked_invalid(dataset[name][:])
    return np.ma.round(values).filled(MISSING).astype(dtype)


def main() -> None:
    import netCDF4  # build-time only: uv run --with netCDF4

    arrays: dict[str, np.ndarray] = {}
    for crop in sorted(set(GGCMI_CROPS.values())):
        path = asyncio.run(download(crop))
        with netCDF4.Dataset(path) as dataset:
            lat, lon = np.asarray(dataset["lat"][:]), np.asarray(dataset["lon"][:])
            if "lat" in arrays:
                assert np.array_equal(arrays["lat"], lat) and np.array_equal(arrays["lon"], lon)
            arrays["lat"], arrays["lon"] = lat, lon
            arrays[f"{crop}_planting_day"] = _grid(dataset, "planting_day", np.int16)
            arrays[f"{crop}_season_length"] = _grid(dataset, "growing_season_length", np.int16)
            arrays[f"{crop}_data_source"] = _grid(dataset, "data_source_used", np.int8)
        cells = int((arrays[f"{crop}_planting_day"] != MISSING).sum())
        print(f"{crop}: {cells} cells with a calendar")
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CALENDAR_FILE, **arrays)
    print(f"{CALENDAR_FILE}: {CALENDAR_FILE.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
