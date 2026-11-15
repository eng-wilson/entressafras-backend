import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.services.power_client import (
    PARAMETROS,
    Janela,
    PowerClient,
    PowerIndisponivel,
    RespostaInvalida,
    celula,
    chave_cache,
    janela_safras,
)

JANELA = Janela(inicio=date(2016, 9, 1), fim=date(2016, 9, 5), safras=["2016/17"])


def resposta_power(lat=-12.5, lon=-55.625, unidade_rad="MJ/m^2/day", fill_em=None) -> dict:
    datas = [f"{JANELA.inicio + timedelta(days=i):%Y%m%d}" for i in range(5)]
    valores = {
        "T2M_MAX": 32.0,
        "T2M_MIN": 18.0,
        "T2MDEW": 12.0,
        "RH2M": 50.0,
        "WS2M": 1.5,
        "ALLSKY_SFC_SW_DWN": 20.0,
        "PRECTOTCORR": 0.0,
        "GWETTOP": 0.3,
        "GWETROOT": 0.5,
    }
    param = {p: {d: v for d in datas} for p, v in valores.items()}
    if fill_em:
        param[fill_em[0]][datas[fill_em[1]]] = -999.0
    return {
        "geometry": {"type": "Point", "coordinates": [lon, lat, 370.0]},
        "properties": {"parameter": param},
        "parameters": {
            p: {"units": unidade_rad if p == "ALLSKY_SFC_SW_DWN" else "x"} for p in PARAMETROS
        },
        "header": {"fill_value": -999.0},
    }


def montar(tmp_path: Path, handler) -> tuple[PowerClient, list[httpx.Request]]:
    chamadas: list[httpx.Request] = []

    def registrar(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        return handler(req, len(chamadas))

    http = httpx.AsyncClient(transport=httpx.MockTransport(registrar))
    client = PowerClient(
        http,
        base_url="https://power.test",
        seed_dir=tmp_path / "seed",
        cache_dir=tmp_path / "cache",
        backoff_s=(0.0,),
    )
    return client, chamadas


def test_celula_merra2() -> None:
    assert celula(-12.55, -55.72) == (-12.5, -55.625)
    assert celula(-12.6, -55.8) == (-12.5, -55.625)  # talhão vizinho, mesma célula
    assert celula(0.1, -0.2) == (0.0, 0.0)


def test_janela_safras() -> None:
    j = janela_safras(date(2026, 11, 14))
    assert (j.inicio, j.fim) == (date(2016, 9, 1), date(2026, 8, 31))
    assert j.safras[0] == "2016/17" and j.safras[-1] == "2025/26" and len(j.safras) == 10
    # Antes de 30 dias do fim de agosto, a safra que acabou de fechar ainda não entra.
    assert janela_safras(date(2026, 9, 15)).fim == date(2025, 8, 31)


def test_janela_safras_com_mais_safras() -> None:
    j = janela_safras(date(2026, 11, 14), n_safras=30)
    assert (j.inicio, j.fim) == (date(1996, 9, 1), date(2026, 8, 31))
    assert j.safras[0] == "1996/97" and j.safras[-1] == "2025/26" and len(j.safras) == 30


def test_busca_grava_cache_e_reusa(tmp_path: Path) -> None:
    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(200, json=resposta_power()))
    serie = asyncio.run(client.serie(-12.55, -55.72, JANELA))
    assert len(chamadas) == 1
    q = chamadas[0].url.params
    assert q["community"] == "AG" and q["format"] == "JSON"
    assert (q["latitude"], q["longitude"]) == ("-12.5", "-55.625")
    assert q["parameters"].split(",") == list(PARAMETROS)
    assert serie.elev == 370.0 and len(serie.dias) == 5
    assert serie.dias[0].et0 is not None and serie.dias[0].et0_metodo == "pm-orvalho"
    assert (tmp_path / "cache" / chave_cache(-12.5, -55.625, JANELA.inicio, JANELA.fim)).is_file()

    # Outro cliente (processo novo), outro ponto da mesma célula: lê do disco, sem rede.
    client2, chamadas2 = montar(tmp_path, lambda req, n: httpx.Response(500))
    asyncio.run(client2.serie(-12.6, -55.8, JANELA))
    assert chamadas2 == []


