from datetime import date, timedelta

import pytest

from app.services.agro import (
    DiaClima,
    agregar_decendios,
    decendio_idx,
    et0_dia,
    et0_hargreaves,
    et0_penman_monteith,
    p20,
    rotulo_decendio,
    safra_de,
)

# FAO-56, exemplo 18: Bruxelas (50°48'N, 100 m), 6 de julho.
BRUXELAS = dict(tmax=21.5, tmin=12.3, elev=100.0, lat=50.8, doy=187)


def test_et0_penman_monteith_fao56_exemplo_18() -> None:
    et0 = et0_penman_monteith(ea=1.409, u2=2.078, rs=22.07, **BRUXELAS)
    assert et0 == pytest.approx(3.9, abs=0.1)


def test_et0_hargreaves_ordem_de_grandeza() -> None:
    et0 = et0_hargreaves(tmax=21.5, tmin=12.3, lat=50.8, doy=187)
    assert 3.0 < et0 < 5.5


def test_et0_dia_reservas() -> None:
    base = dict(data=date(2020, 7, 6), tmax=21.5, tmin=12.3, elev=100.0, lat=50.8)
    _, metodo = et0_dia(**base, tdew=11.9, ur=70.0, u2=2.0, rs=22.0)
    assert metodo == "pm-orvalho"
    _, metodo = et0_dia(**base, tdew=None, ur=70.0, u2=2.0, rs=22.0)
    assert metodo == "pm-ur"
    _, metodo = et0_dia(**base, tdew=11.9, ur=70.0, u2=2.0, rs=None)
    assert metodo == "hargreaves"
    assert et0_dia(**{**base, "tmax": None}, tdew=None, ur=None, u2=None, rs=None) == (None, None)


@pytest.mark.parametrize(
    ("d", "idx"),
    [
        (date(2020, 9, 1), 0),
        (date(2020, 9, 10), 0),
        (date(2020, 9, 11), 1),
        (date(2020, 9, 21), 2),
        (date(2021, 2, 28), 17),
        (date(2024, 2, 29), 17),
        (date(2021, 8, 31), 35),
    ],
)
def test_decendio_idx(d: date, idx: int) -> None:
    assert decendio_idx(d) == idx


def test_safra_e_rotulo() -> None:
    assert safra_de(date(2020, 9, 1)) == 2020
    assert safra_de(date(2021, 8, 31)) == 2020
    assert rotulo_decendio(0) == "1-10 set"
    assert rotulo_decendio(35) == "21-fim ago"


def test_p20() -> None:
    # Quantil inclusivo de 1..10: posição 0,2 × 9 = 1,8 → 2 + 0,8 = 2,8
    assert p20([float(v) for v in range(1, 11)]) == pytest.approx(2.8)


def _serie(safras: int, chuva=lambda d, s: 1.0, **sobrepor) -> list[DiaClima]:
    dias = []
    d = date(2010, 9, 1)
    while d < date(2010 + safras, 9, 1):
        s = safra_de(d) - 2010
        valores = dict(
            tmax=30.0 + s, tmin=20.0 - s, tdew=18.0, ur=70.0, vento=2.0, rad=20.0,
            chuva=chuva(d, s), gwettop=0.5, gwetroot=0.6, et0=4.0, et0_metodo="pm-orvalho",
        )
        valores.update({k: f(d, s) for k, f in sobrepor.items()})
        dias.append(DiaClima(data=d, **valores))
        d += timedelta(days=1)
    return dias


def test_agregar_decendios_basico() -> None:
    res = agregar_decendios(_serie(10))
    assert len(res) == 36
    assert sum(r.dias for r in res) in (365, 366)
    assert res[0].dias == 10 and res[2].dias == 10  # setembro tem 30 dias
    assert res[5].dias == 11  # 21–31/out
    assert res[17].dias in (8, 9)  # 21–fim/fev
    r = res[0]
    assert r.anos_validos == 10
    assert r.chuva_media == 10.0 and r.chuva_p20 == 10.0
    assert r.et0_media == 40.0
    assert r.tmin_absoluto == 11.0  # 20 - 9 na última safra
    assert r.tmax_absoluto == 39.0
    assert r.tmin_extremo_medio == pytest.approx(15.5)  # média de 20..11
    assert r.gwetroot_media == 0.6


def test_lacuna_preenchida_pela_media_dos_dias_validos() -> None:
    # Na 1ª safra, 1º/set sem chuva; os outros 9 dias têm 2 mm → total preenchido = 20 mm.
    res = agregar_decendios(
        _serie(10, chuva=lambda d, s: None if (s, d.month, d.day) == (0, 9, 1) else 2.0)
    )
    assert res[0].dias_preenchidos == 1
    assert res[0].chuva_media == 20.0
    assert res[1].dias_preenchidos == 0


def test_menos_da_metade_dos_dias_descarta_a_safra() -> None:
    def chuva(d, s):
        return None if s < 6 and d.month == 9 and d.day <= 6 else 1.0

    res = agregar_decendios(_serie(10, chuva=chuva))
    assert res[0].anos_validos == 4
    assert res[0].chuva_media is None  # < 5 safras válidas = "Sem dado"
    assert res[0].chuva_p20 is None
    assert res[1].anos_validos == 10
