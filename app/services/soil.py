"""Soil available water: TAW = AW × Zr.

References: Allen et al. (1998), FAO-56, ch. 8 (TAW = 1000 (θFC − θWP) Zr); Saxton & Rawls (2006),
SSSAJ 70:1569–1578 (field capacity and wilting point from texture and organic matter, the global
default); IN SPA/MAPA nº 1/2022 (Brazilian ZARC available-water classes, local validation only).
Pure calculation, no I/O, so any service (water balance, crop profiles, etc.) can reuse it.
"""

from dataclasses import dataclass
from enum import StrEnum


class SoilType(StrEnum):
    SANDY = "sandy"
    MEDIUM = "medium"
    CLAYEY = "clayey"


# Available water per meter of soil depth (mm/m) when only a texture class is known (request
# without location-based soil data). Location-based estimates use `SoilProfile.from_texture`.
# TODO(validate): rough class values; SoilGrids + Saxton & Rawls gives about 105–113 mm/m on
# Sorriso's farmland ("medium"), well below 150. Prefer the soil estimated from the location.
AVAILABLE_WATER_MM_PER_M: dict[SoilType, float] = {
    SoilType.SANDY: 80,
    SoilType.MEDIUM: 150,
    SoilType.CLAYEY: 160,
}


@dataclass(frozen=True)
class SurfaceEvaporationLimits:
    """Bare-soil evaporation limits of the top soil layer (Ze = 0.10 m), FAO-56 ch. 7."""

    readily_evaporable_water_mm: float  # REW: evaporated before the surface starts drying
    total_evaporable_water_mm: float  # TEW: maximum depth evaporated from the surface layer


# Midpoints of FAO-56 Table 19 ranges (ch. 7): sand REW 2–7 / TEW 6–12; loam 8–10 / 16–22;
# clay 8–12 / 22–29. Verified against https://www.fao.org/4/x0490e/x0490e0c.htm.
# TODO(validate): generic FAO texture midpoints, not calibrated for Brazilian (e.g. MT) soils.
SURFACE_EVAPORATION_LIMITS: dict[SoilType, SurfaceEvaporationLimits] = {
    SoilType.SANDY: SurfaceEvaporationLimits(4.5, 9.0),
    SoilType.MEDIUM: SurfaceEvaporationLimits(9.0, 19.0),
    SoilType.CLAYEY: SurfaceEvaporationLimits(10.0, 25.5),
}

# Upper limit of the soil evaporation coefficient after wetting (FAO-56 eq. 71–72, bare soil:
# Kc max = 1.20 with grass-reference ET0).
MAX_EVAPORATION_COEFFICIENT = 1.20


@dataclass(frozen=True)
class SoilTexture:
    """Particle-size fractions of the fine earth (%) and organic matter (% by weight)."""

    sand_pct: float
    silt_pct: float
    clay_pct: float
    organic_matter_pct: float


@dataclass(frozen=True)
class SoilWaterLimits:
    field_capacity: float  # θ33, m³/m³ (−33 kPa)
    wilting_point: float  # θ1500, m³/m³ (−1500 kPa)

    @property
    def available_water_mm_per_m(self) -> float:
        return 1000 * max(0.0, self.field_capacity - self.wilting_point)


def saxton_rawls_2006(texture: SoilTexture) -> SoilWaterLimits:
    """Field capacity and wilting point, Saxton & Rawls (2006), Table 1 (eq. 1 and 2).

    Sand and clay enter as fractions, organic matter in %. Coefficients checked against the USDA
    WEPP (saxpar.for) and DSSAT implementations. The regressions were fitted on USDA A-horizons.
    TODO(validate): tropical soils may need calibration (e.g. Cerrado oxisols); compare with
    field data or with the Brazilian IN SPA/MAPA nº 1/2022 (`zarc_available_water_mm_per_cm`).
    """
    sand, clay, organic_matter = (
        texture.sand_pct / 100,
        texture.clay_pct / 100,
        texture.organic_matter_pct,
    )
    wilting_first = (
        -0.024 * sand
        + 0.487 * clay
        + 0.006 * organic_matter
        + 0.005 * sand * organic_matter
        - 0.013 * clay * organic_matter
        + 0.068 * sand * clay
        + 0.031
    )
    field_first = (
        -0.251 * sand
        + 0.195 * clay
        + 0.011 * organic_matter
        + 0.006 * sand * organic_matter
        - 0.027 * clay * organic_matter
        + 0.452 * sand * clay
        + 0.299
    )
    return SoilWaterLimits(
        field_capacity=field_first + (1.283 * field_first**2 - 0.374 * field_first - 0.015),
        wilting_point=wilting_first + (0.14 * wilting_first - 0.02),
    )


