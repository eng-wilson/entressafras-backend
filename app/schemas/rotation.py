"""Contract of POST /rotations/recommendations.

Risk is reported per crop only: there is deliberately no combined/rotation-level risk field.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.crop import CropDetail
from app.services.soil import SoilType

RiskMethod = Literal["MOCK", "FAO56_SIMPLIFIED_KY"]
Stage = Literal["initial", "development", "mid_season", "late_season"]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, description="Latitude, decimal degrees")
    lon: float = Field(ge=-180, le=180, description="Longitude, decimal degrees")


class RotationRequest(BaseModel):
    soil_type: SoilType
    location: Location
    agricultural_year: int | None = Field(
        None,
        ge=2000,
        le=2100,
        description="Season start year (2027 = Sep/2027–Aug/2028); default: next season",
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
    validated: Literal[False] = False


class CropWithRisk(CropDetail):
    """Full crop detail (same as GET /crops/{code}) plus its individual water risk."""

    risk: CropRisk


class RotationCrop(BaseModel):
    crop: CropWithRisk
    planting_date: date
    harvest_date: date


class Rotation(BaseModel):
    id: str
    rank: int = Field(ge=1, le=4)
    crops: list[RotationCrop] = Field(min_length=2, max_length=2, description="In planting order")


class RotationAssumptions(BaseModel):
    loss_threshold_pct: float
    initial_soil_water_fraction: float = Field(description="Of available water capacity")
    spinup_days: int = Field(description="Bare-soil days simulated before the first planting")
    planting_gap_days: int = Field(description="Days between harvest of crop A and planting of B")


class RotationRecommendationsResponse(BaseModel):
    agricultural_year: int
    soil_type: SoilType
    rotations: list[Rotation] = Field(max_length=4)
    assumptions: RotationAssumptions
    warnings: list[str]
