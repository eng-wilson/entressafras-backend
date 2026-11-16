"""Contract of POST /rotations/recommendations and POST /rotations/simulate.

Risk is reported per crop only: there is deliberately no combined/rotation-level risk field.
Rotation `scores` are 0–100 indicators (higher is better) used to rank by the client priorities.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.clima import ClimateScenarioInfo
from app.schemas.crop import CropDetail, CropEconomics
from app.services.climate_scenario import ScenarioId
from app.services.soil import SoilType

RiskMethod = Literal["FAO56_SIMPLIFIED_KY"]
CalendarSource = Literal["GGCMI_PHASE3_V1_01", "FAO56_REFERENCE"]
SoilSource = Literal["SOILGRIDS_SAXTON_RAWLS_2006", "REQUEST_TEXTURE_CLASS"]
SoilProtectionMethod = Literal["PLANNED_COVER_DIVERSITY_V1"]
Stage = Literal["initial", "development", "mid_season", "late_season"]
ScoreAggregation = Literal["WORST_CROP"]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, description="Latitude, decimal degrees")
    lon: float = Field(ge=-180, le=180, description="Longitude, decimal degrees")


class Priorities(BaseModel):
    """Ranking priorities: 1 turns an indicator on, 0 turns it off.

    The three agronomic priorities are on by default; revenue is off.

    With every priority off, rotations are ranked by the worse individual water risk.
    """

    productivity: int = Field(
        1, ge=0, le=1, description="Rank by preserved yield (100 − water-deficit loss, FAO Ky)"
    )
    water_security: int = Field(
        1, ge=0, le=1, description="Rank by the share of seasons without a critical water loss"
    )
    soil_protection: int = Field(
        1, ge=0, le=1, description="Rank by planned soil cover and crop family diversity"
    )

    revenue: int = Field(
        0,
        ge=0,
        le=1,
        description=(
            "Rank by water-adjusted reference gross revenue (FAOSTAT price × yield × "
            "(1 − site water-deficit loss)); off by default"
        ),
    )

    def enabled(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value]


class CustomCropInput(BaseModel):
    """One crop of a client-defined rotation. Omitted dates follow the regional calendar."""

    code: int = Field(description="EcoCrop code (see GET /crops); needs FAO-56 water data")
    planting_date: date | None = Field(
        None,
        description=(
            "First crop: default is the regional calendar date (or Oct 1 without one). "
            "Second crop: default is the standard gap after the harvest of the first."
        ),
    )
    harvest_date: date | None = Field(
        None,
        description=(
            "Last day of the cycle; sets the cycle length (cultivar maturity). Default: the "
            "regional cycle length. Must stay within 50–150% of it."
        ),
    )


class CustomRotationInput(BaseModel):
    """A two-crop rotation proposed by the client, to be simulated and ranked."""

    crops: list[CustomCropInput] = Field(
        min_length=1,
        max_length=2,
        description="1 or 2 crops, in planting order (one crop leaves the season fallow after it)",
    )


class RotationContext(BaseModel):
    """Fields shared by the recommendation and the simulation requests."""

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
    priorities: Priorities = Field(
        default_factory=Priorities,
        description=(
            "Indicators that rank the rotations (0 = off, 1 = on); the three agronomic ones are "
            "on by default, revenue is off"
        ),
    )
    climate_scenario: ScenarioId = Field(
        "historical",
        description=(
            "Climate the seasons are simulated with: the observed record (default) or a future "
            "scenario applied to it by the delta method (see GET /clima/scenarios)"
        ),
    )


class RotationRequest(RotationContext):
    custom_rotations: list[CustomRotationInput] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Client-defined rotations to simulate and place in the same ranking. They are "
            "returned in `custom_rotations` and never change the recommended `rotations`."
        ),
    )


class RotationSimulationRequest(RotationContext):
    rotation: CustomRotationInput
    sweep: bool = Field(
        True,
        description=(
            "Also simulate the planting date of each crop on every decêndio of the season "
            "(about 1 s). Send false to evaluate only the given dates (fast, e.g. while dragging)."
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
    mean_season_isna: float | None = Field(
        None,
        description=(
            "Water satisfaction index of the whole cycle (ΣETr/ΣETc), averaged across seasons"
        ),
    )
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
    economics: CropEconomics | None = Field(
        None,
        description=(
            "National reference farm-gate price, yield and gross revenue per hectare (FAOSTAT); "
            "an estimate, not a forecast for this location. Null when there is no data"
        ),
    )


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


class RotationScores(BaseModel):
    """Absolute 0–100 scales (0 = worst possible, 100 = best possible), for charts.

    Not relative to the other rotations and independent of `priorities`, which only set the
    order. productivity and water_security take the worse of the two crops; null when its risk
    is unknown.
    """

    productivity: float | None = Field(
        description=(
            "Preserved yield, 100 − estimated_yield_loss_pct: 0 = total loss from water deficit, "
            "100 = no water-limited loss (ideal moisture)"
        )
    )
    water_security: float | None = Field(
        description=(
            "Seasons without a critical loss, 100 − risk_percentage: 0 = every season lost more "
            "than loss_threshold_pct, 100 = none did"
        )
    )
    soil_protection: float = Field(
        description=(
            "Unrounded soil_protection index 70C + 30D: 0 = bare soil all season and no family "
            "change, 100 = living crop every day and every transition changes family"
        )
    )
    revenue: float | None = Field(
        description=(
            "Σ crop reference gross revenue × (1 − estimated_yield_loss_pct/100), over the "
            "highest gross revenue among the viable rotations at this site × 100: 0 = no "
            "revenue, 100 = the best viable rotation with no water-limited loss (capped at 100). "
            "Null when a crop has no economics, an unknown risk, or the site has no viable "
            "rotation"
        )
    )
    overall: float | None = Field(
        description=(
            "Mean of productivity, water_security and soil_protection (revenue is not included); "
            "independent of the priorities"
        )
    )
    aggregation: ScoreAggregation = "WORST_CROP"


class Rotation(BaseModel):
    id: str
    rank: int = Field(ge=1, le=4)
    crops: list[RotationCrop] = Field(min_length=2, max_length=2, description="In planting order")
    soil_protection: SoilProtection
    scores: RotationScores
    gross_revenue_usd_ha: float | None = Field(
        None,
        description=(
            "Sum of the crops' reference gross revenue (USD/ha) in the agricultural year; no "
            "production costs. Null when a crop has no economics"
        ),
    )
    water_adjusted_revenue_usd_ha: float | None = Field(
        None,
        description=(
            "gross_revenue_usd_ha with each crop discounted by its estimated_yield_loss_pct; the "
            "value behind scores.revenue. Null when a crop has no economics or an unknown risk"
        ),
    )


class CustomRotation(BaseModel):
    """A client-defined rotation, with the position it would take in the ranking.

    Unlike the recommended rotations it is not filtered: problems such as same botanical family,
    temperature or a planting date far from the regional calendar are listed in `checks`.
    """

    id: str
    rank: int = Field(
        ge=1,
        description=(
            "Position among the recommended rotations (best planting date of each crop pair) "
            "under the request priorities; 1 = better than all of them"
        ),
    )
    ranked_among: int = Field(description="Rotations compared, this one included")
    would_be_recommended: bool = Field(description="rank is within the top 4")
    crops: list[RotationCrop] = Field(
        min_length=1, max_length=2, description="1 or 2 crops, in planting order"
    )
    soil_protection: SoilProtection
    scores: RotationScores
    gross_revenue_usd_ha: float | None = Field(
        None,
        description=(
            "Sum of the crops' reference gross revenue (USD/ha) in the agricultural year; no "
            "production costs. Null when a crop has no economics"
        ),
    )
    water_adjusted_revenue_usd_ha: float | None = Field(
        None,
        description=(
            "gross_revenue_usd_ha with each crop discounted by its estimated_yield_loss_pct; the "
            "value behind scores.revenue. Null when a crop has no economics or an unknown risk"
        ),
    )
    checks: list[str] = Field(
        description="Agronomic issues found (empty = passes the recommendation filters)"
    )


class SweepCrop(BaseModel):
    code: int
    planting_date: date
    harvest_date: date
    risk_percentage: float | None
    estimated_yield_loss_pct: float | None
    in_calendar: bool = Field(
        description="Planting within planting_tolerance_days of the regional calendar date"
    )
    temperature_ok: bool = Field(description="Passes the EcoCrop temperature filter")


class SweepPoint(BaseModel):
    decendio_idx: int = Field(description="0 = 1–10 Sep … 35 = 21–31 Aug")
    label: str
    crops: list[SweepCrop] = Field(
        min_length=1, max_length=2, description="1 or 2 crops, in planting order"
    )
    scores: RotationScores


class PlantingSweep(BaseModel):
    """The same rotation with one planting date moved to the start of each decêndio.

    Cycle lengths stay as requested. Only decêndios whose rotation ends by Aug 31 are listed.
    """

    moved_crop: Literal["first", "second"] = Field(
        description=(
            "first: only the first crop moves (the second keeps its dates); second: the first "
            "crop is fixed and only the second one moves"
        )
    )
    points: list[SweepPoint]
    best_decendio_idx: int | None = Field(description="Best point by the request priorities")
    worst_decendio_idx: int | None


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
    climate_scenario: ClimateScenarioInfo
    soil_type: SoilType
    soil: SoilInfo
    soil_properties: SoilProperties | None = Field(
        None, description="Estimated soil properties (separate panel); null when unavailable"
    )
    priorities: Priorities = Field(description="Priorities used in the ranking")
    rotations: list[Rotation] = Field(max_length=4)
    custom_rotations: list[CustomRotation] = Field(
        default_factory=list, description="The request custom_rotations, simulated and ranked"
    )
    assumptions: RotationAssumptions
    warnings: list[str]


class RotationSimulationResponse(BaseModel):
    agricultural_year: int
    climate_scenario: ClimateScenarioInfo
    soil_type: SoilType
    soil: SoilInfo
    priorities: Priorities
    rotation: CustomRotation
    first_planting_sweep: PlantingSweep | None = Field(None, description="Null when sweep is false")
    second_planting_sweep: PlantingSweep | None = Field(
        None, description="Null when sweep is false or the rotation has a single crop"
    )
    assumptions: RotationAssumptions
    warnings: list[str]
