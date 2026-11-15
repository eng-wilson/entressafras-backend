"""Build app/data/prices/crop_economics.json: farm-gate price, yield and gross revenue per crop.

Sources (all CC BY 4.0):
- FAOSTAT Producer Prices (PP): annual price received by farmers, USD/tonne, per country.
- FAOSTAT Crops and livestock products (QCL): yield, kg/ha, per country or region.
- World Bank Commodity Price Data (Pink Sheet): monthly international benchmark prices.

The FAOSTAT values are the mean of the latest N years with data (default 3). Crops without a
series for the country use a declared proxy (another crop or region), flagged as method "proxy".
Downloads are kept in .cache/ so a rebuild does not fetch them again.

Usage: uv run --with openpyxl python -m scripts.build_crop_economics [--years 3]
"""

import argparse
import csv
import io
import json
import re
import statistics
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from app.core.config import PROJECT_DIR
from app.services.crop_economics import ECONOMICS_FILE
from app.services.rotations import (
    COMMON_BEAN,
    COTTON,
    COWPEA,
    GROUNDNUT,
    MAIZE,
    MILLET,
    SORGHUM,
    SOYBEAN,
    SUNFLOWER,
)

FAOSTAT_PRICES_URL = "https://bulks-faostat.fao.org/production/Prices_E_All_Data_(Normalized).zip"
FAOSTAT_PRODUCTION_URL = (
    "https://bulks-faostat.fao.org/production/"
    "Production_Crops_Livestock_E_All_Data_(Normalized).zip"
)
# The document id changes every year; take the current one from worldbank.org/commodities.
PINKSHEET_URL = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)
FAOSTAT_PRICES_PAGE = "https://www.fao.org/faostat/en/#data/PP"
FAOSTAT_PRODUCTION_PAGE = "https://www.fao.org/faostat/en/#data/QCL"
PINKSHEET_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
CACHE_DIR = PROJECT_DIR / ".cache" / "crop_economics"

PRICE_ELEMENT = "Producer Price (USD/tonne)"
YIELD_ELEMENT = "Yield"
ANNUAL = "Annual value"
# Pink Sheet series whose last value is older than this (vs the file's last month) are dropped:
# the World Bank stops some series (e.g. sorghum) and leaves "…" afterwards.
MAX_PINKSHEET_AGE_MONTHS = 12

SeriesKey = tuple[str, str, str]  # (area, item, element)


@dataclass(frozen=True)
class FaostatSeries:
    item: str
    area: str | None = None  # None = the target country
    proxy_note: str | None = None  # set when this series stands in for the crop's own


@dataclass(frozen=True)
class PinkSheetSeries:
    column: str
    factor: float = 1.0  # to USD/t
    note: str | None = None


@dataclass(frozen=True)
class CropSources:
    price: FaostatSeries
    crop_yield: FaostatSeries
    international: PinkSheetSeries | None = None


def _same(item: str, international: PinkSheetSeries | None = None) -> CropSources:
    return CropSources(FaostatSeries(item), FaostatSeries(item), international)


NO_BRAZIL_MILLET = "FAOSTAT has no millet series for the country"
NO_BRAZIL_COWPEA = (
    "FAOSTAT has no cowpea series for the country; Brazilian 'feijão' statistics include "
    "cowpea (Vigna) together with common bean (Phaseolus)"
)

CROP_ECONOMIC_SOURCES: dict[int, CropSources] = {
    SOYBEAN: _same("Soya beans", PinkSheetSeries("Soybeans")),
    MAIZE: _same("Maize (corn)", PinkSheetSeries("Maize")),
    SORGHUM: _same("Sorghum", PinkSheetSeries("Sorghum")),
    MILLET: CropSources(
        price=FaostatSeries("Sorghum", proxy_note=f"{NO_BRAZIL_MILLET}; priced as sorghum"),
        crop_yield=FaostatSeries(
            "Millet", area="South America", proxy_note=f"{NO_BRAZIL_MILLET}; regional yield"
        ),
    ),
    COTTON: _same(
        "Seed cotton, unginned",
        PinkSheetSeries(
            "Cotton, A Index",
            factor=1000.0,
            note="Cotton lint (Cotlook A Index), not seed cotton; converted from USD/kg",
        ),
    ),
    COMMON_BEAN: _same("Beans, dry"),
    COWPEA: CropSources(
        price=FaostatSeries("Beans, dry", proxy_note=NO_BRAZIL_COWPEA),
        crop_yield=FaostatSeries("Beans, dry", proxy_note=NO_BRAZIL_COWPEA),
    ),
    SUNFLOWER: _same("Sunflower seed"),  # the Pink Sheet only has sunflower oil
    GROUNDNUT: _same(
        "Groundnuts, excluding shelled",
        PinkSheetSeries("Groundnuts", note="Shelled groundnuts; the farm-gate price is in shell"),
    ),
}


