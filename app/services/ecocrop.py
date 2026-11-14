"""Acesso à base FAO EcoCrop (CSV em latin-1), carregada uma vez em memória."""

import csv
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.schemas.crop import (
    CropCycle,
    CropDetail,
    CropSummary,
    NumericRange,
    SoilRequirements,
    TemperatureRequirements,
    TextOptions,
    TextRange,
)

_MISSING = {"", "NA"}
SUMMARY_NAMES = 5


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in _MISSING else value


def _num(value: str | None) -> float | None:
    text = _text(value)
    return None if text is None else float(text)


def _int(value: str | None) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _list(value: str | None) -> list[str]:
    text = _text(value)
    if text is None:
        return []
    return [part.strip() for part in text.split(",") if part.strip() not in _MISSING]


def normalize(value: str) -> str:
    """Minúsculas, sem acentos e com espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def _range(row: dict[str, str], omn: str, omx: str, amn: str, amx: str) -> dict:
    return {
        "optimal_min": _num(row[omn]),
        "optimal_max": _num(row[omx]),
        "absolute_min": _num(row[amn]),
        "absolute_max": _num(row[amx]),
    }


def _options(row: dict[str, str], optimal: str, absolute: str) -> TextOptions:
    return TextOptions(optimal=_list(row[optimal]), absolute=_list(row[absolute]))


def parse_row(row: dict[str, str]) -> CropDetail:
    return CropDetail(
        eco_port_code=int(row["EcoPortCode"]),
        scientific_name=row["ScientificName"].strip(),
        authority=_text(row["AUTH"]),
        family=_text(row["FAMNAME"]),
        synonyms=_text(row["SYNO"]),
        common_names=_list(row["COMNAME"]),
        life_form=_list(row["LIFO"]),
        habit=_list(row["HABI"]),
        life_span=_list(row["LISPA"]),
        physiology=_list(row["PHYS"]),
        category=_list(row["CAT"]),
        plant_attributes=_list(row["PLAT"]),
        temperature=TemperatureRequirements(
            **_range(row, "TOPMN", "TOPMX", "TMIN", "TMAX"),
            killing_rest=_num(row["KTMPR"]),
            killing_early_growth=_num(row["KTMP"]),
        ),
        rainfall=NumericRange(**_range(row, "ROPMN", "ROPMX", "RMIN", "RMAX")),
        soil_ph=NumericRange(**_range(row, "PHOPMN", "PHOPMX", "PHMIN", "PHMAX")),
        latitude=NumericRange(**_range(row, "LATOPMN", "LATOPMX", "LATMN", "LATMX")),
        altitude_max=_num(row["ALTMX"]),
        light_intensity=TextRange(
            optimal_min=_text(row["LIOPMN"]),
            optimal_max=_text(row["LIOPMX"]),
            absolute_min=_text(row["LIMN"]),
            absolute_max=_text(row["LIMX"]),
        ),
        soil=SoilRequirements(
            depth=_options(row, "DEP", "DEPR"),
            texture=_options(row, "TEXT", "TEXTR"),
            fertility=_options(row, "FER", "FERR"),
            al_toxicity=_options(row, "TOX", "TOXR"),
            salinity=_options(row, "SAL", "SALR"),
            drainage=_options(row, "DRA", "DRAR"),
        ),
        photoperiod=_list(row["PHOTO"]),
        climate_zones=_list(row["CLIZ"]),
        abiotic_tolerance=_list(row["ABITOL"]),
        abiotic_susceptibility=_list(row["ABISUS"]),
        introduction_risk=_text(row["INTRI"]),
        production_system=_list(row["PROSY"]),
        crop_cycle=CropCycle(min_days=_int(row["GMIN"]), max_days=_int(row["GMAX"])),
    )


@dataclass(frozen=True)
class _Entry:
    crop: CropDetail
    names: tuple[str, ...]  # nome científico + nomes populares, normalizados
    category: frozenset[str]
    life_span: frozenset[str]
    life_form: frozenset[str]


class CropRepository:
    def __init__(self, crops: list[CropDetail]) -> None:
        crops = sorted(crops, key=lambda c: c.scientific_name.casefold())
        self._entries = [
            _Entry(
                crop=c,
                names=tuple(normalize(n) for n in [c.scientific_name, *c.common_names]),
                category=frozenset(normalize(v) for v in c.category),
                life_span=frozenset(normalize(v) for v in c.life_span),
                life_form=frozenset(normalize(v) for v in c.life_form),
            )
            for c in crops
        ]
        self._by_code = {e.crop.eco_port_code: e.crop for e in self._entries}

    @classmethod
    def from_csv(cls, path: Path) -> "CropRepository":
        with path.open(encoding="latin-1", newline="") as f:
            return cls([parse_row(row) for row in csv.DictReader(f)])

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, eco_port_code: int) -> CropDetail | None:
        return self._by_code.get(eco_port_code)

    def search(
        self,
        q: str | None = None,
        *,
        category: str | None = None,
        life_span: str | None = None,
        life_form: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[CropSummary]]:
        filters = [
            (attr, normalize(value))
            for attr, value in (
                ("category", category),
                ("life_span", life_span),
                ("life_form", life_form),
            )
            if value
        ]
        term = normalize(q) if q else ""

        ranked: list[tuple[int, int, _Entry]] = []
        for index, entry in enumerate(self._entries):
            if any(value not in getattr(entry, attr) for attr, value in filters):
                continue
            rank = _match_rank(term, entry.names) if term else 0
            if rank is not None:
                ranked.append((rank, index, entry))
        ranked.sort(key=lambda item: (item[0], item[1]))

        page = ranked[offset : offset + limit]
        return len(ranked), [_summary(entry.crop) for _, _, entry in page]


def _match_rank(term: str, names: tuple[str, ...]) -> int | None:
    """0 = exato, 1 = prefixo, 2 = contém, None = sem correspondência."""
    best: int | None = None
    for name in names:
        if name == term:
            return 0
        if name.startswith(term):
            best = 1
        elif best is None and term in name:
            best = 2
    return best


def _summary(crop: CropDetail) -> CropSummary:
    return CropSummary(
        eco_port_code=crop.eco_port_code,
        scientific_name=crop.scientific_name,
        common_names=crop.common_names[:SUMMARY_NAMES],
        category=crop.category,
        life_span=crop.life_span,
    )


@lru_cache
def get_repository() -> CropRepository:
    return CropRepository.from_csv(get_settings().ecocrop_csv_path)
