"""ISRIC SoilGrids 2.0 client (REST, point queries) with seed and disk cache.

Gives the soil texture and organic carbon at a location, which `soil.py` turns into available
water with a global pedotransfer function (Saxton & Rawls 2006). SoilGrids data: Poggio et al.
(2021), SOIL 7:217–240, CC BY 4.0, https://soilgrids.org.

Access notes:
- The REST API is a beta service with a fair use limit of 5 calls per minute
  (https://rest.isric.org), so network calls are spaced by `min_interval_s` and every raw answer
  is cached on disk. Read order: versioned seed (`seed_dir`), local cache (`cache_dir`), network.
- Built-up areas (e.g. a city center) have no soil prediction and come back as nulls. The client
  then searches the nearest point with data on rings around the requested location.
"""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.services.soil import SoilProfile, SoilTexture

log = logging.getLogger(__name__)

PROPERTIES = ("sand", "silt", "clay", "soc")
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")
# Layer used for the root-zone available water (FAO-56 root depths of the catalog reach 0.8–1.4 m).
ROOT_ZONE_BOTTOM_CM = 100
# Layer of the Brazilian IN SPA/MAPA nº 1/2022 equation (local validation only).
ZARC_LAYER_BOTTOM_CM = 40
# Topsoil layer of the soil properties panel (as in the FAO Global Soil Organic Carbon map).
TOPSOIL_BOTTOM_CM = 30
# Organic matter = organic carbon × 1.724 (van Bemmelen factor).
ORGANIC_MATTER_PER_CARBON = 1.724
# Search rings (km) and bearings (degrees) when the requested point has no soil data.
SEARCH_RINGS_KM = (2.0, 5.0)
SEARCH_BEARINGS_DEG = (0, 90, 180, 270)
KM_PER_DEGREE_LAT = 111.32
CACHE_VERSION = "v1"
ATTEMPTS = 3


class SoilGridsUnavailable(Exception):
    """SoilGrids did not answer (network, 429 or 5xx) after the retries."""


class SoilGridsInvalidResponse(Exception):
    """SoilGrids answered something unexpected (format, units)."""


class SoilGridsNoData(Exception):
    """No point with soil data around the location (e.g. water, ice or a large city)."""


@dataclass(frozen=True)
class SoilLayer:
    top_cm: int
    bottom_cm: int
    sand_pct: float | None
    silt_pct: float | None
    clay_pct: float | None
    organic_carbon_g_per_kg: float | None

    @property
    def is_complete(self) -> bool:
        return None not in (
            self.sand_pct,
            self.silt_pct,
            self.clay_pct,
            self.organic_carbon_g_per_kg,
        )


@dataclass(frozen=True)
class SoilGridsPoint:
    lat: float
    lon: float
    layers: tuple[SoilLayer, ...]

    @property
    def has_data(self) -> bool:
        return bool(self.layers) and all(layer.is_complete for layer in self.layers)


@dataclass(frozen=True)
class SoilEstimate:
    requested_lat: float
    requested_lon: float
    sampled_lat: float
    sampled_lon: float
    distance_km: float
    root_zone_texture: SoilTexture  # 0–100 cm, depth-weighted
    zarc_layer_texture: SoilTexture  # 0–40 cm, for the Brazilian local check
    topsoil_texture: SoilTexture  # 0–30 cm, for the soil properties panel

    @property
    def profile(self) -> SoilProfile:
        return SoilProfile.from_texture(self.root_zone_texture)


def parse(raw: dict) -> SoilGridsPoint:
    """Turn the REST JSON into layers in conventional units (%, g/kg)."""
    try:
        lon, lat = raw["geometry"]["coordinates"][:2]
        by_property: dict[str, dict[str, tuple[int, int, float | None]]] = {}
        for layer in raw["properties"]["layers"]:
            factor = float(layer["unit_measure"]["d_factor"])
            by_property[layer["name"]] = {
                depth["label"]: (
                    int(depth["range"]["top_depth"]),
                    int(depth["range"]["bottom_depth"]),
                    None if depth["values"]["mean"] is None else depth["values"]["mean"] / factor,
                )
                for depth in layer["depths"]
            }
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise SoilGridsInvalidResponse(f"SoilGrids JSON without expected field: {exc}") from exc
    missing = set(PROPERTIES) - set(by_property)
    if missing:
        raise SoilGridsInvalidResponse(f"properties missing from the answer: {sorted(missing)}")
    layers = []
    for label in DEPTHS:
        if any(label not in by_property[name] for name in PROPERTIES):
            raise SoilGridsInvalidResponse(f"depth {label} missing from the answer")
        top_cm, bottom_cm, sand = by_property["sand"][label]
        layers.append(
            SoilLayer(
                top_cm=top_cm,
                bottom_cm=bottom_cm,
                sand_pct=sand,
                silt_pct=by_property["silt"][label][2],
                clay_pct=by_property["clay"][label][2],
                organic_carbon_g_per_kg=by_property["soc"][label][2],
            )
        )
    return SoilGridsPoint(lat=float(lat), lon=float(lon), layers=tuple(layers))


def depth_weighted_texture(point: SoilGridsPoint, bottom_cm: int) -> SoilTexture:
    """Thickness-weighted texture and organic matter from the surface down to `bottom_cm`."""
    if not point.has_data:
        raise SoilGridsNoData(f"no soil data at {point.lat}, {point.lon}")
    weights = [
        (layer, min(layer.bottom_cm, bottom_cm) - layer.top_cm)
        for layer in point.layers
        if layer.top_cm < bottom_cm
    ]
    total = sum(weight for _, weight in weights)

    def mean(value_of) -> float:
        return sum(value_of(layer) * weight for layer, weight in weights) / total

    sand, silt, clay = (
        mean(lambda layer: layer.sand_pct),
        mean(lambda layer: layer.silt_pct),
        mean(lambda layer: layer.clay_pct),
    )
    fractions = sand + silt + clay  # SoilGrids layers are predicted independently
    organic_carbon_pct = mean(lambda layer: layer.organic_carbon_g_per_kg) / 10
    return SoilTexture(
        sand_pct=100 * sand / fractions,
        silt_pct=100 * silt / fractions,
        clay_pct=100 * clay / fractions,
        organic_matter_pct=organic_carbon_pct * ORGANIC_MATTER_PER_CARBON,
    )