def collect_faostat(
    rows: Iterable[Mapping[str, str]], wanted: set[SeriesKey]
) -> dict[SeriesKey, dict[int, float]]:
    """Annual values of the wanted (area, item, element) series, by year."""
    series: dict[SeriesKey, dict[int, float]] = defaultdict(dict)
    for row in rows:
        key = (row["Area"], row["Item"], row["Element"])
        if key not in wanted or row.get("Months", ANNUAL) != ANNUAL or not row["Value"]:
            continue
        series[key][int(row["Year"])] = float(row["Value"])
    return dict(series)


def latest_mean(by_year: Mapping[int, float], years: int) -> tuple[int, int, float]:
    """(first year, last year, mean) over the latest `years` years with data."""
    if not by_year:
        raise ValueError("empty series")
    selected = sorted(by_year)[-years:]
    return selected[0], selected[-1], statistics.fmean(by_year[year] for year in selected)


def _month_index(period: str) -> int:
    match = re.fullmatch(r"(\d{4})M(\d{2})", period.strip())
    if match is None:
        raise ValueError(f"not a Pink Sheet month: {period!r}")
    return int(match[1]) * 12 + int(match[2]) - 1


def latest_pinksheet(
    periods: list[str], values: list[object], last_period: str
) -> tuple[str, float] | None:
    """Last numeric value as ('YYYY-MM', value); None if missing or stale."""
    for period, value in zip(reversed(periods), reversed(values), strict=True):
        if _is_number(value):
            if _month_index(last_period) - _month_index(period) > MAX_PINKSHEET_AGE_MONTHS:
                return None
            return f"{period[:4]}-{period[5:7]}", float(value)
    return None


def download(url: str) -> Path:
    path = CACHE_DIR / url.rsplit("/", 1)[-1]
    if not path.is_file():
        with httpx.Client(timeout=600, follow_redirects=True) as http:
            resp = http.get(url)
            resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return path


def read_faostat_zip(path: Path, wanted: set[SeriesKey]) -> dict[SeriesKey, dict[int, float]]:
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith("All_Data_(Normalized).csv"))
        with archive.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            return collect_faostat(csv.DictReader(text), wanted)


PinkSheet = tuple[list[str], dict[str, tuple[str, list[object]]], str]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and value == value  # rejects "…" and NaN


def read_pinksheet(path: Path) -> PinkSheet:
    """Periods, {column: (unit, values)} and last month with data of 'Monthly Prices'."""
    import pandas as pd  # openpyxl is a build-time dependency: uv run --with openpyxl

    sheet = pd.read_excel(path, sheet_name="Monthly Prices", header=4)
    units, data = sheet.iloc[0], sheet.iloc[1:]
    periods = [str(period) for period in data.iloc[:, 0]]
    columns = {
        str(column).strip(): (str(units[column]), list(data[column]))
        for column in sheet.columns[1:]
    }
    last_period = next(
        period
        for index, period in reversed(list(enumerate(periods)))
        if any(_is_number(values[index]) for _, values in columns.values())
    )
    return periods, columns, last_period


def _area(series: FaostatSeries, country: str) -> str:
    return series.area or country


