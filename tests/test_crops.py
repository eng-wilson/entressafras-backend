from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_crop_by_code() -> None:
    response = client.get("/api/crops/2114")
    assert response.status_code == 200
    body = response.json()
    assert body["eco_port_code"] == 2114
    assert body["scientific_name"] == "Triticum aestivum"
    assert isinstance(body["temperature"]["optimal_min"], float)
    assert body["temperature"]["killing_early_growth"] == 0.0
    assert body["crop_cycle"] == {"min_days": 90, "max_days": 250}
    assert "blé" in body["common_names"]  # encoding latin-1 lido corretamente
    assert body["life_span"] == ["annual"]
    assert "cereals & pseudocereals" in body["category"]


def test_get_crop_missing_values_are_null() -> None:
    body = client.get("/api/crops/290").json()  # Abelmoschus manihot
    assert body["scientific_name"] == "Abelmoschus manihot"
    assert body["latitude"]["absolute_min"] is None
    assert body["temperature"]["killing_rest"] is None
    assert body["soil"]["al_toxicity"] == {"optimal": [], "absolute": []}


def test_get_crop_not_found() -> None:
    response = client.get("/api/crops/999999")
    assert response.status_code == 404


def test_search_exact_match_ranks_first() -> None:
    body = client.get("/api/crops", params={"q": "trigo"}).json()
    names = [item["scientific_name"] for item in body["items"]]
    assert names[0] == "Triticum aestivum"
    assert "Fagopyrum esculentum" in names  # "trigo sarraceno" vem depois


def test_search_accent_and_case_insensitive() -> None:
    body = client.get("/api/crops", params={"q": "QUIABO"}).json()
    assert body["items"][0]["scientific_name"] == "Abelmoschus esculentus"
    body = client.get("/api/crops", params={"q": "ble"}).json()
    assert "Triticum aestivum" in [i["scientific_name"] for i in body["items"]]


def test_search_by_scientific_name() -> None:
    body = client.get("/api/crops", params={"q": "triticum aestivum"}).json()
    assert body["items"][0]["eco_port_code"] == 2114


def test_filter_life_span() -> None:
    body = client.get("/api/crops", params={"life_span": "annual", "limit": 100}).json()
    assert body["total"] > 0
    assert all("annual" in item["life_span"] for item in body["items"])


def test_list_pagination() -> None:
    body = client.get("/api/crops").json()
    assert body["total"] == 2568
    assert body["limit"] == 20
    assert len(body["items"]) == 20
    page2 = client.get("/api/crops", params={"offset": 20}).json()
    assert page2["items"][0] != body["items"][0]


def test_limit_validation() -> None:
    assert client.get("/api/crops", params={"limit": 101}).status_code == 422
    assert client.get("/api/crops", params={"offset": -1}).status_code == 422


def test_search_no_results() -> None:
    body = client.get("/api/crops", params={"q": "xyznotacrop"}).json()
    assert body == {"total": 0, "limit": 20, "offset": 0, "items": []}


def test_get_crop_water_requirements_maize() -> None:
    water = client.get("/api/crops/2175").json()["water_requirements"]
    assert water["profile"] == "FAO-MAIZE"
    assert water["profile_name"] == "MAIZE (Grain)"
    assert water["crop_coefficient"] == {"initial": 0.3, "mid_season": 1.2, "late_season": 0.35}
    assert water["stage_length_days"] == {
        "initial": 20,
        "development": 35,
        "mid_season": 40,
        "late_season": 30,
        "total": 125,
    }
    assert water["root_depth_m"] == {"initial": 0.3, "max": 1.0}
    assert water["allowable_depletion"]["late_season"] == 0.8
    assert water["yield_response_factor"]["season"] == 1.25
    assert water["max_height_m"] == 2.0


def test_get_crop_water_requirements_soybean() -> None:
    water = client.get("/api/crops/1150").json()["water_requirements"]
    assert water["profile"] == "FAO-SOYBEAN"
    assert water["crop_coefficient"]["mid_season"] == 1.15


def test_get_crop_without_water_profile_is_null() -> None:
    assert client.get("/api/crops/290").json()["water_requirements"] is None


def test_search_summary_has_no_water_requirements() -> None:
    item = client.get("/api/crops", params={"q": "milho"}).json()["items"][0]
    assert "water_requirements" not in item


def test_filter_simulable_crops() -> None:
    body = client.get("/api/crops", params={"simulable": "true", "limit": 100}).json()
    assert body["total"] == 22
    assert all(item["simulable"] for item in body["items"])
    codes = {item["eco_port_code"] for item in body["items"]}
    assert {1150, 2175, 8418} <= codes
    assert 801 not in codes  # sunn hemp (Crotalaria juncea) has no FAO-56 profile


def test_filter_not_simulable_crops() -> None:
    body = client.get("/api/crops", params={"simulable": "false", "q": "crotalaria"}).json()
    assert body["total"] > 0
    assert not any(item["simulable"] for item in body["items"])
    everything = client.get("/api/crops", params={"simulable": "false"}).json()
    assert everything["total"] == 2568 - 22


def test_summary_flags_simulable_crops() -> None:
    items = client.get("/api/crops", params={"q": "glycine max"}).json()["items"]
    assert items[0]["eco_port_code"] == 1150
    assert items[0]["simulable"] is True