def test_semente_lida_antes_da_rede(tmp_path: Path) -> None:
    seed = tmp_path / "seed" / chave_cache(-12.5, -55.625, JANELA.inicio, JANELA.fim)
    seed.parent.mkdir()
    seed.write_text(json.dumps(resposta_power()))
    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(500))
    asyncio.run(client.serie(-12.55, -55.72, JANELA))
    assert chamadas == []


def test_retry_apos_429(tmp_path: Path) -> None:
    def handler(req, n):
        return httpx.Response(429) if n == 1 else httpx.Response(200, json=resposta_power())

    client, chamadas = montar(tmp_path, handler)
    asyncio.run(client.serie(-12.55, -55.72, JANELA))
    assert len(chamadas) == 2


def test_indisponivel_apos_tentativas(tmp_path: Path) -> None:
    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(503))
    with pytest.raises(PowerIndisponivel):
        asyncio.run(client.serie(-12.55, -55.72, JANELA))
    assert len(chamadas) == 3
    assert not (tmp_path / "cache").exists()


def test_fill_value_vira_none(tmp_path: Path) -> None:
    bruto = resposta_power(fill_em=("PRECTOTCORR", 2))
    client, _ = montar(tmp_path, lambda req, n: httpx.Response(200, json=bruto))
    serie = asyncio.run(client.serie(-12.55, -55.72, JANELA))
    assert serie.dias[2].chuva is None and serie.dias[1].chuva == 0.0


def test_unidade_de_radiacao_inesperada(tmp_path: Path) -> None:
    bruto = resposta_power(unidade_rad="kW-hr/m^2/day")
    client, _ = montar(tmp_path, lambda req, n: httpx.Response(200, json=bruto))
    with pytest.raises(RespostaInvalida, match="radiação"):
        asyncio.run(client.serie(-12.55, -55.72, JANELA))


def _gravar(pasta: Path, lat: float, lon: float, janela: Janela) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / chave_cache(lat, lon, janela.inicio, janela.fim)).write_text(
        json.dumps(resposta_power(lat, lon))
    )


def test_nearest_cached_prefers_the_same_window_then_the_closest_cell(tmp_path: Path) -> None:
    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(503))
    outra = Janela(inicio=date(2015, 9, 1), fim=date(2015, 9, 5), safras=["2015/16"])
    _gravar(tmp_path / "seed", -15.0, -50.0, outra)  # same cell, other window
    _gravar(tmp_path / "cache", -18.0, -51.25, JANELA)
    _gravar(tmp_path / "seed", -12.5, -55.625, JANELA)
    serie = client.nearest_cached(-15.0, -50.0, JANELA)
    assert serie is not None
    assert (serie.lat, serie.lon) == (-18.0, -51.25)
    assert serie.inicio == JANELA.inicio
    assert chamadas == []


def test_nearest_cached_falls_back_to_another_window(tmp_path: Path) -> None:
    client, _ = montar(tmp_path, lambda req, n: httpx.Response(503))
    outra = Janela(inicio=date(2015, 9, 1), fim=date(2015, 9, 5), safras=["2015/16"])
    _gravar(tmp_path / "seed", -12.5, -55.625, outra)
    serie = client.nearest_cached(-15.0, -50.0, JANELA)
    assert serie is not None
    assert (serie.lat, serie.lon) == (-12.5, -55.625)


def test_nearest_cached_without_files_is_none(tmp_path: Path) -> None:
    client, _ = montar(tmp_path, lambda req, n: httpx.Response(503))
    assert client.nearest_cached(-15.0, -50.0, JANELA) is None


def test_after_an_outage_calls_fail_fast_until_the_cooldown(tmp_path: Path) -> None:
    import time

    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(503))
    client._breaker.cooldown_s = 0.2
    with pytest.raises(PowerIndisponivel):
        asyncio.run(client.serie(-12.5, -55.6, JANELA))
    assert len(chamadas) == 3
    with pytest.raises(PowerIndisponivel, match="recently"):
        asyncio.run(client.serie(-15.0, -50.0, JANELA))
    assert len(chamadas) == 3
    time.sleep(0.25)
    with pytest.raises(PowerIndisponivel):
        asyncio.run(client.serie(-15.0, -50.0, JANELA))
    assert len(chamadas) == 6


def test_mark_unavailable_opens_the_breaker(tmp_path: Path) -> None:
    client, chamadas = montar(tmp_path, lambda req, n: httpx.Response(503))
    client.mark_unavailable()
    with pytest.raises(PowerIndisponivel):
        asyncio.run(client.serie(-12.5, -55.6, JANELA))
    assert chamadas == []