def _faostat_value(
    series: FaostatSeries,
    element: str,
    unit: str,
    data: dict[SeriesKey, dict[int, float]],
    country: str,
    years: int,
    source: str,
    url: str,
) -> dict:
    area = _area(series, country)
    by_year = data.get((area, series.item, element))
    if not by_year:
        raise SystemExit(f"FAOSTAT has no {element!r} for {series.item!r} in {area!r}")
    first, last, mean = latest_mean(by_year, years)
    return {
        "value": round(mean, 1),
        "unit": unit,
        "period": str(first) if first == last else f"{first}-{last}",
        "area": area,
        "method": "proxy" if series.proxy_note else "direct",
        "proxy_of": f"{series.item} ({area})" if series.proxy_note else None,
        "source": source,
        "series": series.item,
        "url": url,
        "note": series.proxy_note,
    }


def _international(series: PinkSheetSeries | None, pinksheet: PinkSheet) -> dict | None:
    if series is None:
        return None
    periods, columns, last_period = pinksheet
    unit, values = columns[series.column]
    latest = latest_pinksheet(periods, values, last_period)
    if latest is None:
        print(f"{series.column}: no Pink Sheet value in the last {MAX_PINKSHEET_AGE_MONTHS} months")
        return None
    period, value = latest
    if series.factor == 1.0 and unit != "($/mt)":
        raise SystemExit(f"{series.column}: unexpected unit {unit}")
    return {
        "value": round(value * series.factor, 1),
        "unit": "USD/t",
        "period": period,
        "area": "World market",
        "method": "direct",
        "proxy_of": None,
        "source": "World Bank Commodity Price Data (Pink Sheet)",
        "series": series.column,
        "url": PINKSHEET_PAGE,
        "note": series.note,
    }


def build(country: str, years: int, prices_url: str, production_url: str, pinksheet_url: str):
    price_keys = {
        (_area(s.price, country), s.price.item, PRICE_ELEMENT)
        for s in CROP_ECONOMIC_SOURCES.values()
    }
    yield_keys = {
        (_area(s.crop_yield, country), s.crop_yield.item, YIELD_ELEMENT)
        for s in CROP_ECONOMIC_SOURCES.values()
    }
    prices = read_faostat_zip(download(prices_url), price_keys)
    production = read_faostat_zip(download(production_url), yield_keys)
    pinksheet = read_pinksheet(download(pinksheet_url))

    crops = {}
    for code, sources in CROP_ECONOMIC_SOURCES.items():
        price = _faostat_value(
            sources.price,
            PRICE_ELEMENT,
            "USD/t",
            prices,
            country,
            years,
            "FAOSTAT Producer Prices (PP)",
            FAOSTAT_PRICES_PAGE,
        )
        crop_yield = _faostat_value(
            sources.crop_yield,
            YIELD_ELEMENT,
            "kg/ha",
            production,
            country,
            years,
            "FAOSTAT Crops and livestock products (QCL)",
            FAOSTAT_PRODUCTION_PAGE,
        )
        crops[str(code)] = {
            "farm_gate_price": price,
            "crop_yield": crop_yield,
            "gross_revenue_usd_ha": round(crop_yield["value"] / 1000 * price["value"], 1),
            "international_price": _international(sources.international, pinksheet),
        }
        print(
            f"{code}: {price['value']} USD/t × {crop_yield['value']} kg/ha "
            f"= {crops[str(code)]['gross_revenue_usd_ha']} USD/ha"
        )
    return {
        "generated": date.today().isoformat(),
        "country": country,
        "years_averaged": years,
        "sources": {
            "faostat_prices": prices_url,
            "faostat_production": production_url,
            "pinksheet": pinksheet_url,
        },
        "crops": crops,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--country", default="Brazil", help="FAOSTAT area name")
    parser.add_argument("--years", type=int, default=3, help="Latest years averaged")
    parser.add_argument("--faostat-prices-url", default=FAOSTAT_PRICES_URL)
    parser.add_argument("--faostat-production-url", default=FAOSTAT_PRODUCTION_URL)
    parser.add_argument("--pinksheet-url", default=PINKSHEET_URL)
    args = parser.parse_args()
    data = build(
        args.country,
        args.years,
        args.faostat_prices_url,
        args.faostat_production_url,
        args.pinksheet_url,
    )
    ECONOMICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ECONOMICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"{ECONOMICS_FILE}: {len(data['crops'])} crops")


if __name__ == "__main__":
    main()
