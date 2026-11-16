"""Future climate scenarios by the delta (change factor) method, with no I/O.

The observed daily series is perturbed and the simulation is rerun unchanged: temperature gets
+ΔT (Tmax and Tmin; dew point and relative humidity are kept, as in the usual delta method),
rainfall is scaled by (1 + Δ%), and an optional onset delay shifts the rainfall of 15 Aug–31 Dec
later in the season. ET0 is recomputed with the same FAO-56 routine used for the observed series.
Ported from the prototype (entressafras/src/climate/fonte-clima.js, `comCenario`).

These are "what if" scenarios over the historical record, not a forecast of the next season.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from app.services.agro import DiaClima, et0_dia

ScenarioId = Literal["historical", "warmer", "warmer_drier"]
# Window (month, day) whose rainfall is shifted by `onset_delay_days` (rainy season onset).
ONSET_WINDOW = ((8, 15), (12, 31))
ILLUSTRATIVE_SOURCE = (
    "Illustrative values, to be validated against the NASA NEX-GDDP-CMIP6 multi-model mean "
    "(SSP2-4.5, 2021–2040 vs 1995–2014, Brazilian Central-West)"
)


@dataclass(frozen=True)
class ClimateScenario:
    id: ScenarioId
    label: str
    delta_temp_c: float = 0.0
    rain_change_pct: float = 0.0
    onset_delay_days: int = 0
    description: str = ""
    source: str = ""

    @property
    def is_historical(self) -> bool:
        return not (self.delta_temp_c or self.rain_change_pct or self.onset_delay_days)


# TODO(validate): replace the deltas with the NEX-GDDP-CMIP6 (or IPCC Interactive Atlas)
# multi-model mean for the region and update `source`.
SCENARIOS: dict[ScenarioId, ClimateScenario] = {
    "historical": ClimateScenario(
        id="historical",
        label="Current climate (30 seasons)",
        description="Observed climate, no adjustment",
        source="NASA POWER",
    ),
    "warmer": ClimateScenario(
        id="warmer",
        label="2030: warmer",
        delta_temp_c=1.0,
        description="+1.0 °C",
        source=ILLUSTRATIVE_SOURCE,
    ),
    "warmer_drier": ClimateScenario(
        id="warmer_drier",
        label="2030: warmer and drier",
        delta_temp_c=1.5,
        rain_change_pct=-10.0,
        onset_delay_days=10,
        description="+1.5 °C, −10% rainfall, rainy season starts 10 days later",
        source=ILLUSTRATIVE_SOURCE,
    ),
}


def _in_onset_window(day: date) -> bool:
    return ONSET_WINDOW[0] <= (day.month, day.day) <= ONSET_WINDOW[1]


def _plus(value: float | None, delta: float) -> float | None:
    return None if value is None else value + delta


def apply_scenario(
    days: Sequence[DiaClima], scenario: ClimateScenario, *, elev: float, lat: float
) -> list[DiaClima]:
    """The daily series perturbed by the scenario, with ET0 recomputed (same order and dates)."""
    if scenario.is_historical:
        return list(days)
    factor = 1 + scenario.rain_change_pct / 100
    rain = {day.data: None if day.chuva is None else day.chuva * factor for day in days}
    delay = timedelta(days=scenario.onset_delay_days)

    result = []
    for day in days:
        rainfall = rain[day.data]
        if scenario.onset_delay_days and _in_onset_window(day.data):
            # Rainfall of `delay` days earlier; 0 before the start of the series.
            rainfall = rain.get(day.data - delay, 0.0)
        tmax, tmin = _plus(day.tmax, scenario.delta_temp_c), _plus(day.tmin, scenario.delta_temp_c)
        et0, method = et0_dia(
            data=day.data,
            tmax=tmax,
            tmin=tmin,
            tdew=day.tdew,
            ur=day.ur,
            u2=day.vento,
            rs=day.rad,
            elev=elev,
            lat=lat,
        )
        result.append(
            replace(day, tmax=tmax, tmin=tmin, chuva=rainfall, et0=et0, et0_metodo=method)
        )
    return result
