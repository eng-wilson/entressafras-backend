"""Reference price, yield and gross revenue per crop (FAOSTAT, World Bank Pink Sheet).

The data is a versioned seed built by `scripts/build_crop_economics.py`; no network at runtime.
"""

import json
from collections.abc import Iterable
from functools import cache

from app.core.config import APP_DIR
from app.schemas.crop import CropEconomics

ECONOMICS_FILE = APP_DIR / "data" / "prices" / "crop_economics.json"


@cache
def load_crop_economics() -> dict[int, CropEconomics]:
    data = json.loads(ECONOMICS_FILE.read_text(encoding="utf-8"))
    return {int(code): CropEconomics(**crop) for code, crop in data["crops"].items()}


def crop_economics(code: int) -> CropEconomics | None:
    return load_crop_economics().get(code)


def rotation_gross_revenue(economics: Iterable[CropEconomics | None]) -> float | None:
    """Gross revenue of the whole rotation per hectare; None when a crop has no data."""
    values = list(economics)
    if not values or any(item is None for item in values):
        return None
    return round(sum(item.gross_revenue_usd_ha for item in values), 1)


def revenue_ceiling(rotations: Iterable[Iterable[CropEconomics | None]]) -> float | None:
    """Ceiling of the revenue score: the highest gross revenue (no loss) among the rotations.

    Given the viable rotations of a site, 100 is the best of them with no water-limited loss.
    Rotations with a crop without economics are skipped; None when none is left.
    """
    revenues = [
        revenue for crops in rotations if (revenue := rotation_gross_revenue(crops)) is not None
    ]
    return max(revenues, default=None)


def water_adjusted_revenue(
    crops: Iterable[tuple[CropEconomics | None, float | None]],
) -> float | None:
    """Σ gross revenue × (1 − water-deficit loss %/100), USD/ha; None if any value is missing."""
    values = list(crops)
    if not values or any(item is None or loss is None for item, loss in values):
        return None
    return sum(item.gross_revenue_usd_ha * (1 - loss / 100) for item, loss in values)  # type: ignore[union-attr,operator]
