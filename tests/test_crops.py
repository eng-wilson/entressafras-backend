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
