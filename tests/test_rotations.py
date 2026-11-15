"""POST /api/rotations/recommendations and the rotation risk/ranking service.

API tests run the real ranking on the Sorriso POWER seed (app/data/power), with no network.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes.clima import get_power_client
from app.api.routes.rotations import get_soilgrids_client, get_today
from app.core.config import get_settings
from app.main import app
from app.schemas.crop import CropDetail
from app.schemas.rotation import CropRisk, Priorities
from app.services import power_client as pc
from app.services import rotations as rotations_service
from app.services.agro import DiaClima
from app.services.crop_calendar import apply_calendar, crop_calendars, scale_stage_lengths
from app.services.ecocrop import get_repository
from app.services.rotations import (
    COMMON_BEAN,
    COWPEA,
    LOSS_THRESHOLD_PCT,
    MAIZE,
    MILLET,
    ROTATION_CROP_CODES,
    ROTATION_SEASONS,
    SORGHUM,
    SOYBEAN,
    RankedRotation,
    crop_risk,
    evaluate_rotation,
    first_planting_dates,
    next_agricultural_year,
    priority_value,
    rank_key,
    rank_rotations,
    rotation_indicators,
    rotation_scores,
    rotation_soil_protection,
    select_rotations,
)
from app.services.soil import (
    SoilType,
    saxton_rawls_2006,
    zarc_available_water_mm_per_cm,
)
from app.services.soilgrids_client import (
    SoilGridsClient,
    SoilGridsNoData,
    SoilGridsUnavailable,
)
from app.services.water_balance import CropSeasonResult

URL = "/api/rotations/recommendations"
TODAY = date(2026, 11, 14)  # the rotation seed covers 1996/97–2025/26 (30 seasons)
SORRISO = {"lat": -12.55, "lon": -55.72}
BODY = {"soil_type": "medium", "location": SORRISO}
OFF = {"productivity": 0, "water_security": 0, "soil_protection": 0}
SCORE_KEYS = {"productivity", "water_security", "soil_protection", "overall", "aggregation"}
RISK_KEYS = {
    "risk_percentage",
    "estimated_yield_loss_pct",
    "loss_threshold_pct",
    "critical_stage",
    "mean_isna",
    "mean_season_isna",
    "valid_years",
    "method",
    "validated",
}
STAGES = {"initial", "development", "mid_season", "late_season"}
SEED_SEASONS = list(range(1996, 2026))


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"test tried to reach the network: {request.url}")


def _seed_soilgrids_client(cache_dir) -> SoilGridsClient:
    settings = get_settings()
    return SoilGridsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_no_network)),
        base_url="https://soilgrids.test",
        seed_dir=settings.soilgrids_seed_dir,
        cache_dir=cache_dir,
        min_interval_s=0.0,
    )


def _seed_power_client(cache_dir) -> pc.PowerClient:
    return pc.PowerClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_no_network)),
        base_url="https://power.test",
        seed_dir=get_settings().power_seed_dir,
        cache_dir=cache_dir,
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> Iterator[TestClient]:
    power = _seed_power_client(tmp_path_factory.mktemp("power"))
    app.dependency_overrides[get_today] = lambda: TODAY
    soilgrids = _seed_soilgrids_client(tmp_path_factory.mktemp("soilgrids"))
    app.dependency_overrides[get_power_client] = lambda: power
    app.dependency_overrides[get_soilgrids_client] = lambda: soilgrids
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def body(client: TestClient) -> dict:
    resp = client.post(URL, json={**BODY, "agricultural_year": 2027})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def sorriso_days(tmp_path_factory) -> list[DiaClima]:
    power = _seed_power_client(tmp_path_factory.mktemp("power"))
    window = pc.janela_safras(TODAY, ROTATION_SEASONS)
    series = asyncio.run(power.serie(SORRISO["lat"], SORRISO["lon"], window))
    return series.dias


@pytest.fixture(scope="module")
def crops() -> dict[int, CropDetail]:
    repository = get_repository()
    return {code: repository.get(code) for code in ROTATION_CROP_CODES}


def _crops(body: dict) -> list[dict]:
    return [rc["crop"] for r in body["rotations"] for rc in r["crops"]]


# --- API contract ---------------------------------------------------------------------------


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
    assert not any("historical seasons" in warning for warning in body["warnings"])
    assert set(body["assumptions"]) >= {
        "loss_threshold_pct",
        "initial_soil_water_fraction",
        "planting_gap_days",
    }


def test_each_crop_has_full_crop_detail_plus_computed_risk(client: TestClient, body: dict) -> None:
    for crop in _crops(body):
        detail = client.get(f"/api/crops/{crop['eco_port_code']}").json()

        # Only the stage lengths differ: they are stretched to the regional calendar cycle.
        def without_stages(item: dict) -> dict:
            requirements = {
                k: v for k, v in item["water_requirements"].items() if k != "stage_length_days"
            }
            return {**item, "water_requirements": requirements}

        assert {k: v for k, v in without_stages(crop).items() if k != "risk"} == without_stages(
            detail
        )
        risk = crop["risk"]
        assert set(risk) == RISK_KEYS
        assert set(risk["mean_isna"]) == STAGES
        assert risk["validated"] is False
        assert risk["method"] == "FAO56_SIMPLIFIED_KY"
        assert risk["valid_years"] == 30
        assert 0 <= risk["risk_percentage"] <= 100
        assert 0 <= risk["estimated_yield_loss_pct"] <= 100


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
        calendars = crop_calendars(SORRISO["lat"], SORRISO["lon"])
        assert a_plant in first_planting_dates(2027, calendars.get(a["crop"]["eco_port_code"]))
        cycle_a = a["crop"]["water_requirements"]["stage_length_days"]["total"]
        assert (a_harvest - a_plant).days == cycle_a - 1 == a["calendar"]["cycle_days"] - 1
        gap = body["assumptions"]["planting_gap_days"]
        assert (b_plant - a_harvest).days - 1 == gap  # free days between harvest and planting


def test_rotation_crops_belong_to_different_families(body: dict) -> None:
    for rotation in body["rotations"]:
        a, b = (rc["crop"]["family"] for rc in rotation["crops"])
        assert a and b and a != b


def test_no_combined_risk_anywhere(body: dict) -> None:
    for rotation in body["rotations"]:
        assert set(rotation) == {"id", "rank", "crops", "soil_protection", "scores"}
        assert not any("risk" in key for key in rotation["soil_protection"])
        assert not any("risk" in key for key in rotation["scores"])
    assert not any("risk" in key for key in body)


# --- Soil protection index and soil properties panel ----------------------------------------


def test_each_rotation_has_a_soil_protection_planning_index(body: dict) -> None:
    for rotation in body["rotations"]:
        protection = rotation["soil_protection"]
        first, second = rotation["crops"]
        cycles = sum(
            (date.fromisoformat(rc["harvest_date"]) - date.fromisoformat(rc["planting_date"])).days
            + 1
            for rc in (first, second)
        )
        assert protection["living_cover_days"] == cycles
        assert protection["season_days"] == 366  # Sep/2027–Aug/2028 has Feb 29
        assert protection["transitions"] == 1
        assert protection["family_change_fraction"] == 1.0
        assert len(protection["botanical_families"]) == 2
        assert protection["includes_history"] is False
        assert protection["validated"] is False
        expected = round(70 * protection["living_cover_days"] / 366 + 30)
        assert protection["score"] == expected
        assert 0 <= protection["score"] <= 100
    assert any("planning index" in warning for warning in body["warnings"])
    assert any("crop_history" in warning for warning in body["warnings"])


def test_crop_history_counts_previous_family_transitions(client: TestClient, body: dict) -> None:
    history = [SOYBEAN, MAIZE, MAIZE]
    resp = client.post(URL, json={**BODY, "agricultural_year": 2027, "crop_history": history})
    assert resp.status_code == 200, resp.text
    with_history = resp.json()
    # Without the soil protection priority, the history does not change the ranking.
    without_soil = {**OFF, "productivity": 1, "water_security": 1}
    ids = [
        [r["id"] for r in _post_history(client, crop_history, without_soil)["rotations"]]
        for crop_history in ([], history)
    ]
    assert ids[0] == ids[1]
    for rotation in with_history["rotations"]:
        protection = rotation["soil_protection"]
        assert protection["includes_history"] is True
        assert protection["transitions"] == 4  # soy→maize→maize→A→B
        assert protection["family_change_fraction"] < 1.0  # maize→maize keeps the family
        assert protection["botanical_families"][:2] == ["Fabaceae", "Poaceae"]
    assert not any("Without crop_history" in w for w in with_history["warnings"])


def _post_history(client: TestClient, crop_history: list[int], priorities: dict) -> dict:
    resp = client.post(
        URL,
        json={
            **BODY,
            "agricultural_year": 2027,
            "crop_history": crop_history,
            "priorities": priorities,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_unknown_crop_history_is_rejected(client: TestClient) -> None:
    resp = client.post(URL, json={**BODY, "crop_history": [999999999]})
    assert resp.status_code == 422
    assert "crop_history" in resp.json()["detail"]


def test_soil_properties_panel_comes_from_soilgrids_topsoil(body: dict) -> None:
    panel = body["soil_properties"]
    assert panel["source"] == "SOILGRIDS"
    assert panel["depth"] == "0-30cm"
    assert panel["sand_pct"] + panel["silt_pct"] + panel["clay_pct"] == pytest.approx(100, abs=0.1)
    assert 0 < panel["organic_carbon_g_per_kg"] < 100
    assert panel["distance_km"] > 0  # Sorriso center is built-up
    assert any("laboratory" in warning for warning in body["warnings"])


def _post(client: TestClient, priorities: dict) -> dict:
    resp = client.post(URL, json={**BODY, "agricultural_year": 2027, "priorities": priorities})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_with_every_priority_off_rotations_are_sorted_by_worst_individual_risk(
    client: TestClient,
) -> None:
    risk_only = _post(client, OFF)
    worst = [
        (
            max(rc["crop"]["risk"]["risk_percentage"] for rc in rotation["crops"]),
            max(rc["crop"]["risk"]["estimated_yield_loss_pct"] for rc in rotation["crops"]),
        )
        for rotation in risk_only["rotations"]
    ]
    assert worst == sorted(worst)
    assert all(rotation["scores"]["overall"] is not None for rotation in risk_only["rotations"])
    assert any("Every priority is off" in warning for warning in risk_only["warnings"])


# --- Priorities -----------------------------------------------------------------------------


def test_priorities_default_to_all_on_and_scores_use_absolute_scales(body: dict) -> None:
    assert body["priorities"] == {"productivity": 1, "water_security": 1, "soil_protection": 1}
    overall = []
    for rotation in body["rotations"]:
        scores = rotation["scores"]
        assert set(scores) == SCORE_KEYS
        assert scores["aggregation"] == "WORST_CROP"
        risks = [rc["crop"]["risk"] for rc in rotation["crops"]]
        assert scores["productivity"] == pytest.approx(
            min(100 - risk["estimated_yield_loss_pct"] for risk in risks), abs=0.05
        )
        assert scores["water_security"] == pytest.approx(
            min(100 - risk["risk_percentage"] for risk in risks), abs=0.05
        )
        assert round(scores["soil_protection"]) == rotation["soil_protection"]["score"]
        mean = (scores["productivity"] + scores["water_security"] + scores["soil_protection"]) / 3
        assert scores["overall"] == pytest.approx(mean, abs=0.1)
        assert all(0 <= scores[key] <= 100 for key in SCORE_KEYS - {"aggregation"})
        overall.append(scores["overall"])
    assert overall == sorted(overall, reverse=True)
    assert any("mean of the enabled priorities" in warning for warning in body["warnings"])


@pytest.mark.parametrize("priority", ["productivity", "water_security", "soil_protection"])
def test_a_single_priority_ranks_by_its_score(client: TestClient, priority: str) -> None:
    result = _post(client, {**OFF, priority: 1})
    scores = [rotation["scores"][priority] for rotation in result["rotations"]]
    assert scores == sorted(scores, reverse=True)
    long_cycle = any("favors long cycles" in warning for warning in result["warnings"])
    assert long_cycle == (priority == "soil_protection")


def test_scores_do_not_depend_on_the_priorities(client: TestClient, body: dict) -> None:
    by_id = {rotation["id"]: rotation["scores"] for rotation in body["rotations"]}
    seen = 0
    for priorities in (OFF, {**OFF, "productivity": 1}, {**OFF, "soil_protection": 1}):
        for rotation in _post(client, priorities)["rotations"]:
            if rotation["id"] in by_id:
                assert rotation["scores"] == by_id[rotation["id"]]
                seen += 1
    assert seen  # some rotation appears in more than one ranking


def test_soil_protection_priority_changes_the_ranking(client: TestClient) -> None:
    soil = _post(client, {**OFF, "soil_protection": 1})
    risk_only = _post(client, OFF)
    assert [r["id"] for r in soil["rotations"]] != [r["id"] for r in risk_only["rotations"]]
    best_soil = soil["rotations"][0]["scores"]["soil_protection"]
    assert all(r["scores"]["soil_protection"] <= best_soil for r in risk_only["rotations"])


@pytest.mark.parametrize("value", [2, -1, "high"])
def test_invalid_priorities_are_rejected(client: TestClient, value) -> None:
    resp = client.post(URL, json={**BODY, "priorities": {"productivity": value}})
    assert resp.status_code == 422


def test_priorities_and_history_rescore_cached_candidates(
    client: TestClient, body: dict, monkeypatch
) -> None:
    calls = []
    original = rotations_service.rotation_candidates
    monkeypatch.setattr(
        rotations_service,
        "rotation_candidates",
        lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs),
    )
    _post(client, {**OFF, "productivity": 1})
    resp = client.post(
        URL, json={**BODY, "agricultural_year": 2027, "crop_history": [MAIZE], "priorities": OFF}
    )
    assert resp.status_code == 200
    assert calls == []


def test_ranking_is_cached_per_climate_cell_soil_and_year(client: TestClient, body: dict) -> None:
    import time

    started = time.perf_counter()
    resp = client.post(URL, json={**BODY, "agricultural_year": 2027})
    assert time.perf_counter() - started < 1.0  # the module fixture already computed it
    assert resp.json() == body


def test_default_agricultural_year_is_next_season(client: TestClient) -> None:
    resp = client.post(URL, json={**BODY, "soil_type": "clayey"})
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
        {**BODY, "soil_type": "loamy"},
        {"soil_type": "medium"},
        {**BODY, "location": {"lat": -95, "lon": 0}},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, payload: dict) -> None:
    assert client.post(URL, json=payload).status_code == 422


# --- Soil -----------------------------------------------------------------------------------


SOIL_KEYS = {
    "source",
    "texture_class",
    "available_water_mm_per_m",
    "sand_pct",
    "silt_pct",
    "clay_pct",
    "organic_matter_pct",
    "field_capacity",
    "wilting_point",
    "sampled_location",
    "distance_km",
}


def test_soil_type_from_request_is_echoed(body: dict) -> None:
    soil = body["soil"]
    assert set(soil) == SOIL_KEYS
    assert soil["source"] == "REQUEST_TEXTURE_CLASS"
    assert soil["texture_class"] == "medium"
    assert soil["available_water_mm_per_m"] == 150


@pytest.fixture(scope="module")
def estimated(client: TestClient) -> dict:
    resp = client.post(URL, json={"location": SORRISO, "agricultural_year": 2027})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_without_soil_type_the_soil_is_estimated_from_soilgrids(estimated: dict) -> None:
    soil = estimated["soil"]
    assert set(soil) == SOIL_KEYS
    assert soil["source"] == "SOILGRIDS_SAXTON_RAWLS_2006"
    # The Sorriso city center is built-up (no prediction): the nearest ring point is used.
    assert soil["distance_km"] > 0
    assert soil["sampled_location"] != SORRISO
    assert estimated["soil_type"] == soil["texture_class"] == "medium"
    assert 80 <= soil["available_water_mm_per_m"] <= 130
    assert soil["sand_pct"] + soil["silt_pct"] + soil["clay_pct"] == pytest.approx(100, abs=0.1)
    assert any("SoilGrids" in warning for warning in estimated["warnings"])


def test_estimated_soil_is_close_to_the_brazilian_local_equation(tmp_path) -> None:
    """Local validation: Saxton & Rawls (0–100 cm) vs IN SPA/MAPA nº 1/2022 (0–40 cm)."""
    estimate = asyncio.run(
        _seed_soilgrids_client(tmp_path).estimate(SORRISO["lat"], SORRISO["lon"])
    )
    global_mm_per_m = saxton_rawls_2006(estimate.root_zone_texture).available_water_mm_per_m
    top = estimate.zarc_layer_texture
    local_mm_per_m = 100 * zarc_available_water_mm_per_cm(top.sand_pct, top.silt_pct, top.clay_pct)
    assert global_mm_per_m == pytest.approx(local_mm_per_m, rel=0.2)


class _FailingSoilGrids:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def estimate(self, lat: float, lon: float):
        raise self.error


@pytest.mark.parametrize(
    ("error", "status"),
    [(SoilGridsNoData("no data"), 422), (SoilGridsUnavailable("down"), 503)],
)
def test_soilgrids_failure_asks_for_soil_type(client: TestClient, error, status: int) -> None:
    previous = app.dependency_overrides[get_soilgrids_client]
    app.dependency_overrides[get_soilgrids_client] = lambda: _FailingSoilGrids(error)
    try:
        resp = client.post(URL, json={"location": SORRISO})
        assert resp.status_code == status
        assert "soil_type" in resp.json()["detail"]
        # With soil_type the estimate only feeds the soil properties panel.
        resp = client.post(URL, json=BODY)
        assert resp.status_code == 200
        assert resp.json()["soil_properties"] is None
        assert any("panel unavailable" in w for w in resp.json()["warnings"])
    finally:
        app.dependency_overrides[get_soilgrids_client] = previous


# --- Catalog --------------------------------------------------------------------------------


def test_rotation_catalog_has_family_and_ky_for_every_crop(crops: dict[int, CropDetail]) -> None:
    assert len(ROTATION_CROP_CODES) == 9
    for code, crop in crops.items():
        assert crop is not None, code
        assert crop.family, code
        assert crop.water_requirements is not None, code
        ky = crop.water_requirements.yield_response_factor
        assert None not in (ky.season, ky.initial, ky.development, ky.mid_season, ky.late_season)


# --- Regional crop calendar (GGCMI) ---------------------------------------------------------


def test_sorriso_calendar_comes_from_conab_statistics() -> None:
    calendars = crop_calendars(SORRISO["lat"], SORRISO["lon"])
    soybean, maize = calendars[SOYBEAN], calendars[MAIZE]
    assert (soybean.cell_lat, soybean.cell_lon) == (-12.75, -55.75)
    assert soybean.data_source == maize.data_source == "Brazil CONAB"
    assert (soybean.planting_date(2027), soybean.season_length_days) == (date(2027, 10, 31), 100)
    # Second-season maize (safrinha), planted after the soybean harvest.
    assert (maize.planting_date(2027), maize.season_length_days) == (date(2028, 2, 14), 116)
    assert COWPEA not in calendars  # GGCMI has no cowpea calendar
    assert crop_calendars(0.0, -30.0) == {}  # ocean


def test_stage_lengths_are_stretched_to_the_calendar_cycle(
    crops: dict[int, CropDetail],
) -> None:
    calendar = crop_calendars(SORRISO["lat"], SORRISO["lon"])[SOYBEAN]
    fao = crops[SOYBEAN].water_requirements.stage_length_days  # 15/15/40/15 = 85
    stages = apply_calendar(crops[SOYBEAN], calendar).water_requirements.stage_length_days
    assert stages.total == 100
    assert stages.initial + stages.development + stages.mid_season + stages.late_season == 100
    assert stages.mid_season == round(fao.mid_season * 100 / fao.total)
    assert scale_stage_lengths(fao, fao.total) == fao
    assert apply_calendar(crops[SOYBEAN], None) is crops[SOYBEAN]


def test_planting_dates_follow_the_calendar(body: dict) -> None:
    tolerance = body["assumptions"]["planting_tolerance_days"]
    for rotation in body["rotations"]:
        for rotation_crop in rotation["crops"]:
            calendar = rotation_crop["calendar"]
            if calendar["source"] == "FAO56_REFERENCE":
                continue
            assert calendar["source"] == "GGCMI_PHASE3_V1_01"
            reference = date.fromisoformat(calendar["reference_planting_date"])
            planting = date.fromisoformat(rotation_crop["planting_date"])
            assert abs((planting - reference).days) <= tolerance


def test_first_planting_dates_around_the_calendar_date() -> None:
    calendar = crop_calendars(SORRISO["lat"], SORRISO["lon"])[SOYBEAN]  # 31 Oct
    dates = first_planting_dates(2027, calendar)
    assert (dates[0], dates[-1]) == (date(2027, 10, 1), date(2027, 11, 30))
    assert date(2027, 10, 31) in dates


def test_crops_without_calendar_are_named_in_a_warning(body: dict) -> None:
    assert any("No regional calendar for" in warning for warning in body["warnings"])


def test_first_planting_dates_every_ten_days_in_october_and_november() -> None:
    dates = first_planting_dates(2027)
    assert dates[0] == date(2027, 10, 1)
    assert dates[-1] <= date(2027, 11, 30)
    assert all(
        (later - earlier).days == 10 for earlier, later in zip(dates, dates[1:], strict=False)
    )


# --- Risk per crop --------------------------------------------------------------------------


def _season(
    loss: float | None,
    *,
    valid: bool = True,
    critical: str | None = "mid_season",
    satisfaction: float = 0.9,
) -> CropSeasonResult:
    return CropSeasonResult(
        profile="TEST",
        sowing_date=date(2020, 10, 1),
        harvest_date=date(2021, 1, 1),
        is_valid=valid,
        missing_days=0 if valid else 50,
        water_satisfaction_index=dict.fromkeys(STAGES, satisfaction),
        season_water_satisfaction_index=satisfaction,
        yield_loss_fraction=loss,
        critical_stage=critical,
    )


def test_crop_risk_counts_seasons_above_threshold_and_averages_loss() -> None:
    losses = [0.0, 0.05, 0.1, 0.25, 0.3, 0.1, 0.15, 0.2, 0.0, 0.05]
    results = [_season(loss) for loss in losses]
    results[0] = _season(0.0, critical="development")
    risk = crop_risk(results)
    assert risk.valid_years == 10
    # > 20% (strictly): 0.25 and 0.3; exactly 0.2 does not count
    assert risk.risk_percentage == 20.0
    assert risk.estimated_yield_loss_pct == pytest.approx(100 * sum(losses) / len(losses), abs=0.05)
    assert risk.loss_threshold_pct == LOSS_THRESHOLD_PCT
    assert risk.critical_stage == "mid_season"
    assert risk.mean_isna.mid_season == pytest.approx(0.9)
    assert risk.mean_season_isna == pytest.approx(0.9)
    assert risk.method == "FAO56_SIMPLIFIED_KY"


def test_crop_risk_ignores_invalid_seasons_and_needs_five() -> None:
    results = [_season(0.5) for _ in range(4)] + [_season(0.5, valid=False) for _ in range(6)]
    risk = crop_risk(results)
    assert risk.valid_years == 4
    assert risk.risk_percentage is None
    assert risk.estimated_yield_loss_pct is None
    assert risk.critical_stage is None
    assert risk.mean_isna.initial is None
    assert risk.mean_season_isna is None
    risk = crop_risk(results + [_season(None)])  # loss not computable: not counted either
    assert risk.valid_years == 4


def test_crop_risk_critical_stage_tie_goes_to_earlier_stage_and_none_without_deficit() -> None:
    results = [_season(0.1, critical="mid_season") for _ in range(3)] + [
        _season(0.1, critical="development") for _ in range(3)
    ]
    assert crop_risk(results).critical_stage == "development"
    assert crop_risk([_season(0.0, critical=None) for _ in range(5)]).critical_stage is None


# --- Ranking --------------------------------------------------------------------------------


def _wet_climate(tmin: float = 20.0, tmax: float = 30.0) -> list[DiaClima]:
    """Rain above ET0 every day of the seed seasons: no deficit, so every risk is 0."""
    start, end = date(2016, 9, 1), date(2026, 8, 31)
    return [
        DiaClima(
            data=start + timedelta(days=offset),
            tmax=tmax,
            tmin=tmin,
            tdew=None,
            ur=None,
            vento=None,
            rad=None,
            chuva=10.0,
            gwettop=None,
            gwetroot=None,
            et0=5.0,
            et0_metodo=None,
        )
        for offset in range((end - start).days + 1)
    ]


@pytest.fixture(scope="module")
def wet_days() -> list[DiaClima]:
    return _wet_climate()


def test_pairs_of_the_same_family_are_excluded(crops, wet_days) -> None:
    same_family = [crops[SOYBEAN], crops[COMMON_BEAN]]
    assert crops[SOYBEAN].family == crops[COMMON_BEAN].family
    assert rank_rotations(same_family, wet_days, SEED_SEASONS, SoilType.MEDIUM, 2027) == []


def test_fewer_than_four_viable_pairs_returns_fewer(crops, wet_days) -> None:
    ranked = rank_rotations(
        [crops[SOYBEAN], crops[MAIZE]], wet_days, SEED_SEASONS, SoilType.MEDIUM, 2027
    )
    assert {rotation.crop_codes for rotation in ranked} == {(SOYBEAN, MAIZE), (MAIZE, SOYBEAN)}


def test_ties_are_broken_by_planting_date_then_codes(crops, wet_days) -> None:
    candidates = [crops[code] for code in (SOYBEAN, MAIZE, SORGHUM, MILLET)]
    ranked = rank_rotations(candidates, wet_days, SEED_SEASONS, SoilType.MEDIUM, 2027)
    assert ranked
    for rotation in ranked:
        assert rotation.first.crop.risk.risk_percentage == 0
        assert rotation.second.crop.risk.risk_percentage == 0
        assert rotation.first.planting_date == date(2027, 10, 1)  # earliest date wins the tie
    assert [r.crop_codes for r in ranked] == sorted(r.crop_codes for r in ranked)
    again = rank_rotations(
        list(reversed(candidates)), wet_days, SEED_SEASONS, SoilType.MEDIUM, 2027
    )
    assert [r.crop_codes for r in again] == [r.crop_codes for r in ranked]


def test_temperature_filter_excludes_crops_outside_absolute_limits(crops) -> None:
    hot_days = _wet_climate(tmin=25.0, tmax=36.0)
    cold_tolerant_maize = crops[MAIZE].model_copy(
        update={"temperature": crops[MAIZE].temperature.model_copy(update={"absolute_max": 35.0})}
    )
    ranked = rank_rotations(
        [crops[SOYBEAN], cold_tolerant_maize], hot_days, SEED_SEASONS, SoilType.MEDIUM, 2027
    )
    assert ranked == []


def test_unknown_risk_sorts_after_any_known_risk(crops, wet_days) -> None:
    planting = date(2027, 10, 1)
    known = crop_risk([_season(1.0) for _ in range(10)])  # 100% risk, worst possible
    unknown = crop_risk([_season(0.0) for _ in range(3)])  # too few seasons
    assert unknown.risk_percentage is None
    assert rank_key(known, known, planting, (2, 2)) < rank_key(unknown, known, planting, (1, 1))
    # Climate with only 3 seasons: every risk is unknown and the rotation still ranks.
    few_seasons = rank_rotations(
        [crops[SOYBEAN], crops[MAIZE]], wet_days, [2016, 2017, 2018], SoilType.MEDIUM, 2027
    )
    assert few_seasons and all(r.ranking_key[0] == 1 for r in few_seasons)


# --- Sanity on the Sorriso seed (task 6) ----------------------------------------------------


def test_soybean_in_october_november_has_low_risk(crops, sorriso_days) -> None:
    for planting in (date(2027, 10, 11), date(2027, 11, 10)):
        rotation = evaluate_rotation(
            crops[SOYBEAN], crops[MAIZE], planting, sorriso_days, SEED_SEASONS, SoilType.MEDIUM
        )
        assert rotation.first.crop.risk.risk_percentage <= 10


def test_late_second_season_maize_is_riskier_than_early(crops, sorriso_days) -> None:
    early, late = (
        evaluate_rotation(
            crops[SOYBEAN], crops[MAIZE], planting, sorriso_days, SEED_SEASONS, SoilType.MEDIUM
        ).second.crop.risk
        for planting in (date(2027, 10, 1), date(2027, 11, 30))
    )
    assert late.estimated_yield_loss_pct > early.estimated_yield_loss_pct
    assert late.risk_percentage >= early.risk_percentage


def test_sandy_soil_risk_is_at_least_clayey(crops, sorriso_days) -> None:
    planting = date(2027, 11, 10)
    sandy, clayey = (
        evaluate_rotation(
            crops[SOYBEAN], crops[MAIZE], planting, sorriso_days, SEED_SEASONS, soil
        ).second.crop.risk
        for soil in (SoilType.SANDY, SoilType.CLAYEY)
    )
    assert sandy.risk_percentage >= clayey.risk_percentage
    assert sandy.estimated_yield_loss_pct >= clayey.estimated_yield_loss_pct


def test_wetter_initial_soil_never_raises_first_crop_loss(crops, sorriso_days) -> None:
    losses = [
        evaluate_rotation(
            crops[SOYBEAN],
            crops[MAIZE],
            date(2027, 10, 1),
            sorriso_days,
            SEED_SEASONS,
            SoilType.SANDY,
            fraction,
        ).first.crop.risk.estimated_yield_loss_pct
        for fraction in (0.2, 0.5, 0.8)
    ]
    assert losses == sorted(losses, reverse=True)


# --- Priority scores (unit) -----------------------------------------------------------------


def _with_risks(
    ranked: RankedRotation, risk_a: CropRisk, risk_b: CropRisk, planting: date
) -> RankedRotation:
    first = ranked.first.model_copy(
        update={
            "crop": ranked.first.crop.model_copy(update={"risk": risk_a}),
            "planting_date": planting,
        }
    )
    second = ranked.second.model_copy(
        update={"crop": ranked.second.crop.model_copy(update={"risk": risk_b})}
    )
    return replace(
        ranked,
        ranking_key=rank_key(risk_a, risk_b, planting, ranked.crop_codes),
        first=first,
        second=second,
    )


def _risk(loss_pct: float | None, risk_pct: float | None, isna: float | None) -> CropRisk:
    return crop_risk([_season(0.0) for _ in range(5)]).model_copy(
        update={
            "estimated_yield_loss_pct": loss_pct,
            "risk_percentage": risk_pct,
            "mean_season_isna": isna,
        }
    )


@pytest.fixture(scope="module")
def soy_maize(crops, wet_days) -> RankedRotation:
    return evaluate_rotation(
        crops[SOYBEAN], crops[MAIZE], date(2027, 10, 1), wet_days, SEED_SEASONS, SoilType.MEDIUM
    )


def _indicators(soy_maize, risk_a: CropRisk, risk_b: CropRisk) -> dict:
    ranked = _with_risks(soy_maize, risk_a, risk_b, date(2027, 10, 1))
    protection = rotation_soil_protection(ranked, [], date(2027, 9, 1), date(2028, 8, 31))
    return rotation_indicators(ranked, protection)


def test_scores_take_the_worse_crop_and_overall_averages_all_three(soy_maize) -> None:
    indicators = _indicators(soy_maize, _risk(4.0, 0.0, 0.95), _risk(12.0, 10.0, 0.97))
    scores = rotation_scores(indicators)
    assert scores.productivity == 88.0  # 100 − 12% loss (crop B)
    assert scores.water_security == 90.0  # 100 − 10% critical seasons (crop B)
    expected = (88.0 + 90.0 + indicators["soil_protection"]) / 3
    assert scores.overall == pytest.approx(expected, abs=0.05)


def test_priority_value_averages_only_the_enabled_indicators(soy_maize) -> None:
    indicators = _indicators(soy_maize, _risk(4.0, 0.0, 0.95), _risk(12.0, 10.0, 0.97))
    only_water = Priorities(productivity=0, water_security=1, soil_protection=0)
    assert priority_value(indicators, only_water) == 90.0
    both = Priorities(productivity=1, water_security=1, soil_protection=0)
    assert priority_value(indicators, both) == 89.0
    assert priority_value(indicators, Priorities(**OFF)) is None


def test_scales_reach_0_and_100(soy_maize) -> None:
    best = rotation_scores(_indicators(soy_maize, _risk(0.0, 0.0, 1.0), _risk(0.0, 0.0, 1.0)))
    assert (best.productivity, best.water_security) == (100.0, 100.0)
    worst = rotation_scores(
        _indicators(soy_maize, _risk(100.0, 100.0, 0.0), _risk(100.0, 100.0, 0.0))
    )
    assert (worst.productivity, worst.water_security) == (0.0, 0.0)


def test_unknown_risk_gives_no_overall_score(soy_maize) -> None:
    indicators = _indicators(soy_maize, _risk(None, None, None), _risk(1.0, 0.0, 0.99))
    scores = rotation_scores(indicators)
    assert scores.productivity is None and scores.water_security is None
    assert scores.overall is None
    only_soil = Priorities(productivity=0, water_security=0, soil_protection=1)
    assert priority_value(indicators, only_soil) is not None


def test_best_planting_date_of_a_pair_follows_the_priorities(soy_maize) -> None:
    # Early date: lowest risk_percentage (wins on risk); late date: smallest loss (wins on yield).
    early = _with_risks(soy_maize, _risk(10.0, 0.0, 0.9), _risk(10.0, 0.0, 0.9), date(2027, 10, 1))
    late = _with_risks(soy_maize, _risk(1.0, 5.0, 0.99), _risk(1.0, 5.0, 0.99), date(2027, 10, 11))
    unknown = _with_risks(
        soy_maize, _risk(None, None, None), _risk(None, None, None), date(2027, 10, 21)
    )
    candidates = [unknown, late, early]

    [by_risk] = select_rotations(candidates, Priorities(**OFF), [], 2027)
    assert by_risk.ranked.first.planting_date == date(2027, 10, 1)
    [by_yield] = select_rotations(candidates, Priorities(**{**OFF, "productivity": 1}), [], 2027)
    assert by_yield.ranked.first.planting_date == date(2027, 10, 11)
