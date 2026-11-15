"""Regional planting dates and cycle lengths from the GGCMI Phase 3 crop calendar.

Global 0.5° grid of rainfed planting day and growing season length per crop, harmonized from
national and subnational statistics (e.g. CONAB for Brazil) with MIRCA2000/SAGE as fallback:
Jägermeyr et al. (2021), Nature Food 2:873–885, https://doi.org/10.5281/zenodo.5062513 (v1.01,
CC BY 4.0). `scripts/build_ggcmi_calendar.py` packs the crops below into one numpy file.

The FAO-56 stage proportions (Kc curve, Ky per stage) are kept and stretched to the regional
cycle length, because GGCMI gives only the whole season.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache

import numpy as np

from app.core.config import APP_DIR
from app.schemas.crop import CropDetail, StageLengths

CALENDAR_FILE = APP_DIR / "data" / "ggcmi" / "ggcmi_phase3_v1.01_rf.npz"
CALENDAR_SOURCE = "GGCMI_PHASE3_V1_01"
MISSING = -1
GRID_STEP_DEG = 0.5

# EcoCrop code → GGCMI crop. Cowpea has no GGCMI calendar ("pea" is field pea, Pisum sativum).
GGCMI_CROPS: dict[int, str] = {
    1150: "soy",  # soybean
    2175: "mai",  # maize
    48747: "sor",  # sorghum
    8418: "mil",  # millet
    1160: "cot",  # cotton
    1668: "bea",  # common bean
    1191: "sun",  # sunflower
    2199: "nut",  # groundnut
}
# Codes of the `data_source_used` variable.
DATA_SOURCES = {
    1: "MIRCA2000",
    2: "SAGE",
    3: "Iizumi et al. 2019",
    4: "RiceAtlas",
    5: "Dimou et al. 2018",
    6: "ChinaCropPhen1km",
    7: "India Agricultural Statistics",
    8: "Brazil CONAB",
    9: "Australia ABARES",
}
# Shortest season accepted from the grid; shorter values are treated as missing.
MIN_SEASON_DAYS = 30


@dataclass(frozen=True)
class CropCalendar:
    planting_day: int  # day of year
    season_length_days: int  # GGCMI growing_season_length = maturity day − planting day
    data_source: str
    cell_lat: float
    cell_lon: float

    def planting_date(self, agricultural_year: int) -> date:
        """Planting date inside the season that starts on Sep 1 of `agricultural_year`."""
        for year in (agricultural_year, agricultural_year + 1):
            planting = date(year, 1, 1) + timedelta(days=self.planting_day - 1)
            if date(agricultural_year, 9, 1) <= planting <= date(agricultural_year + 1, 8, 31):
                return planting
        # Day 366 of a non-leap year: last day of that year.
        return date(agricultural_year, 12, 31)

    def days_from_planting(self, planting: date) -> int:
        """Circular distance (days) between a date and the calendar planting day of its year."""
        doy = planting.timetuple().tm_yday
        distance = abs(doy - self.planting_day) % 365
        return min(distance, 365 - distance)


@cache
def _grid() -> dict[str, np.ndarray]:
    with np.load(CALENDAR_FILE) as data:
        return {name: data[name] for name in data.files}


def _cell_index(values: np.ndarray, coordinate: float) -> int:
    return int(np.abs(values - coordinate).argmin())


def crop_calendars(lat: float, lon: float) -> dict[int, CropCalendar]:
    """Calendars available at the 0.5° cell of the location, by EcoCrop code."""
    grid = _grid()
    i, j = _cell_index(grid["lat"], lat), _cell_index(grid["lon"], lon)
    cell_lat, cell_lon = float(grid["lat"][i]), float(grid["lon"][j])
    calendars = {}
    for code, crop in GGCMI_CROPS.items():
        planting_day = int(grid[f"{crop}_planting_day"][i, j])
        season_length = int(grid[f"{crop}_season_length"][i, j])
        if planting_day == MISSING or season_length < MIN_SEASON_DAYS:
            continue
        source = int(grid[f"{crop}_data_source"][i, j])
        calendars[code] = CropCalendar(
            planting_day=planting_day,
            season_length_days=season_length,
            data_source=DATA_SOURCES.get(source, f"code {source}"),
            cell_lat=cell_lat,
            cell_lon=cell_lon,
        )
    return calendars


def scale_stage_lengths(stages: StageLengths, total: int) -> StageLengths:
    """FAO-56 stage proportions stretched to `total` days (largest remainder rounding)."""
    names = ("initial", "development", "mid_season", "late_season")
    exact = [getattr(stages, name) * total / stages.total for name in names]
    days = [int(value) for value in exact]
    by_remainder = sorted(range(len(names)), key=lambda k: exact[k] - days[k], reverse=True)
    for k in by_remainder[: total - sum(days)]:
        days[k] += 1
    return StageLengths(**dict(zip(names, days, strict=True)), total=total)


def apply_calendar(crop: CropDetail, calendar: CropCalendar | None) -> CropDetail:
    """Copy of the crop with its cycle stretched to the regional season length."""
    if calendar is None or crop.water_requirements is None:
        return crop
    requirements = crop.water_requirements
    stages = scale_stage_lengths(requirements.stage_length_days, calendar.season_length_days)
    return crop.model_copy(
        update={"water_requirements": requirements.model_copy(update={"stage_length_days": stages})}
    )
