"""Two-crop rotation recommendations.

For now the ranking is MOCKED (fixed sequences and risk values) so the frontend can integrate
against the final contract. Crop data and planting/harvest dates are real: crops come from the
EcoCrop + CROPWAT repository and dates follow each crop's FAO-56 cycle length.
"""

from datetime import date, timedelta

from app.schemas.crop import CropDetail
from app.schemas.rotation import (
    CropRisk,
    CropWithRisk,
    MeanIsna,
    Rotation,
    RotationAssumptions,
    RotationCrop,
    RotationRecommendationsResponse,
    RotationRequest,
)
from app.services.ecocrop import CropRepository

LOSS_THRESHOLD_PCT = 20.0
INITIAL_SOIL_WATER_FRACTION = 0.2
SPINUP_DAYS = 30
PLANTING_GAP_DAYS = 5
MAX_ROTATIONS = 4
SEASON_START = (9, 1)  # month, day
MOCK_PLANTING_A = (10, 15)

SOYBEAN, MAIZE, SORGHUM, MILLET, COTTON = 1150, 2175, 48747, 8418, 1160
MOCK_SEQUENCES: list[tuple[int, int]] = [
    (SOYBEAN, MAIZE),
    (SOYBEAN, SORGHUM),
    (SOYBEAN, MILLET),
    (SOYBEAN, COTTON),
]

# Placeholder values, only to exercise the contract. (risk %, loss %, critical stage, ISNA by stage)
_MOCK_RISK: dict[int, tuple[float, float, str, tuple[float, float, float, float]]] = {
    SOYBEAN: (10.0, 6.0, "mid_season", (0.95, 0.9, 0.84, 0.8)),
    MAIZE: (30.0, 14.5, "mid_season", (0.9, 0.78, 0.66, 0.6)),
    SORGHUM: (20.0, 9.0, "mid_season", (0.92, 0.82, 0.72, 0.65)),
    MILLET: (20.0, 8.0, "development", (0.9, 0.7, 0.75, 0.7)),
    COTTON: (40.0, 18.0, "mid_season", (0.95, 0.8, 0.6, 0.5)),
}

MOCK_WARNING = (
    "MOCK: ranking and risk values are placeholders for contract integration; "
    "they are not computed from climate data."
)
CYCLE_WARNING = (
    "Crop cycle lengths are FAO-56 reference values, not calibrated for Brazilian cultivars; "
    "harvest dates may be earlier than in practice."
)


def next_agricultural_year(today: date) -> int:
    """Start year of the next season to begin (seasons run Sep 1 – Aug 31)."""
    return today.year if today < date(today.year, *SEASON_START) else today.year + 1


def harvest_date(planting: date, crop: CropDetail) -> date:
    """Last day of the crop cycle (planting day counts as day 1)."""
    return planting + timedelta(days=_cycle_days(crop) - 1)


def _cycle_days(crop: CropDetail) -> int:
    if crop.water_requirements is None:
        raise ValueError(f"crop {crop.eco_port_code} has no water requirements")
    return crop.water_requirements.stage_length_days.total


def _mock_risk(code: int) -> CropRisk:
    risk_pct, loss, stage, isna = _MOCK_RISK[code]
    return CropRisk(
        risk_percentage=risk_pct,
        estimated_yield_loss_pct=loss,
        loss_threshold_pct=LOSS_THRESHOLD_PCT,
        critical_stage=stage,
        mean_isna=MeanIsna(
            initial=isna[0], development=isna[1], mid_season=isna[2], late_season=isna[3]
        ),
        valid_years=10,
        method="MOCK",
    )


def _rotation_crop(crop: CropDetail, risk: CropRisk, planting: date) -> RotationCrop:
    return RotationCrop(
        crop=CropWithRisk(**crop.model_dump(), risk=risk),
        planting_date=planting,
        harvest_date=harvest_date(planting, crop),
    )


def rank_key(a: CropRisk, b: CropRisk, planting: date, codes: tuple[int, int]) -> tuple:
    """Lowest worst individual risk first; unknown risk sorts last. No combined risk is exposed."""
    risks = [r.risk_percentage for r in (a, b)]
    losses = [r.estimated_yield_loss_pct for r in (a, b)]
    if None in risks or None in losses:
        return (1, 0.0, 0.0, 0.0, planting, codes)
    return (0, max(risks), max(losses), sum(losses), planting, codes)


def mock_recommendations(
    repo: CropRepository, request: RotationRequest, today: date
) -> RotationRecommendationsResponse:
    year = request.agricultural_year or next_agricultural_year(today)
    season_end = date(year + 1, 8, 31)
    candidates = []
    for code_a, code_b in MOCK_SEQUENCES:
        crop_a, crop_b = repo.get(code_a), repo.get(code_b)
        if crop_a is None or crop_b is None or crop_a.family == crop_b.family:
            continue
        risk_a, risk_b = _mock_risk(code_a), _mock_risk(code_b)
        a = _rotation_crop(crop_a, risk_a, date(year, *MOCK_PLANTING_A))
        b = _rotation_crop(crop_b, risk_b, a.harvest_date + timedelta(days=PLANTING_GAP_DAYS + 1))
        if b.harvest_date > season_end:
            continue
        key = rank_key(risk_a, risk_b, a.planting_date, (code_a, code_b))
        candidates.append((key, a, b))

    candidates.sort(key=lambda item: item[0])
    rotations = [
        Rotation(
            id=f"{a.crop.eco_port_code}-{b.crop.eco_port_code}-{a.planting_date:%Y-%m-%d}",
            rank=rank,
            crops=[a, b],
        )
        for rank, (_, a, b) in enumerate(candidates[:MAX_ROTATIONS], start=1)
    ]
    return RotationRecommendationsResponse(
        agricultural_year=year,
        soil_type=request.soil_type,
        rotations=rotations,
        assumptions=RotationAssumptions(
            loss_threshold_pct=LOSS_THRESHOLD_PCT,
            initial_soil_water_fraction=INITIAL_SOIL_WATER_FRACTION,
            spinup_days=SPINUP_DAYS,
            planting_gap_days=PLANTING_GAP_DAYS,
        ),
        warnings=[MOCK_WARNING, CYCLE_WARNING],
    )
