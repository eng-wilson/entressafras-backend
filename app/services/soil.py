"""Soil available water: TAW = AW × Zr.

Reference: Allen et al. (1998), FAO-56, ch. 8 (TAW = 1000 (θFC − θWP) Zr). Pure calculation, no
I/O, so any service (water balance, crop profiles, etc.) can reuse it.
"""

from enum import StrEnum


class SoilType(StrEnum):
    SANDY = "sandy"
    MEDIUM = "medium"
    CLAYEY = "clayey"


# Available water per meter of soil depth (mm/m).
AVAILABLE_WATER_MM_PER_M: dict[SoilType, float] = {
    SoilType.SANDY: 80,
    SoilType.MEDIUM: 150,
    SoilType.CLAYEY: 160,
}


def calculate_available_water_capacity(soil_type: SoilType | str, root_depth_m: float) -> float:
    """Total available water (mm) = soil available water (mm/m) × effective root depth (m)."""
    try:
        soil = SoilType(soil_type)
    except ValueError:
        valid = ", ".join(SoilType)
        raise ValueError(f"Unknown soil type {soil_type!r}; expected one of: {valid}") from None
    if root_depth_m < 0:
        raise ValueError(f"root_depth_m must be >= 0, got {root_depth_m}")
    return AVAILABLE_WATER_MM_PER_M[soil] * root_depth_m
