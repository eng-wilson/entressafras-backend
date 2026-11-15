import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import ee
import pytest

from app.services import smap_client as sc
from app.services.agro import CAMPOS_SMAP
from app.services.power_client import Janela
from app.services.smap_client import (
    SmapClient,
    SmapIndisponivel,
    SmapRespostaInvalida,
    chave_cache,
    diario,
    interpretar,
    pixel,
)

JANELA = Janela(inicio=date(2016, 9, 1), fim=date(2016, 9, 3), safras=["2016/17"])
CABECALHO = ["id", "longitude", "latitude", "time", *CAMPOS_SMAP]


def linha(instante_utc: datetime, valores=(0.2, 0.3, 0.6, 50.0)) -> list:
    ms = int(instante_utc.timestamp() * 1000)
    return ["img", -55.7, -12.5, ms, *valores]


def linhas_3h(inicio_utc: datetime, n: int, valores=(0.2, 0.3, 0.6, 50.0)) -> list[list]:
    return [linha(inicio_utc + timedelta(hours=3 * i), valores) for i in range(n)]


def bruto_diario(dias: int = 3, valor: float = 0.3) -> dict:
    return {
        "pixel": {"lat": -12.5, "lon": -55.7},
        "bandas": list(CAMPOS_SMAP),
        "dias": {
            f"{JANELA.inicio + timedelta(days=i):%Y-%m-%d}": [0.2, valor, 0.6, 50.0]
            for i in range(dias)
        },
    }


def test_pixel_grade_01() -> None:
    assert pixel(-12.55, -55.72) == (-12.6, -55.7)
    assert pixel(-12.54, -55.71) == pixel(-12.53, -55.74)  # talhões vizinhos, mesmo pixel
    assert pixel(0.01, -0.02) == (0.0, 0.0)


def test_diario_agrupa_no_fuso_de_mt_e_descarta_dia_incompleto() -> None:
    # 1º/set/2016 00:00 em Cuiabá (UTC-4) = 04:00 UTC. Amostras das 01:30 locais em diante.
    inicio_local = datetime(2016, 9, 1, 5, 30, tzinfo=UTC)
    linhas = linhas_3h(inicio_local, 16)  # 2 dias completos
    linhas += linhas_3h(inicio_local + timedelta(days=2), 3)  # 3/set incompleto
    linhas.append(linha(datetime(2016, 9, 1, 2, 30, tzinfo=UTC)))  # 31/ago local: fora da janela
    bruto = diario(CABECALHO, linhas, JANELA.inicio, JANELA.fim)
    assert list(bruto["dias"]) == ["2016-09-01", "2016-09-02"]
    assert bruto["dias"]["2016-09-01"] == [0.2, 0.3, 0.6, 50.0]
    assert bruto["pixel"] == {"lat": -12.5, "lon": -55.7}


def test_diario_media_ignora_mascarado() -> None:
    inicio = datetime(2016, 9, 1, 5, 30, tzinfo=UTC)
    linhas = linhas_3h(inicio, 4, (0.1, 0.2, None, 40.0)) + linhas_3h(
        inicio + timedelta(hours=12), 4, (0.3, 0.4, None, 60.0)
    )
    dia = diario(CABECALHO, linhas, JANELA.inicio, JANELA.fim)["dias"]["2016-09-01"]
    assert dia[0] == pytest.approx(0.2) and dia[1] == pytest.approx(0.3)
    assert dia[2] is None and dia[3] == 50.0


def test_diario_coluna_ausente() -> None:
    with pytest.raises(SmapRespostaInvalida, match="coluna"):
        diario(CABECALHO[:-1], [], JANELA.inicio, JANELA.fim)


def test_interpretar_valida_bandas() -> None:
    serie = interpretar(bruto_diario())
    assert serie.inicio == date(2016, 9, 1) and serie.fim == date(2016, 9, 3)
    assert serie.dias[date(2016, 9, 2)]["sm_rootzone"] == 0.3
    with pytest.raises(SmapRespostaInvalida):
        interpretar({**bruto_diario(), "bandas": ["x", "y", "z", "w"]})


