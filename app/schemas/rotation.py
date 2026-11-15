"""Contract of POST /rotations/recommendations.

Risk is reported per crop only: there is deliberately no combined/rotation-level risk field.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.crop import CropDetail
from app.services.soil import SoilType

RiskMethod = Literal["FAO56_SIMPLIFIED_KY"]
CalendarSource = Literal["GGCMI_PHASE3_V1_01", "FAO56_REFERENCE"]
SoilSource = Literal["SOILGRIDS_SAXTON_RAWLS_2006", "REQUEST_TEXTURE_CLASS"]
SoilProtectionMethod = Literal["PLANNED_COVER_DIVERSITY_V1"]
Stage = Literal["initial", "development", "mid_season", "late_season"]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, description="Latitude, decimal degrees")
    lon: float = Field(ge=-180, le=180, description="Longitude, decimal degrees")


class RotationRequest(BaseModel):
    soil_type: SoilType | None = Field(
        None,
        description=(
            "Optional texture class. When omitted, the soil is estimated from the location "
            "(SoilGrids texture + Saxton & Rawls 2006 pedotransfer function)."
        ),
    )
    # TODO(pending): read the location from the farm profile once onboarding persists it
    # (follow-up 8); until then it is required in the request.
    location: Location
    agricultural_year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description="Season start year (2027 = Sep/2027–Aug/2028); default: next season",
    )
    crop_history: list[int] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "EcoCrop codes of the farmer's previous crops, oldest first (e.g. the last 2–3 "
            "seasons). Only used by the soil protection index (crop family transitions)."
        ),
    )


class MeanIsna(BaseModel):
    """Water satisfaction index (ΣETr/ΣETc) per stage, averaged across historical seasons."""

    initial: float | None
    development: float | None
    mid_season: float | None
    late_season: float | None


class CropRisk(BaseModel):
    risk_percentage: float | None = Field(
        description=(
            "Share of simulated historical seasons (%) whose estimated water-deficit yield loss "
            "exceeded loss_threshold_pct. Not a yield reduction."
        )
    )
    estimated_yield_loss_pct: float | None = Field(
        description=(
            "Mean relative yield loss caused by water deficit, Ky × (1 − ETr/ETc) (FAO-33). "
            "Not the total expected loss."
        )
    )
    loss_threshold_pct: float = Field(description="Loss above which a season counts as critical")
    critical_stage: Stage | None = Field(description="Stage with the largest Ky × deficit")
    mean_isna: MeanIsna
    valid_years: int = Field(description="Historical seasons used")
    method: RiskMethod
    # TODO(validate): compare with the official ZARC zoning for soybean and maize in MT before
    # this can become true (follow-up 9).
    validated: Literal[False] = False


class CropWithRisk(CropDetail):
    """Full crop detail (same as GET /crops/{code}) plus its individual water risk."""

    risk: CropRisk


class CropCalendarInfo(BaseModel):
    """Where the planting date window and the cycle length of the crop came from."""

    source: CalendarSource
    cycle_days: int = Field(description="Planting to harvest; FAO-56 stages scaled to it")
    reference_planting_date: date | None = Field(
        None, description="Regional planting date of the calendar in this agricultural year"
    )
    data_source: str | None = Field(
        None, description="Statistics behind the GGCMI cell (e.g. 'Brazil CONAB', 'MIRCA2000')"
    )
    grid_cell: Location | None = Field(None, description="Center of the 0.5° calendar cell")


class RotationCrop(BaseModel):
    crop: CropWithRisk
    planting_date: date
    harvest_date: date
    calendar: CropCalendarInfo


class SoilProtectionWeights(BaseModel):
    cover: int
    diversity: int


class SoilProtection(BaseModel):
    """Planned soil protection: a planning index, not a measurement of soil health.

    score = weights.cover × cover_fraction + weights.diversity × family_change_fraction. It does
    not forecast organic matter. Informational only: rotations are ranked by water risk.
    """

    score: int = Field(ge=0, le=100)
    living_cover_days: int = Field(description="Days of the season with a planned living crop")
    season_days: int = Field(description="Days from Sep 1 to Aug 31 of the agricultural year")
    cover_fraction: float = Field(ge=0, le=1, description="living_cover_days / season_days")
    family_change_fraction: float = Field(
        ge=0, le=1, description="Share of crop transitions that change botanical family"
    )
    transitions: int = Field(description="Crop transitions counted (history included)")
    family_changes: int
    botanical_families: list[str] = Field(
        description="Distinct families in the sequence (history included), in order"
    )
    includes_history: bool = Field(description="Whether crop_history was used")
    weights: SoilProtectionWeights
    method: SoilProtectionMethod
    # TODO(validate): heuristic weights (70/30), not validated coefficients.
    validated: Literal[False] = False


class Rotation(BaseModel):
    id: str
    rank: int = Field(ge=1, le=4)
    crops: list[RotationCrop] = Field(min_length=2, max_length=2, description="In planting order")
    soil_protection: SoilProtection


class RotationAssumptions(BaseModel):
    loss_threshold_pct: float
    initial_soil_water_fraction: float = Field(description="Of available water capacity")
    spinup_days: int = Field(description="Bare-soil days simulated before the first planting")
    planting_gap_days: int = Field(description="Days between harvest of crop A and planting of B")
    planting_tolerance_days: int = Field(
        description="Maximum distance of a planting date from the regional calendar date"
    )


class SoilInfo(BaseModel):
    """Soil used in the simulation and where it came from."""

    source: SoilSource
    texture_class: SoilType = Field(description="Picks the FAO-56 surface evaporation limits")
    available_water_mm_per_m: float = Field(
        description="Field capacity minus wilting point, per meter of root depth"
    )
    sand_pct: float | None = Field(None, description="0–100 cm, depth-weighted")
    silt_pct: float | None = None
    clay_pct: float | None = None
    organic_matter_pct: float | None = None
    field_capacity: float | None = Field(None, description="m³/m³ at −33 kPa")
    wilting_point: float | None = Field(None, description="m³/m³ at −1500 kPa")
    sampled_location: Location | None = Field(
        None, description="Point actually read; may differ when the location has no soil data"
    )
    distance_km: float | None = Field(None, description="From the requested location")


class SoilProperties(BaseModel):
    """Topsoil properties estimated from the location (SoilGrids), for display only.

    250 m machine-learning maps (predicted mean); they do not replace a laboratory analysis.
    """

    source: Literal["SOILGRIDS"] = "SOILGRIDS"
    depth: Literal["0-30cm"] = "0-30cm"
    organic_carbon_g_per_kg: float = Field(description="Soil organic carbon, depth-weighted")
    sand_pct: float
    silt_pct: float
    clay_pct: float
    texture_class: SoilType
    sampled_location: Location
    distance_km: float = Field(description="From the requested location")


class RotationRecommendationsResponse(BaseModel):
    agricultural_year: int
    soil_type: SoilType
    soil: SoilInfo
    soil_properties: SoilProperties | None = Field(
        None, description="Estimated soil properties (separate panel); null when unavailable"
    )
    rotations: list[Rotation] = Field(max_length=4)
    assumptions: RotationAssumptions
    warnings: list[str]
