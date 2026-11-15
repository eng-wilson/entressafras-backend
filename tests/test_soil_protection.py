"""Soil protection planning index (pure calculation)."""

from datetime import date

import pytest

from app.services.soil_protection import botanical_family, living_cover_days, soil_protection

SOY = "Magnoliopsida:Rosidae:Fabales:Leguminosae"
MAIZE = "Liliopsida:Commelinidae:Cyperales:Gramineae"
COTTON = "Magnoliopsida:Dilleniidae:Malvales:Malvaceae"
START, END = date(2027, 9, 1), date(2028, 8, 31)  # 366 days


def test_botanical_family_uses_last_segment_and_current_names() -> None:
    assert botanical_family(SOY) == "Fabaceae"
    assert botanical_family(MAIZE) == "Poaceae"
    assert botanical_family(COTTON) == "Malvaceae"


def test_living_cover_days_merges_overlaps_and_clips_to_season() -> None:
    periods = [
        (date(2027, 8, 20), date(2027, 9, 10)),  # 10 days inside the season
        (date(2027, 9, 5), date(2027, 9, 14)),  # overlaps: adds Sep 11–14
        (date(2028, 8, 30), date(2028, 9, 10)),  # Aug 30–31
    ]
    assert living_cover_days(periods, START, END) == 14 + 2


def test_two_crops_without_history() -> None:
    result = soil_protection(
        [(date(2027, 10, 1), date(2028, 1, 8), SOY), (date(2028, 1, 15), date(2028, 6, 3), MAIZE)],
        [],
        START,
        END,
    )
    assert result.living_cover_days == 100 + 141
    assert result.season_days == 366
    assert result.family_change_fraction == 1.0
    assert result.transitions == 1
    assert result.botanical_families == ["Fabaceae", "Poaceae"]
    assert result.score == round(70 * 241 / 366 + 30)
    assert result.includes_history is False


def test_history_with_repeated_family_lowers_diversity() -> None:
    result = soil_protection(
        [(date(2027, 10, 1), date(2027, 10, 1), MAIZE), (date(2028, 1, 1), date(2028, 1, 1), SOY)],
        [MAIZE],
        START,
        END,
    )
    assert result.transitions == 2
    assert result.family_changes == 1
    assert result.family_change_fraction == 0.5
    assert result.score == round(70 * 2 / 366 + 15)


@pytest.mark.parametrize(
    ("planned", "expected"),
    [
        ([(START, START, SOY), (END, END, SOY)], 0),  # 2 days, same family
        ([(START, date(2028, 2, 1), SOY), (date(2028, 2, 2), END, MAIZE)], 100),
    ],
)
def test_score_extremes(planned, expected: int) -> None:
    assert soil_protection(planned, [], START, END).score == expected
