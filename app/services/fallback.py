"""Helpers that keep the API answering when an external source (POWER, SoilGrids) is down.

- `CircuitBreaker`: after an outage, skip the network for a while so the next requests go
  straight to the cached fallback instead of waiting for another timeout.
- `distance_km`: great-circle distance, to pick the nearest cached point or cell.
"""

import math
import time

EARTH_RADIUS_KM = 6371.0
DEFAULT_COOLDOWN_S = 300.0


def distance_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Haversine distance between two points, in km."""
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    half_dphi = (phi_b - phi_a) / 2
    half_dlambda = math.radians(lon_b - lon_a) / 2
    h = math.sin(half_dphi) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(half_dlambda) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


class CircuitBreaker:
    """Open for `cooldown_s` after `trip()`; `is_open` tells callers to skip the network."""

    def __init__(self, cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
        self.cooldown_s = cooldown_s
        self._open_until = -math.inf

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    def trip(self) -> None:
        self._open_until = time.monotonic() + self.cooldown_s
