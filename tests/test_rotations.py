"""Contract tests for POST /api/rotations/recommendations (mocked ranking, real crop data)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.routes.rotations import get_today
from app.main import app
from app.services.rotations import next_agricultural_year

URL = "/api/rotations/recommendations"
TODAY = date(2026, 11, 14)
BODY = {"soil_type": "medium", "location": {"lat": -12.55, "lon": -55.72}}
RISK_KEYS = {
    "risk_percentage",
    "estimated_yield_loss_pct",
    "loss_threshold_pct",
    "critical_stage",
    "mean_isna",
    "valid_years",
    "method",
    "validated",
}
STAGES = {"initial", "development", "mid_season", "late_season"}


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_today] = lambda: TODAY
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def body(client: TestClient) -> dict:
    resp = client.post(URL, json={**BODY, "agricultural_year": 2027})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _crops(body: dict) -> list[dict]:
    return [rc["crop"] for r in body["rotations"] for rc in r["crops"]]


def test_returns_up_to_four_ranked_rotations_with_two_crops(body: dict) -> None:
    rotations = body["rotations"]
    assert 1 <= len(rotations) <= 4
    assert [r["rank"] for r in rotations] == list(range(1, len(rotations) + 1))
    assert all(len(r["crops"]) == 2 for r in rotations)
    assert len({r["id"] for r in rotations}) == len(rotations)


def test_echoes_request_and_top_level_fields(body: dict) -> None:
    assert body["agricultural_year"] == 2027
    assert body["soil_type"] == "medium"
    assert body["warnings"]
    assert set(body["assumptions"]) >= {
        "loss_threshold_pct",
        "initial_soil_water_fraction",
        "planting_gap_days",
    }


def test_each_crop_has_full_crop_detail_plus_risk(client: TestClient, body: dict) -> None:
    for crop in _crops(body):
        detail = client.get(f"/api/crops/{crop['eco_port_code']}").json()
        assert {k: v for k, v in crop.items() if k != "risk"} == detail
        assert crop["water_requirements"] is not None
        assert set(crop["risk"]) == RISK_KEYS
        assert set(crop["risk"]["mean_isna"]) == STAGES
        assert crop["risk"]["validated"] is False


def test_crop_b_is_planted_after_harvest_of_a_within_the_season(body: dict) -> None:
    start, end = date(2027, 9, 1), date(2028, 8, 31)
    for rotation in body["rotations"]:
        a, b = rotation["crops"]
        a_plant, a_harvest = (
            date.fromisoformat(a["planting_date"]),
            date.fromisoformat(a["harvest_date"]),
        )
        b_plant, b_harvest = (
            date.fromisoformat(b["planting_date"]),
            date.fromisoformat(b["harvest_date"]),
        )
        assert start <= a_plant < a_harvest < b_plant < b_harvest <= end
        cycle_a = a["crop"]["water_requirements"]["stage_length_days"]["total"]
        assert (a_harvest - a_plant).days == cycle_a - 1
        gap = body["assumptions"]["planting_gap_days"]
        assert (b_plant - a_harvest).days - 1 == gap  # free days between harvest and planting


def test_rotation_crops_belong_to_different_families(body: dict) -> None:
    for rotation in body["rotations"]:
        a, b = (rc["crop"]["family"] for rc in rotation["crops"])
        assert a and b and a != b


def test_no_combined_risk_anywhere(body: dict) -> None:
    for rotation in body["rotations"]:
        assert set(rotation) == {"id", "rank", "crops"}
    assert not any("risk" in key for key in body)


def test_default_agricultural_year_is_next_season(client: TestClient) -> None:
    resp = client.post(URL, json=BODY)
    assert resp.status_code == 200
    assert resp.json()["agricultural_year"] == 2027


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 8, 31), 2026), (date(2026, 9, 1), 2027), (date(2026, 11, 14), 2027)],
)
def test_next_agricultural_year(today: date, expected: int) -> None:
    assert next_agricultural_year(today) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"location": BODY["location"]},
        {**BODY, "soil_type": "loamy"},
        {"soil_type": "medium"},
        {**BODY, "location": {"lat": -95, "lon": 0}},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, payload: dict) -> None:
    assert client.post(URL, json=payload).status_code == 422