def zarc_available_water_mm_per_cm(sand_pct: float, silt_pct: float, clay_pct: float) -> float:
    """Brazilian ZARC available water (mm/cm), IN SPA/MAPA nº 1/2022, art. 3º (0–40 cm layer).

    Local reference, used only to validate the global estimate; the equation is copied verbatim.
    """
    sand, silt, clay = sand_pct, silt_pct, clay_pct
    inner = (
        (-0.02128887 * sand)
        + (-0.01005814 * silt)
        + (-0.01901894 * clay)
        + (0.0001171219 * sand * silt)
        + (0.0002073924 * sand * clay)
        + (0.00006118707 * silt * clay)
        + (-0.000006373789 * sand * silt * clay)
    )
    return (1 + 0.3591 * inner) ** 2.78474 * 10


# IN SPA/MAPA nº 1/2022, art. 2º (wording by IN SPA/MAPA nº 2/2022): lower bound of each class.
ZARC_AD_CLASS_LOWER_BOUNDS_MM_PER_CM: tuple[tuple[str, float], ...] = (
    ("AD1", 0.34),
    ("AD2", 0.46),
    ("AD3", 0.61),
    ("AD4", 0.80),
    ("AD5", 1.06),
    ("AD6", 1.40),
)


def zarc_ad_class(available_water_mm_per_cm: float) -> str | None:
    """ZARC class AD1–AD6, or None below the AD1 lower bound."""
    selected = None
    for name, lower_bound in ZARC_AD_CLASS_LOWER_BOUNDS_MM_PER_CM:
        if available_water_mm_per_cm >= lower_bound:
            selected = name
    return selected


def texture_class(texture: SoilTexture) -> SoilType:
    """Coarse texture class, used only to pick the FAO-56 Table 19 surface evaporation limits.

    sandy ≈ sand and loamy sand; clayey ≈ clay, silty clay and sandy clay; medium = the rest.
    TODO(validate): simplified USDA texture triangle (thresholds below), not the full triangle.
    """
    if texture.sand_pct >= 70 and texture.clay_pct < 15:
        return SoilType.SANDY
    if texture.clay_pct >= 40 or (texture.clay_pct >= 35 and texture.sand_pct >= 45):
        return SoilType.CLAYEY
    return SoilType.MEDIUM


@dataclass(frozen=True)
class SoilProfile:
    """Soil parameters used by the water balance."""

    texture_class: SoilType
    available_water_mm_per_m: float

    @property
    def surface_evaporation(self) -> SurfaceEvaporationLimits:
        return SURFACE_EVAPORATION_LIMITS[self.texture_class]

    def available_water_mm(self, root_depth_m: float) -> float:
        if root_depth_m < 0:
            raise ValueError(f"root_depth_m must be >= 0, got {root_depth_m}")
        return self.available_water_mm_per_m * root_depth_m

    @classmethod
    def from_texture_class(cls, soil_type: SoilType | str) -> "SoilProfile":
        soil_type = SoilType(soil_type)
        return cls(soil_type, AVAILABLE_WATER_MM_PER_M[soil_type])

    @classmethod
    def from_texture(cls, texture: SoilTexture) -> "SoilProfile":
        return cls(texture_class(texture), saxton_rawls_2006(texture).available_water_mm_per_m)


def as_soil_profile(soil: "SoilProfile | SoilType | str") -> SoilProfile:
    return soil if isinstance(soil, SoilProfile) else SoilProfile.from_texture_class(soil)


def calculate_available_water_capacity(soil_type: SoilType | str, root_depth_m: float) -> float:
    """Total available water (mm) = soil available water (mm/m) × effective root depth (m)."""
    # StrEnum members hash like their values, so plain strings hit the dict without conversion
    # (this runs once per simulated day).
    available_water_mm_per_m = AVAILABLE_WATER_MM_PER_M.get(soil_type)
    if available_water_mm_per_m is None:
        valid = ", ".join(SoilType)
        raise ValueError(f"Unknown soil type {soil_type!r}; expected one of: {valid}")
    if root_depth_m < 0:
        raise ValueError(f"root_depth_m must be >= 0, got {root_depth_m}")
    return available_water_mm_per_m * root_depth_m