def montar(tmp_path: Path, buscar) -> tuple[SmapClient, list]:
    chamadas: list = []

    def registrar(*args):
        chamadas.append(args)
        return buscar(*args)

    client = SmapClient(
        seed_dir=tmp_path / "seed",
        cache_dir=tmp_path / "cache",
        buscar=registrar if buscar else None,
    )
    return client, chamadas


def test_busca_grava_cache_e_reusa(tmp_path: Path) -> None:
    client, chamadas = montar(tmp_path, lambda *a: bruto_diario())
    serie = asyncio.run(client.serie(-12.54, -55.71, JANELA))
    assert chamadas == [(-12.5, -55.7, JANELA.inicio, JANELA.fim)]  # amostra o centro do pixel
    assert len(serie.dias) == 3
    assert (tmp_path / "cache" / chave_cache(-12.5, -55.7, JANELA.inicio, JANELA.fim)).is_file()

    # Processo novo, talhão vizinho: lê do disco, sem Earth Engine.
    client2, chamadas2 = montar(tmp_path, None)
    asyncio.run(client2.serie(-12.53, -55.74, JANELA))
    assert chamadas2 == []


def test_semente_lida_antes_do_earth_engine(tmp_path: Path) -> None:
    seed = tmp_path / "seed" / chave_cache(-12.5, -55.7, JANELA.inicio, JANELA.fim)
    seed.parent.mkdir()
    seed.write_text(json.dumps(bruto_diario()))
    client, chamadas = montar(tmp_path, lambda *a: pytest.fail("não deveria buscar"))
    asyncio.run(client.serie(-12.54, -55.71, JANELA))
    assert chamadas == []


def test_sem_earth_engine_configurado(tmp_path: Path) -> None:
    client, _ = montar(tmp_path, None)
    with pytest.raises(SmapIndisponivel, match="EE_PROJECT"):
        asyncio.run(client.serie(-12.54, -55.71, JANELA))


def test_retry_do_earth_engine(monkeypatch) -> None:
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    tentativas = []

    def falha_uma_vez() -> list:
        tentativas.append(1)
        if len(tentativas) == 1:
            raise ee.EEException("Too many concurrent aggregations")
        return [CABECALHO]

    assert sc._com_retry(falha_uma_vez, (0.0,)) == [CABECALHO]
    assert len(tentativas) == 2

    def sempre_falha() -> list:
        raise ee.EEException("quota")

    with pytest.raises(SmapIndisponivel):
        sc._com_retry(sempre_falha, (0.0,))


class _ColecaoFalsa:
    """Imita filterDate().getRegion().getInfo(); estoura memória em fatias > 15 dias."""

    def __init__(self) -> None:
        self.consultas: list[tuple[int, int]] = []

    def filterDate(self, ini_ms: int, fim_ms: int):  # noqa: N802 (API do Earth Engine)
        colecao = self

        class _Consulta:
            def getRegion(self, ponto, escala):  # noqa: N802
                return self

            def getInfo(self) -> list:  # noqa: N802
                colecao.consultas.append((ini_ms, fim_ms))
                if fim_ms - ini_ms > 15 * 86_400_000:
                    raise ee.EEException("User memory limit exceeded.")
                inicio = datetime.fromtimestamp(ini_ms / 1000, tz=UTC)
                n = (fim_ms - ini_ms) // (3 * 3_600_000)
                return [CABECALHO, *linhas_3h(inicio, n)]

        return _Consulta()


def test_regiao_divide_ao_estourar_memoria() -> None:
    col = _ColecaoFalsa()
    a = datetime(2016, 9, 1, 4, tzinfo=UTC)
    b = a + timedelta(days=30)
    res = sc._regiao(col, None, a, b, (0.0,))
    assert res[0] == CABECALHO and CABECALHO not in res[1:]
    assert len(res) - 1 == 30 * 8  # todas as amostras de 3 h
    assert len(col.consultas) == 3  # 30 dias falha → 2 × 15 dias


