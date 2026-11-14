"""Umidade do solo SMAP L4 (NASA/SMAP/SPL4SMGP) pelo Google Earth Engine, com cache em disco.

O SMAP L4 é um produto de modelo (GEOS Catchment) que assimila as temperaturas de brilho do
satélite SMAP: sem lacunas, a cada 3 h, desde 31/03/2015. No Earth Engine ele está reprojetado para
lat/lon com escala nominal de 11 km. Cada ponto é encaixado numa grade de 0,1° (≈ 11 km), então
talhões vizinhos dividem a mesma consulta e o mesmo arquivo.

As 8 amostras de 3 h são agregadas em médias diárias no fuso de Mato Grosso, o que deixa os dias
alinhados aos do NASA POWER (que usa a hora solar local). Ordem de leitura: semente versionada
(`smap_seed_dir`), cache local (`smap_cache_dir`) e só então o Earth Engine.
"""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.agro import CAMPOS_SMAP
from app.services.power_client import CARENCIA, Janela, janela_safras

log = logging.getLogger(__name__)

GRADE = 0.1
ESCALA_M = 11000  # escala nominal do asset no Earth Engine
FUSO = ZoneInfo("America/Cuiaba")
# 8 amostras por dia; 7 aceito no dia de 23 h do antigo horário de verão de MT (até 2019).
AMOSTRAS_MIN = 7
TENTATIVAS = 3
BACKOFF_S = (2.0, 4.0, 8.0)
CACHE_VERSAO = "v1"

ValoresSmap = dict[str, float | None]
Buscar = Callable[[float, float, date, date], dict]


class SmapIndisponivel(Exception):
    """Earth Engine não configurado ou sem resposta depois das tentativas."""


class SmapRespostaInvalida(Exception):
    """O Earth Engine respondeu algo fora do esperado (bandas, formato)."""


def pixel(lat: float, lon: float) -> tuple[float, float]:
    """Centro da célula de 0,1° que contém o ponto (chave de cache e ponto amostrado)."""
    return round(round(lat / GRADE) * GRADE, 1) + 0.0, round(round(lon / GRADE) * GRADE, 1) + 0.0


def chave_cache(lat_p: float, lon_p: float, inicio: date, fim: date) -> str:
    return f"smap_{CACHE_VERSAO}_{lat_p:.1f}_{lon_p:.1f}_{inicio:%Y%m%d}_{fim:%Y%m%d}.json"


@dataclass(frozen=True)
class SerieSmap:
    lat: float  # pixel do Earth Engine efetivamente amostrado
    lon: float
    inicio: date
    fim: date
    dias: dict[date, ValoresSmap]


def diario(cabecalho: Sequence[str], linhas: Sequence[Sequence], inicio: date, fim: date) -> dict:
    """Agrega as linhas do `getRegion` (3 h) em médias diárias no fuso de MT.

    Dias com menos de AMOSTRAS_MIN amostras (bordas da janela, dados ainda não publicados) são
    descartados. Banda mascarada (None) fica fora da média; sem nenhum valor válido, vira None.
    """
    try:
        i_tempo = cabecalho.index("time")
        i_lat, i_lon = cabecalho.index(
            "latitude"), cabecalho.index("longitude")
        i_bandas = [cabecalho.index(b) for b in CAMPOS_SMAP]
    except ValueError as exc:
        raise SmapRespostaInvalida(
            f"coluna ausente no getRegion: {exc}") from exc

    grupos: dict[date, list[Sequence]] = {}
    for linha in linhas:
        local = datetime.fromtimestamp(
            linha[i_tempo] / 1000, tz=UTC).astimezone(FUSO)
        if inicio <= local.date() <= fim:
            grupos.setdefault(local.date(), []).append(linha)
    if not grupos:
        raise SmapRespostaInvalida(
            "Earth Engine devolveu série vazia para o ponto")

    dias: dict[str, list[float | None]] = {}
    for dia in sorted(grupos):
        amostras = grupos[dia]
        if len(amostras) < AMOSTRAS_MIN:
            continue
        medias: list[float | None] = []
        for i in i_bandas:
            ok = [float(a[i]) for a in amostras if a[i] is not None]
            medias.append(round(sum(ok) / len(ok), 5) if ok else None)
        dias[f"{dia:%Y-%m-%d}"] = medias
    primeira = next(iter(grupos.values()))[0]
    return {
        "pixel": {"lat": primeira[i_lat], "lon": primeira[i_lon]},
        "bandas": list(CAMPOS_SMAP),
        "dias": dias,
    }


