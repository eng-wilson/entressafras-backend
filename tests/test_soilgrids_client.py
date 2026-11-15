"""SoilGrids client against synthetic answers (no network)."""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.services.soil import SoilType, saxton_rawls_2006
from app.services.soilgrids_client import (
    DEPTHS,
    SEARCH_RINGS_KM,
    SoilGridsClient,
    SoilGridsInvalidResponse,
    SoilGridsNoData,
    SoilGridsUnavailable,
    cache_key,
    depth_weighted_texture,
    parse,
    search_points,
)

RANGES = {"0-5cm": (0, 5), "5-15cm": (5, 15), "15-30cm": (15, 30), "30-60cm": (30, 60)}
RANGES["60-100cm"] = (60, 100)


def answer(lat: float, lon: float, values: dict[str, list[float | None]] | None = None) -> dict:
    """SoilGrids-like JSON; `values` per property in conventional units, one per depth."""
    values = values or {
        "sand": [56.0, 56.0, 54.0, 51.0, 50.0],
        "silt": [19.0, 19.0, 18.0, 18.0, 17.0],
        "clay": [25.0, 25.0, 28.0, 31.0, 33.0],
        "soc": [15.0, 12.0, 9.0, 7.0, 4.0],
    }
    units = {"sand": "g/kg", "silt": "g/kg", "clay": "g/kg", "soc": "dg/kg"}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "layers": [
                {
                    "name": name,
                    "unit_measure": {"d_factor": 10, "mapped_units": units[name]},
                    "depths": [
                        {
                            "range": {
                                "top_depth": RANGES[label][0],
                                "bottom_depth": RANGES[label][1],
                                "unit_depth": "cm",
                            },
                            "label": label,
                            "values": {"mean": None if v is None else round(v * 10)},
                        }
                        for label, v in zip(DEPTHS, series, strict=True)
                    ],
                }
                for name, series in values.items()
            ]
        },
    }


NULLS = {name: [None] * 5 for name in ("sand", "silt", "clay", "soc")}


def make_client(tmp_path: Path, handler) -> tuple[SoilGridsClient, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = SoilGridsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        base_url="https://soilgrids.test",
        seed_dir=tmp_path / "seed",
        cache_dir=tmp_path / "cache",
        min_interval_s=0.0,
        backoff_s=(0.0,),
    )
    return client, calls


def test_parse_converts_units() -> None:
    point = parse(answer(-12.62, -55.8))
    assert (point.lat, point.lon) == (-12.62, -55.8)
    assert [layer.bottom_cm for layer in point.layers] == [5, 15, 30, 60, 100]
    assert point.layers[0].sand_pct == 56.0
    assert point.layers[0].organic_carbon_g_per_kg == 15.0
    assert point.has_data
    assert not parse(answer(0, 0, NULLS)).has_data


def test_parse_rejects_missing_property() -> None:
    raw = answer(0, 0)
    raw["properties"]["layers"] = raw["properties"]["layers"][:3]
    with pytest.raises(SoilGridsInvalidResponse, match="soc"):
        parse(raw)


def test_depth_weighted_texture_and_organic_matter() -> None:
    point = parse(answer(-12.62, -55.8))
    top = depth_weighted_texture(point, 40)
    # 0–40 cm: weights 5, 10, 15, 10 cm
    assert top.sand_pct == pytest.approx((56 * 5 + 56 * 10 + 54 * 15 + 51 * 10) / 40)
    assert top.organic_matter_pct == pytest.approx(
        (15 * 5 + 12 * 10 + 9 * 15 + 7 * 10) / 40 / 10 * 1.724
    )
    assert top.sand_pct + top.silt_pct + top.clay_pct == pytest.approx(100)
    root_zone = depth_weighted_texture(point, 100)
    assert root_zone.clay_pct > top.clay_pct  # clay increases with depth in the answer
    with pytest.raises(SoilGridsNoData):
        depth_weighted_texture(parse(answer(0, 0, NULLS)), 100)


def test_search_points_start_at_location_then_rings() -> None:
    points = search_points(-12.55, -55.72)
    assert points[0] == (-12.55, -55.72, 0.0)
    assert len(points) == 1 + 4 * len(SEARCH_RINGS_KM)
    north = points[1]
    assert north[2] == SEARCH_RINGS_KM[0]
    assert north[0] == pytest.approx(-12.55 + SEARCH_RINGS_KM[0] / 111.32, abs=1e-4)


def test_estimate_skips_built_up_point_and_caches(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        lat, lon = float(request.url.params["lat"]), float(request.url.params["lon"])
        values = NULLS if (lat, lon) == (-12.55, -55.72) else None
        return httpx.Response(200, json=answer(lat, lon, values))

    client, calls = make_client(tmp_path, handler)
    estimate = asyncio.run(client.estimate(-12.55, -55.72))
    assert len(calls) == 2  # the null center, then the first ring point
    query = calls[0].url.params
    assert query.get_list("property") == ["sand", "silt", "clay", "soc"]
    assert query.get_list("depth") == list(DEPTHS)
    assert estimate.distance_km == SEARCH_RINGS_KM[0]
    assert estimate.topsoil_texture == depth_weighted_texture(parse(answer(0, 0)), 30)
    assert (estimate.sampled_lat, estimate.sampled_lon) != (-12.55, -55.72)
    profile = estimate.profile
    assert profile.texture_class == SoilType.MEDIUM
    assert profile.available_water_mm_per_m == pytest.approx(
        saxton_rawls_2006(estimate.root_zone_texture).available_water_mm_per_m
    )
    assert (tmp_path / "cache" / cache_key(-12.55, -55.72)).is_file()

    # A new client reads both answers from disk, with no network call.
    fresh, fresh_calls = make_client(tmp_path, handler)
    assert asyncio.run(fresh.estimate(-12.55, -55.72)) == estimate
    assert fresh_calls == []


def test_estimate_without_data_nearby_raises(tmp_path: Path) -> None:
    client, calls = make_client(
        tmp_path,
        lambda request: httpx.Response(
            200,
            json=answer(float(request.url.params["lat"]), float(request.url.params["lon"]), NULLS),
        ),
    )
    with pytest.raises(SoilGridsNoData):
        asyncio.run(client.estimate(0.0, -30.0))
    assert len(calls) == 1 + 4 * len(SEARCH_RINGS_KM)


def test_unavailable_after_retries(tmp_path: Path) -> None:
    client, calls = make_client(tmp_path, lambda request: httpx.Response(503))
    with pytest.raises(SoilGridsUnavailable):
        asyncio.run(client.estimate(-12.62, -55.8))
    assert len(calls) == 3
    assert not any((tmp_path / "cache").glob("*.json"))


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    client, calls = make_client(tmp_path, lambda request: httpx.Response(400, text="bad"))
    with pytest.raises(SoilGridsInvalidResponse):
        asyncio.run(client.estimate(-12.62, -55.8))
    assert len(calls) == 1


def test_network_calls_are_spaced(tmp_path: Path) -> None:
    import time

    def handler(request: httpx.Request) -> httpx.Response:
        lat, lon = float(request.url.params["lat"]), float(request.url.params["lon"])
        return httpx.Response(200, json=answer(lat, lon, NULLS if lat == -12.55 else None))

    client, calls = make_client(tmp_path, handler)
    client._min_interval_s = 0.2
    started = time.monotonic()
    asyncio.run(client.estimate(-12.55, -55.72))
    assert len(calls) == 2
    assert time.monotonic() - started >= 0.2
