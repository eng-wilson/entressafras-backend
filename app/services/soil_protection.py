"""Planned soil protection of a rotation: a planning index, not a soil health measurement.

S = COVER_WEIGHT × C + DIVERSITY_WEIGHT × D, where C is the share of the season's days with a
living crop planned and D is the share of crop transitions that change botanical family. Living
cover and crop diversity are soil conservation practices recognized by USDA NRCS (Conservation
Practice Standards 340 "Cover Crop" and 328 "Conservation Crop Rotation"). It does not estimate
current soil health (organic matter, pH, bulk density, biology) nor any change in organic matter.
Pure calculation, no I/O.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from itertools import pairwise

from app.schemas.rotation import SoilProtection, SoilProtectionWeights

# TODO(validate): heuristic MVP weights, not validated coefficients.
COVER_WEIGHT = 70
DIVERSITY_WEIGHT = 30
METHOD = "PLANNED_COVER_DIVERSITY_V1"

# EcoCrop uses conserved family names; show the current (APG) ones.
FAMILY_ALIASES = {
    "Leguminosae": "Fabaceae",
    "Gramineae": "Poaceae",
    "Compositae": "Asteraceae",
    "Cruciferae": "Brassicaceae",
    "Umbelliferae": "Apiaceae",
    "Labiatae": "Lamiaceae",
    "Palmae": "Arecaceae",
    "Guttiferae": "Clusiaceae",
}


def botanical_family(family: str) -> str:
    """Family name from an EcoCrop FAMNAME ("Magnoliopsida:Rosidae:Fabales:Leguminosae")."""
    name = family.rsplit(":", 1)[-1].strip()
    return FAMILY_ALIASES.get(name, name)


def living_cover_days(
    periods: Sequence[tuple[date, date]], season_start: date, season_end: date
) -> int:
    """Days of the season covered by at least one planting→harvest period (both inclusive)."""
    covered: set[date] = set()
    for planting, harvest in periods:
        day = max(planting, season_start)
        last = min(harvest, season_end)
        while day <= last:
            covered.add(day)
            day += timedelta(days=1)
    return len(covered)


def protection_value(cover_fraction: float, change_fraction: float) -> float:
    """Unrounded index (0–100), to rank without the ties of the rounded `score`."""
    return COVER_WEIGHT * cover_fraction + DIVERSITY_WEIGHT * change_fraction


def exact_value(protection: SoilProtection) -> float:
    """Unrounded index of a `soil_protection` result, from its exact day and transition counts."""
    change_fraction = (
        protection.family_changes / protection.transitions if protection.transitions else 0.0
    )
    return protection_value(protection.living_cover_days / protection.season_days, change_fraction)


def soil_protection(
    planned: Sequence[tuple[date, date, str]],
    history_families: Sequence[str],
    season_start: date,
    season_end: date,
) -> SoilProtection:
    """Index of the planned crops (planting, harvest, EcoCrop family) in planting order.

    `history_families` are the EcoCrop families of the farmer's previous crops, oldest first;
    they only add transitions to D (the cover share C is about the planned season only).
    """
    season_days = (season_end - season_start).days + 1
    cover_days = living_cover_days(
        [(planting, harvest) for planting, harvest, _ in planned], season_start, season_end
    )
    cover_fraction = cover_days / season_days
    sequence = [botanical_family(family) for family in history_families]
    sequence += [botanical_family(family) for _, _, family in planned]
    transitions = len(sequence) - 1
    changes = sum(before != after for before, after in pairwise(sequence))
    change_fraction = changes / transitions if transitions else 0.0
    return SoilProtection(
        score=round(protection_value(cover_fraction, change_fraction)),
        living_cover_days=cover_days,
        season_days=season_days,
        cover_fraction=round(cover_fraction, 3),
        family_change_fraction=round(change_fraction, 3),
        transitions=transitions,
        family_changes=changes,
        botanical_families=list(dict.fromkeys(sequence)),
        includes_history=bool(history_families),
        weights=SoilProtectionWeights(cover=COVER_WEIGHT, diversity=DIVERSITY_WEIGHT),
        method=METHOD,
    )