def interpretar(bruto: dict) -> SerieSmap:
    """JSON diário (semente/cache) → SerieSmap."""
    try:
        bandas = bruto["bandas"]
        lat, lon = float(bruto["pixel"]["lat"]), float(bruto["pixel"]["lon"])
        dias = {
            date.fromisoformat(d): dict(zip(bandas, v, strict=True))
            for d, v in bruto["dias"].items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SmapRespostaInvalida(
            f"JSON do SMAP sem campo esperado: {exc}") from exc
    if list(bandas) != list(CAMPOS_SMAP):
        raise SmapRespostaInvalida(
            f"bandas {bandas}, esperado {list(CAMPOS_SMAP)}")
    if not dias:
        raise SmapRespostaInvalida("série SMAP vazia")
    return SerieSmap(lat=lat, lon=lon, inicio=min(dias), fim=max(dias), dias=dias)


# --- Earth Engine (síncrono; chamado em thread) -----------------------------------------------

_ee_lock = threading.Lock()
_ee_pronto = False


def inicializar_ee(project: str | None, chave_conta_servico: Path | None) -> None:
    """Inicializa o Earth Engine uma vez por processo."""
    global _ee_pronto
    with _ee_lock:
        if _ee_pronto:
            return
        import ee

        try:
            if chave_conta_servico:
                info = json.loads(chave_conta_servico.read_text(encoding="utf-8"))
                creds = ee.ServiceAccountCredentials(
                    info["client_email"], key_file=str(chave_conta_servico)
                )
                project = project or info.get("project_id")
                ee.Initialize(creds, project=project)
            else:
                ee.Initialize(project=project)
        except Exception as exc:  # credencial ausente/inválida, projeto não registrado
            raise SmapIndisponivel(f"Earth Engine não inicializou: {exc}") from exc
        log.info("Earth Engine inicializado (projeto %s)", project)
        _ee_pronto = True


# Erros de consulta grande demais: repetir não adianta, a fatia precisa ser dividida.
ERROS_DE_TAMANHO = ("memory limit", "too many values", "computation timed out")


def _grande_demais(exc: Exception) -> bool:
    return any(t in str(exc).lower() for t in ERROS_DE_TAMANHO)


def _com_retry(fn: Callable[[], list], backoff_s: Sequence[float]) -> list:
    """Repete erros transitórios; erros de tamanho sobem direto (ver `_regiao`)."""
    import ee

    erro = ""
    for tentativa in range(TENTATIVAS):
        try:
            return fn()
        except (ee.EEException, OSError) as exc:
            if _grande_demais(exc):
                raise
            erro = repr(exc)
        if tentativa < TENTATIVAS - 1:
            espera = backoff_s[min(tentativa, len(backoff_s) - 1)]
            log.warning("Earth Engine falhou (%s), nova tentativa em %.0f s", erro, espera)
            time.sleep(espera)
    raise SmapIndisponivel(f"Earth Engine indisponível após {TENTATIVAS} tentativas: {erro}")


def _regiao(col, ponto, a: datetime, b: datetime, backoff_s: Sequence[float]) -> list:
    """`getRegion` de [a, b); se estourar memória/tempo, divide a fatia ao meio e junta."""
    import ee

    ini_ms, fim_ms = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
    consulta = col.filterDate(ini_ms, fim_ms).getRegion(ponto, ESCALA_M)
    try:
        return _com_retry(consulta.getInfo, backoff_s)
    except ee.EEException as exc:
        if not _grande_demais(exc) or b - a <= timedelta(days=1):
            raise SmapIndisponivel(f"Earth Engine falhou em {a:%Y-%m-%d}: {exc}") from exc
        meio = a + (b - a) / 2
        log.info("fatia %s–%s grande demais (%s), dividindo", a.date(), b.date(), exc)
        esquerda = _regiao(col, ponto, a, meio, backoff_s)
        direita = _regiao(col, ponto, meio, b, backoff_s)
        return esquerda + direita[1:]  # linha 0 = cabeçalho


def fatias_mensais(t0: datetime, t1: datetime) -> list[tuple[datetime, datetime]]:
    """[t0, t1) em fatias que terminam na virada de cada mês (≈ 240 imagens de 3 h cada)."""
    fatias = []
    a = t0
    while a < t1:
        prox = datetime(a.year + a.month // 12, a.month % 12 + 1, 1, tzinfo=a.tzinfo)
        b = min(t1, prox)
        fatias.append((a, b))
        a = b
    return fatias


def buscar_ee(
    asset: str,
    project: str | None,
    chave_conta_servico: Path | None,
    backoff_s: Sequence[float] = BACKOFF_S,
) -> Buscar:
    """Função que baixa do Earth Engine a série diária do ponto entre `inicio` e `fim`."""

    def buscar(lat: float, lon: float, inicio: date, fim: date) -> dict:
        inicializar_ee(project, chave_conta_servico)
        import ee

        col = ee.ImageCollection(asset).select(list(CAMPOS_SMAP))
        ponto = ee.Geometry.Point([lon, lat])  # o Earth Engine usa [lon, lat]
        # Janela em horário local → instantes UTC. Um getRegion por mês: com um ano inteiro
        # (~2.900 imagens) o Earth Engine estoura o limite de memória por consulta.
        t0 = datetime(inicio.year, inicio.month, inicio.day, tzinfo=FUSO)
        t1 = datetime(fim.year, fim.month, fim.day, tzinfo=FUSO) + timedelta(days=1)

        with ThreadPoolExecutor(max_workers=4) as pool:
            partes = list(
                pool.map(lambda f: _regiao(col, ponto, *f, backoff_s), fatias_mensais(t0, t1))
            )
        cabecalho = partes[0][0]
        linhas = [linha for parte in partes for linha in parte[1:]]
        bruto = diario(cabecalho, linhas, inicio, fim)
        bruto["fonte"] = asset
        return bruto

    return buscar


# --- Cliente ----------------------------------------------------------------------------------


class SmapClient:
    def __init__(self, *, seed_dir: Path, cache_dir: Path, buscar: Buscar | None) -> None:
        """`buscar=None`: Earth Engine não configurado, só semente e cache."""
        self._seed_dir = seed_dir
        self._cache_dir = cache_dir
        self._buscar = buscar
        self._locks: dict[str, asyncio.Lock] = {}
        self._memoria: dict[str, SerieSmap] = {}

    async def serie(self, lat: float, lon: float, janela: Janela | None = None) -> SerieSmap:
        """Série diária SMAP do pixel do ponto na janela de safras (padrão: 10 últimas)."""
        janela = janela or janela_safras()
        chave = chave_cache(*pixel(lat, lon), janela.inicio, janela.fim)
        if chave in self._memoria:
            return self._memoria[chave]
        async with self._locks.setdefault(chave, asyncio.Lock()):
            if chave not in self._memoria:
                bruto = self._ler_disco(chave)
                remoto = bruto is None
                if remoto:
                    bruto = await self._remoto(lat, lon, janela)
                serie = interpretar(bruto)  # valida antes de gravar
                if remoto and date.today() - janela.fim >= CARENCIA:
                    self._gravar(self._cache_dir / chave, bruto)
                self._memoria[chave] = serie
        return self._memoria[chave]

    async def bruto(self, lat: float, lon: float, janela: Janela | None = None) -> tuple[str, dict]:
        """JSON diário e o nome do arquivo de cache (usado pelo script de semente)."""
        janela = janela or janela_safras()
        chave = chave_cache(*pixel(lat, lon), janela.inicio, janela.fim)
        bruto = self._ler_disco(chave) or await self._remoto(lat, lon, janela)
        interpretar(bruto)  # valida antes de alguém gravar
        return chave, bruto

    async def _remoto(self, lat: float, lon: float, janela: Janela) -> dict:
        if self._buscar is None:
            raise SmapIndisponivel(
                "Earth Engine não configurado (EE_PROJECT) e sem semente/cache")
        lat_p, lon_p = pixel(lat, lon)
        return await asyncio.to_thread(self._buscar, lat_p, lon_p, janela.inicio, janela.fim)

    def _ler_disco(self, chave: str) -> dict | None:
        for pasta in (self._seed_dir, self._cache_dir):
            arquivo = pasta / chave
            if arquivo.is_file():
                try:
                    return json.loads(arquivo.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    log.warning(
                        "cache do SMAP ilegível, ignorando: %s", arquivo)
        return None

    @staticmethod
    def _gravar(arquivo: Path, bruto: dict) -> None:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(bruto, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(arquivo)
