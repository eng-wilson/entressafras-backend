"""Rotas de clima contra a semente de Sorriso (app/data/power), sem rede."""

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes.clima import get_power_client
from app.core.config import get_settings
from app.main import app
from app.services import power_client as pc

SORRISO = {"lat": -12.55, "lon": -55.72}
HOJE = date(2026, 11, 14)  # a semente cobre 2016/17–2025/26


def _sem_rede(req: httpx.Request) -> httpx.Response:
    raise AssertionError(f"teste tentou acessar a rede: {req.url}")


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    real = pc.janela_safras
    monkeypatch.setattr(pc, "janela_safras", lambda hoje=None: real(hoje or HOJE))
    monkeypatch.setattr("app.api.routes.clima.janela_safras", lambda hoje=None: real(hoje or HOJE))
    power = pc.PowerClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_sem_rede)),
        base_url="https://power.test",
        seed_dir=get_settings().power_seed_dir,
        cache_dir=tmp_path,
    )
    app.dependency_overrides[get_power_client] = lambda: power
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_decendios_sorriso(client: TestClient) -> None:
    resp = client.get("/api/clima/decendios", params=SORRISO)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ponto"] == {"lat": -12.55, "lon": -55.72}
    assert (body["celula"]["lat"], body["celula"]["lon"]) == (-12.5, -55.625)
    assert body["safras"][0] == "2016/17" and len(body["safras"]) == 10
    dec = body["decendios"]
    assert len(dec) == 36
    assert [d["idx"] for d in dec] == list(range(36))
    assert sum(d["dias"] for d in dec) in (365, 366)
    assert all(d["anos_validos"] == 10 for d in dec)
    # Clima de Sorriso: chuva concentrada de out a abr, inverno seco.
    chuva = {d["rotulo"]: d["chuva_media"] for d in dec}
    partes = ("1-10", "11-20", "21-fim")
    verao = sum(chuva[f"{p} {m}"] for m in ("dez", "jan", "fev") for p in partes)
    inverno = sum(chuva[f"{p} {m}"] for m in ("jun", "jul") for p in partes)
    assert verao > 600 and inverno < 60
    for d in dec:
        assert d["chuva_p20"] <= d["chuva_media"] + 1e-6
        assert d["tmin_absoluto"] <= d["tmin_extremo_medio"] <= d["tmin_media"]
        assert d["tmax_media"] <= d["tmax_extremo_medio"] <= d["tmax_absoluto"]
        assert 20 < d["et0_media"] < 70  # 2–7 mm/dia
        assert 0 <= d["gwetroot_media"] <= 1


def test_diario_recorte(client: TestClient) -> None:
    params = {**SORRISO, "inicio": "2020-01-01", "fim": "2020-01-10"}
    body = client.get("/api/clima/diario", params=params).json()
    assert [d["data"] for d in body["dias"]][::9] == ["2020-01-01", "2020-01-10"]
    assert body["dias"][0]["et0_metodo"] == "pm-orvalho"


def test_diario_janela_inteira(client: TestClient) -> None:
    body = client.get("/api/clima/diario", params=SORRISO).json()
    assert body["dias"][0]["data"] == "2016-09-01" and body["dias"][-1]["data"] == "2026-08-31"


@pytest.mark.parametrize(
    "extra",
    [
        {"inicio": "2010-01-01", "fim": "2017-01-01"},
        {"inicio": "2026-01-01", "fim": "2026-10-01"},
        {"inicio": "2020-02-01", "fim": "2020-01-01"},
        {"lat": 91},
    ],
)
def test_validacao_422(client: TestClient, extra: dict) -> None:
    assert client.get("/api/clima/diario", params={**SORRISO, **extra}).status_code == 422


def test_power_indisponivel_503(tmp_path) -> None:
    power = pc.PowerClient(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
        base_url="https://power.test",
        seed_dir=tmp_path / "seed",
        cache_dir=tmp_path / "cache",
        backoff_s=(0.0,),
    )
    app.dependency_overrides[get_power_client] = lambda: power
    try:
        resp = TestClient(app).get("/api/clima/decendios", params={"lat": 10, "lon": 10})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