def test_retry_nao_repete_erro_de_tamanho() -> None:
    chamadas = []

    def estoura() -> list:
        chamadas.append(1)
        raise ee.EEException("User memory limit exceeded.")

    with pytest.raises(ee.EEException):
        sc._com_retry(estoura, (0.0,))
    assert len(chamadas) == 1


def test_fatias_mensais() -> None:
    t0 = datetime(2016, 9, 1, tzinfo=sc.FUSO)
    t1 = datetime(2017, 1, 15, tzinfo=sc.FUSO)
    fatias = sc.fatias_mensais(t0, t1)
    assert [(a.month, b.month, b.day) for a, b in fatias] == [
        (9, 10, 1),
        (10, 11, 1),
        (11, 12, 1),
        (12, 1, 1),
        (1, 1, 15),
    ]


# --- Autenticação ------------------------------------------------------------------------------

CHAVE = {
    "type": "service_account",
    "project_id": "projeto-da-chave",
    "client_email": "conta@projeto-da-chave.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nSEGREDO\n-----END PRIVATE KEY-----\n",
}


@pytest.fixture
def ee_falso(monkeypatch) -> dict:
    """Troca ServiceAccountCredentials/Initialize por registradores e zera o estado global."""
    chamadas: dict = {}

    def credenciais(email, key_file=None, key_data=None):
        chamadas["credenciais"] = (email, key_file, key_data)
        return "creds"

    def initialize(creds=None, project=None):
        chamadas["initialize"] = (creds, project)

    monkeypatch.setattr(ee, "ServiceAccountCredentials", credenciais)
    monkeypatch.setattr(ee, "Initialize", initialize)
    monkeypatch.setattr(sc, "_ee_pronto", False)
    return chamadas


def test_chave_json_puro(ee_falso) -> None:
    sc.inicializar_ee(None, chave_json=json.dumps(CHAVE))
    email, arquivo, dados = ee_falso["credenciais"]
    assert email == CHAVE["client_email"] and arquivo is None
    assert json.loads(dados) == CHAVE
    assert ee_falso["initialize"] == ("creds", "projeto-da-chave")  # project_id da chave


def test_chave_base64_com_projeto_explicito(ee_falso) -> None:
    import base64

    b64 = base64.b64encode(json.dumps(CHAVE).encode()).decode()
    sc.inicializar_ee("tcc2-e909a", chave_json=f"  {b64}\n")
    assert ee_falso["initialize"] == ("creds", "tcc2-e909a")


def test_chave_json_tem_prioridade_sobre_arquivo(ee_falso, tmp_path: Path) -> None:
    arquivo = tmp_path / "outra.json"
    arquivo.write_text(json.dumps({**CHAVE, "client_email": "arquivo@x.com"}))
    sc.inicializar_ee(None, chave_arquivo=arquivo, chave_json=json.dumps(CHAVE))
    assert ee_falso["credenciais"][0] == CHAVE["client_email"]


@pytest.mark.parametrize(
    "valor",
    ["não é base64 SEGREDO", "eyJzZWdyZWRvIjog", json.dumps({"private_key": "SEGREDO"})],
)
def test_chave_invalida_nao_vaza_valor(ee_falso, valor: str) -> None:
    with pytest.raises(SmapIndisponivel) as exc:
        sc.inicializar_ee(None, chave_json=valor)
    assert "SEGREDO" not in str(exc.value) and valor not in str(exc.value)
    assert "initialize" not in ee_falso
    assert sc._ee_pronto is False


def test_sem_chave_usa_credencial_de_usuario(ee_falso) -> None:
    sc.inicializar_ee("tcc2-e909a")
    assert "credenciais" not in ee_falso
    assert ee_falso["initialize"] == (None, "tcc2-e909a")
    sc.inicializar_ee("outro")  # já inicializado: não repete
    assert ee_falso["initialize"] == (None, "tcc2-e909a")
