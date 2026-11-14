"""Cálculos agrometeorológicos puros (sem rede): ET0 FAO-56, decêndios e climatologia.

Referência: Allen et al. (1998), FAO Irrigation and Drainage Paper 56, cap. 3 e 4.
Decêndio: 36 por safra (setembro a agosto); 0 = 1–10/set, 35 = 21–31/ago.
O 3º decêndio do mês vai do dia 21 ao último dia (8 a 11 dias).
"""

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

GSC = 0.0820  # constante solar, MJ/m²/min
SIGMA = 4.903e-9  # Stefan-Boltzmann, MJ/K⁴/m²/dia
ALBEDO = 0.23
MIN_SAFRAS_VALIDAS = 5
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


# --- ET0 ---------------------------------------------------------------------------------------


def esat(t: float) -> float:
    """Pressão de saturação de vapor (kPa), eq. 11."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def radiacao_extraterrestre(lat: float, doy: int) -> float:
    """Ra (MJ/m²/dia), eq. 21."""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (
        24 * 60 / math.pi * GSC * dr
        * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )


def et0_penman_monteith(
    *, tmax: float, tmin: float, ea: float, u2: float, rs: float, elev: float, lat: float, doy: int
) -> float:
    """ET0 diária (mm/dia), eq. 6, com Rs medido e pressão real de vapor `ea` (kPa)."""
    tmed = (tmax + tmin) / 2
    p = 101.3 * ((293 - 0.0065 * elev) / 293) ** 5.26  # eq. 7
    gama = 0.000665 * p  # eq. 8
    delta = 4098 * esat(tmed) / (tmed + 237.3) ** 2  # eq. 13
    es = (esat(tmax) + esat(tmin)) / 2  # eq. 12
    ea = min(ea, es)
    ra = radiacao_extraterrestre(lat, doy)
    rso = (0.75 + 2e-5 * elev) * ra  # eq. 37
    rs = min(rs, rso)
    rns = (1 - ALBEDO) * rs  # eq. 38
    tk4 = ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2
    rnl = SIGMA * tk4 * (0.34 - 0.14 * math.sqrt(ea)) * (1.35 * rs / rso - 0.35)  # eq. 39
    rn = rns - rnl  # eq. 40
    num = 0.408 * delta * rn + gama * 900 / (tmed + 273) * u2 * (es - ea)
    return max(0.0, num / (delta + gama * (1 + 0.34 * u2)))


def et0_hargreaves(*, tmax: float, tmin: float, lat: float, doy: int) -> float:
    """ET0 diária (mm/dia), eq. 52; Ra convertida para mm/dia (× 0,408)."""
    ra = radiacao_extraterrestre(lat, doy)
    tmed = (tmax + tmin) / 2
    return max(0.0, 0.0023 * (tmed + 17.8) * math.sqrt(max(0.0, tmax - tmin)) * 0.408 * ra)


def et0_dia(
    *,
    data: date,
    tmax: float | None,
    tmin: float | None,
    tdew: float | None,
    ur: float | None,
    u2: float | None,
    rs: float | None,
    elev: float,
    lat: float,
) -> tuple[float | None, str | None]:
    """ET0 do dia com reservas. Devolve (valor, método); (None, None) sem temperatura.

    Métodos: "pm-orvalho" (ea pelo ponto de orvalho, eq. 14), "pm-ur" (ea pela UR média,
    eq. 19) e "hargreaves" (estimado, sem radiação, vento ou umidade).
    """
    if tmax is None or tmin is None:
        return None, None
    doy = data.timetuple().tm_yday
    if u2 is not None and rs is not None:
        if tdew is not None:
            ea, metodo = esat(tdew), "pm-orvalho"
        elif ur is not None:
            ea, metodo = ur / 100 * (esat(tmax) + esat(tmin)) / 2, "pm-ur"
        else:
            ea, metodo = None, None
        if ea is not None:
            valor = et0_penman_monteith(
                tmax=tmax, tmin=tmin, ea=ea, u2=u2, rs=rs, elev=elev, lat=lat, doy=doy
            )
            return valor, metodo
    return et0_hargreaves(tmax=tmax, tmin=tmin, lat=lat, doy=doy), "hargreaves"


# --- Decêndios ---------------------------------------------------------------------------------


def safra_de(d: date) -> int:
    """Ano em que começa a safra (setembro) que contém `d`."""
    return d.year if d.month >= 9 else d.year - 1


def rotulo_safra(ano: int) -> str:
    return f"{ano}/{(ano + 1) % 100:02d}"


def decendio_idx(d: date) -> int:
    mes_safra = (d.month - 9) % 12  # set = 0 … ago = 11
    return mes_safra * 3 + min(2, (d.day - 1) // 10)


def rotulo_decendio(idx: int) -> str:
    mes = MESES[(idx // 3 + 8) % 12]
    return [f"1-10 {mes}", f"11-20 {mes}", f"21-fim {mes}"][idx % 3]


@dataclass(frozen=True)
class DiaClima:
    data: date
    tmax: float | None
    tmin: float | None
    tdew: float | None
    ur: float | None
    vento: float | None
    rad: float | None
    chuva: float | None
    gwettop: float | None
    gwetroot: float | None
    et0: float | None
    et0_metodo: str | None


@dataclass
class ResumoDecendio:
    idx: int
    rotulo: str
    dias: int
    anos_validos: int
    dias_preenchidos: int
    chuva_media: float | None
    chuva_p20: float | None
    et0_media: float | None
    tmin_media: float | None
    tmin_extremo_medio: float | None
    tmin_absoluto: float | None
    tmax_media: float | None
    tmax_extremo_medio: float | None
    tmax_absoluto: float | None
    gwettop_media: float | None
    gwetroot_media: float | None


def _validos(valores: Sequence[float | None]) -> list[float] | None:
    """Valores válidos, ou None se menos da metade dos dias tiver dado."""
    ok = [v for v in valores if v is not None]
    return ok if ok and len(ok) * 2 >= len(valores) else None


def _total(valores: Sequence[float | None]) -> float | None:
    """Soma do decêndio, preenchendo lacunas pela média dos dias válidos."""
    ok = _validos(valores)
    return None if ok is None else sum(ok) / len(ok) * len(valores)


def _media(valores: Sequence[float | None]) -> float | None:
    ok = _validos(valores)
    return None if ok is None else sum(ok) / len(ok)


def _extremo(valores: Sequence[float | None], fn: Callable[[list[float]], float]) -> float | None:
    ok = _validos(valores)
    return None if ok is None else fn(ok)


def _entre_safras(
    valores: Sequence[float | None], fn: Callable[[list[float]], float]
) -> float | None:
    ok = [v for v in valores if v is not None]
    return round(fn(ok), 2) if len(ok) >= MIN_SAFRAS_VALIDAS else None


def p20(valores: list[float]) -> float:
    """Valor superado em 8 de cada 10 anos (1º corte de quintis, método inclusivo)."""
    return statistics.quantiles(valores, n=5, method="inclusive")[0]


def agregar_decendios(dias: Sequence[DiaClima]) -> list[ResumoDecendio]:
    """Climatologia por decêndio a partir da série diária de safras completas."""
    grupos: dict[int, dict[int, list[DiaClima]]] = {i: {} for i in range(36)}
    for dia in dias:
        grupos[decendio_idx(dia.data)].setdefault(safra_de(dia.data), []).append(dia)

    resumo = []
    for idx in range(36):
        por_safra = grupos[idx]
        chuva, et0, gwettop, gwetroot = [], [], [], []
        tmin_m, tmin_x, tmax_m, tmax_x = [], [], [], []
        tmin_abs: list[float] = []
        tmax_abs: list[float] = []
        preenchidos = 0
        for lista in por_safra.values():
            col = {c: [getattr(d, c) for d in lista] for c in DiaClima.__dataclass_fields__}
            chuva.append(_total(col["chuva"]))
            et0.append(_total(col["et0"]))
            gwettop.append(_media(col["gwettop"]))
            gwetroot.append(_media(col["gwetroot"]))
            tmin_m.append(_media(col["tmin"]))
            tmin_x.append(_extremo(col["tmin"], min))
            tmax_m.append(_media(col["tmax"]))
            tmax_x.append(_extremo(col["tmax"], max))
            tmin_abs += [v for v in col["tmin"] if v is not None]
            tmax_abs += [v for v in col["tmax"] if v is not None]
            if chuva[-1] is not None:
                preenchidos += sum(v is None for v in col["chuva"])
        n_dias = round(statistics.mean(len(v) for v in por_safra.values())) if por_safra else 0
        anos_validos = sum(v is not None for v in chuva)
        ok = anos_validos >= MIN_SAFRAS_VALIDAS
        resumo.append(
            ResumoDecendio(
                idx=idx,
                rotulo=rotulo_decendio(idx),
                dias=n_dias,
                anos_validos=anos_validos,
                dias_preenchidos=preenchidos,
                chuva_media=_entre_safras(chuva, statistics.mean),
                chuva_p20=_entre_safras(chuva, p20),
                et0_media=_entre_safras(et0, statistics.mean),
                tmin_media=_entre_safras(tmin_m, statistics.mean),
                tmin_extremo_medio=_entre_safras(tmin_x, statistics.mean),
                tmin_absoluto=round(min(tmin_abs), 2) if ok and tmin_abs else None,
                tmax_media=_entre_safras(tmax_m, statistics.mean),
                tmax_extremo_medio=_entre_safras(tmax_x, statistics.mean),
                tmax_absoluto=round(max(tmax_abs), 2) if ok and tmax_abs else None,
                gwettop_media=_entre_safras(gwettop, statistics.mean),
                gwetroot_media=_entre_safras(gwetroot, statistics.mean),
            )
        )
    return resumo
