"""Parâmetros de consumo hídrico FAO-56 do CropWat (INRAE), ligados às culturas do EcoCrop.

O TSV traz 55 perfis; só os `FAO-*` (padrões do CropWat 8 / FAO-56) são usados. Os `KURN-*`
(Índia) e `SB2023-*` (França, sem Ky) são calibrações regionais e ficam de fora.
"""

import csv
from pathlib import Path

from app.schemas.crop import (
    AllowableDepletion,
    CropCoefficient,
    RootDepth,
    StageLengths,
    WaterRequirements,
    YieldResponseFactor,
)
from app.services.ecocrop import _int, _num

PROFILE_PREFIX = "FAO-"

# EcoPortCode (EcoCrop) -> perfil CropWat. Curado à mão; códigos conferidos no EcoCrop_DB.csv.
ECOCROP_TO_CROPWAT: dict[int, str] = {
    2175: "FAO-MAIZE",  # milho (Zea mays)
    238663: "FAO-MAIZE",  # milho (Zea mays ssp. mays)
    1150: "FAO-SOYBEAN",  # soja
    1668: "FAO-BEANS-DR",  # feijão comum
    2153: "FAO-PULSES",  # feijão-caupi (sem perfil próprio)
    576: "FAO-PULSES",  # feijão-guandu (sem perfil próprio)
    48747: "FAO-SORGHUM",  # sorgo
    8418: "FAO-MILLET",  # milheto
    2114: "FAO-WHEAT",  # trigo
    1232: "FAO-BARLEY",  # cevada
    1160: "FAO-COTTON",  # algodão
    2199: "FAO-GRONDNUT",  # amendoim
    1191: "FAO-SUNFLOWR",  # girassol
    1884: "FAO-SUGARCAN",  # cana-de-açúcar
    48742: "FAO-SUGARBET",  # beterraba açucareira
    1971: "FAO-POTATO",  # batata
    1379: "FAO-TOMATO",  # tomate
    618: "FAO-PEPPER",  # pimentão
    815: "FAO-SW-MELON",  # melão
    554: "FAO-CABBAGE",  # repolho
    1534: "FAO-TOBACCO",  # fumo
    1428: "FAO-ALFALFA0",  # alfafa
}


def _req_num(row: dict[str, str], column: str) -> float:
    value = _num(row[column])
    if value is None:
        raise ValueError(f"Perfil {row['crop']}: coluna obrigatória {column} vazia")
    return value


def _req_int(row: dict[str, str], column: str) -> int:
    value = _int(row[column])
    if value is None:
        raise ValueError(f"Perfil {row['crop']}: coluna obrigatória {column} vazia")
    return value


def parse_profile(row: dict[str, str]) -> WaterRequirements:
    stages = [_req_int(row, c) for c in ("Lini", "Ldev", "Lmid", "Lend")]
    return WaterRequirements(
        profile=row["crop"].strip(),
        profile_name=" ".join(row["name"].split()),
        crop_coefficient=CropCoefficient(
            initial=_req_num(row, "Kini"),
            mid_season=_req_num(row, "Kmax"),
            late_season=_req_num(row, "Kend"),
        ),
        stage_length_days=StageLengths(
            initial=stages[0],
            development=stages[1],
            mid_season=stages[2],
            late_season=stages[3],
            total=sum(stages),
        ),
        root_depth_m=RootDepth(initial=_req_num(row, "Zini"), max=_req_num(row, "Zend")),
        allowable_depletion=AllowableDepletion(
            initial=_req_num(row, "p_ini"),
            mid_season=_req_num(row, "p_mid"),
            late_season=_req_num(row, "p_end"),
        ),
        yield_response_factor=YieldResponseFactor(
            initial=_num(row["Yini"]),
            development=_num(row["Ydev"]),
            mid_season=_num(row["Ymid"]),
            late_season=_num(row["Yend"]),
            season=_num(row["Ytot"]),
        ),
        max_height_m=_num(row["h"]),
    )


def load_profiles(path: Path) -> dict[str, WaterRequirements]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.DictReader(f, delimiter="\t")
        profiles = [parse_profile(r) for r in rows if r["crop"].startswith(PROFILE_PREFIX)]
    return {p.profile: p for p in profiles}


def water_by_code(
    path: Path, mapping: dict[int, str] = ECOCROP_TO_CROPWAT
) -> dict[int, WaterRequirements]:
    profiles = load_profiles(path)
    missing = sorted(set(mapping.values()) - profiles.keys())
    if missing:
        raise ValueError(f"Perfis CropWat do mapeamento ausentes em {path.name}: {missing}")
    return {code: profiles[name] for code, name in mapping.items()}