def search_points(lat: float, lon: float) -> list[tuple[float, float, float]]:
    """Requested point first, then rings of SEARCH_RINGS_KM; (lat, lon, distance_km)."""
    points = [(round(lat, 4), round(lon, 4), 0.0)]
    km_per_degree_lon = KM_PER_DEGREE_LAT * max(0.01, math.cos(math.radians(lat)))
    for distance_km in SEARCH_RINGS_KM:
        for bearing in SEARCH_BEARINGS_DEG:
            north = distance_km * math.cos(math.radians(bearing))
            east = distance_km * math.sin(math.radians(bearing))
            points.append(
                (
                    round(lat + north / KM_PER_DEGREE_LAT, 4),
                    round(lon + east / km_per_degree_lon, 4),
                    distance_km,
                )
            )
    return points


def cache_key(lat: float, lon: float) -> str:
    return f"soilgrids_{CACHE_VERSION}_{lat:.4f}_{lon:.4f}.json"


class SoilGridsClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        seed_dir: Path,
        cache_dir: Path,
        min_interval_s: float = 12.0,
        backoff_s: tuple[float, ...] = (5.0, 15.0),
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._seed_dir = seed_dir
        self._cache_dir = cache_dir
        self._min_interval_s = min_interval_s
        self._backoff_s = backoff_s
        self._network_lock = asyncio.Lock()
        self._last_call = -math.inf
        self._estimates: dict[tuple[float, float], SoilEstimate] = {}
        self._locks: dict[tuple[float, float], asyncio.Lock] = {}

    async def estimate(self, lat: float, lon: float) -> SoilEstimate:
        """Soil at the location, or at the nearest point with data on the search rings."""
        key = (round(lat, 4), round(lon, 4))
        if key in self._estimates:
            return self._estimates[key]
        async with self._locks.setdefault(key, asyncio.Lock()):
            if key not in self._estimates:
                self._estimates[key] = await self._search(lat, lon)
        return self._estimates[key]

    async def raw(self, lat: float, lon: float) -> tuple[str, dict]:
        """Raw JSON of one point and its cache file name (used by the seed script)."""
        key = cache_key(round(lat, 4), round(lon, 4))
        cached = self._read_disk(key)
        if cached is not None:
            return key, cached
        raw = await self._fetch(round(lat, 4), round(lon, 4))
        parse(raw)  # validate before anyone stores it
        self._write(self._cache_dir / key, raw)
        return key, raw

    async def _search(self, lat: float, lon: float) -> SoilEstimate:
        for point_lat, point_lon, distance_km in search_points(lat, lon):
            _, raw = await self.raw(point_lat, point_lon)
            point = parse(raw)
            if point.has_data:
                return SoilEstimate(
                    requested_lat=lat,
                    requested_lon=lon,
                    sampled_lat=point_lat,
                    sampled_lon=point_lon,
                    distance_km=distance_km,
                    root_zone_texture=depth_weighted_texture(point, ROOT_ZONE_BOTTOM_CM),
                    zarc_layer_texture=depth_weighted_texture(point, ZARC_LAYER_BOTTOM_CM),
                    topsoil_texture=depth_weighted_texture(point, TOPSOIL_BOTTOM_CM),
                )
        rings = ", ".join(f"{distance:g}" for distance in SEARCH_RINGS_KM)
        raise SoilGridsNoData(f"no SoilGrids data at {lat}, {lon} nor within {rings} km")

    def _read_disk(self, key: str) -> dict | None:
        for folder in (self._seed_dir, self._cache_dir):
            path = folder / key
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    log.warning("unreadable SoilGrids cache, ignoring: %s", path)
        return None

    @staticmethod
    def _write(path: Path, raw: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    async def _fetch(self, lat: float, lon: float) -> dict:
        params: list[tuple[str, str | float]] = [("lon", lon), ("lat", lat)]
        params += [("property", name) for name in PROPERTIES]
        params += [("depth", depth) for depth in DEPTHS]
        params.append(("value", "mean"))
        url = f"{self._base_url}/soilgrids/v2.0/properties/query"
        error = ""
        for attempt in range(ATTEMPTS):
            async with self._network_lock:  # fair use: one call at a time, spaced
                wait = self._last_call + self._min_interval_s - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    resp = await self._http.get(url, params=params)
                except httpx.TransportError as exc:
                    resp, error = None, f"network: {exc!r}"
                finally:
                    self._last_call = time.monotonic()
            if resp is not None:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise SoilGridsInvalidResponse(
                            f"SoilGrids answer is not JSON: {exc}"
                        ) from exc
                if resp.status_code != 429 and resp.status_code < 500:
                    raise SoilGridsInvalidResponse(
                        f"SoilGrids {resp.status_code}: {resp.text[:300]}"
                    )
                error = f"HTTP {resp.status_code}"
            if attempt < ATTEMPTS - 1:
                pause = self._backoff_s[min(attempt, len(self._backoff_s) - 1)]
                log.warning("SoilGrids failed (%s), retrying in %.0f s", error, pause)
                await asyncio.sleep(pause)
        raise SoilGridsUnavailable(f"SoilGrids unavailable after {ATTEMPTS} attempts ({error})")
