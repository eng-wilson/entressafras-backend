"""Cliente do NASA POWER (série diária por ponto, comunidade AG) com cache em disco.

O cache é obrigatório: a NASA bloqueia aplicações que repetem a mesma consulta. Cada ponto é
encaixado na célula MERRA-2 (0,5° × 0,625°), então talhões vizinhos dividem a mesma consulta e o
mesmo arquivo. Ordem de leitura: semente versionada (`power_seed_dir`), cache local
(`power_cache_dir`) e só então a rede.

When POWER is down, a circuit breaker skips the network for a while and `nearest_cached` gives
the closest cell already on disk (any region) as a fallback.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from app.services.agro import DiaClima, et0_dia, rotulo_safra
from app.services.fallback import DEFAULT_COOLDOWN_S, CircuitBreaker, distance_km

log = logging.getLogger(__name__)

# Padrão da rota /clima: o SMAP só existe a partir de 2015. As rotações pedem mais safras.
N_SAFRAS = 10
GRADE_LAT = 0.5
GRADE_LON = 0.625
FILL_VALUE = -999.0
# Só guarda em cache janelas encerradas há pelo menos este tempo (os últimos dias chegam como -999).
CARENCIA = timedelta(days=30)
TENTATIVAS = 3
BACKOFF_S = (1.0, 2.0, 4.0)
CACHE_VERSAO = "v1"

PARAMETROS = {
    "T2M_MAX": "tmax",
    "T2M_MIN": "tmin",
    "T2MDEW": "tdew",
    "RH2M": "ur",
    "WS2M": "vento",
    "ALLSKY_SFC_SW_DWN": "rad",
    "PRECTOTCORR": "chuva",
    "GWETTOP": "gwettop",
    "GWETROOT": "gwetroot",
}
UNIDADE_RAD = "MJ/m^2/day"


class PowerIndisponivel(Exception):
    """O POWER não respondeu (rede, 429 ou 5xx) depois das tentativas."""


class RespostaInvalida(Exception):
    """O POWER respondeu algo fora do esperado (unidade, formato)."""


def celula(lat: float, lon: float) -> tuple[float, float]:
    """Centro da célula MERRA-2 que contém o ponto."""
    lat_c = round(lat / GRADE_LAT) * GRADE_LAT
    lon_c = round(lon / GRADE_LON) * GRADE_LON
    return round(lat_c, 3) + 0.0, round(lon_c, 3) + 0.0  # + 0.0 evita "-0.0" na chave


@dataclass(frozen=True)
class Janela:
    inicio: date
    fim: date
    safras: list[str]


def janela_safras(hoje: date | None = None, n_safras: int = N_SAFRAS) -> Janela:
    """As `n_safras` últimas safras completas (1º/set a 31/ago) encerradas há ≥ CARENCIA."""
    hoje = hoje or date.today()
    ano_fim = hoje.year if hoje - date(hoje.year, 8, 31) >= CARENCIA else hoje.year - 1
    ano_ini = ano_fim - n_safras
    return Janela(
        inicio=date(ano_ini, 9, 1),
        fim=date(ano_fim, 8, 31),
        safras=[rotulo_safra(a) for a in range(ano_ini, ano_fim)],
    )


@dataclass(frozen=True)
class SerieDiaria:
    lat: float  # célula
    lon: float
    elev: float
    inicio: date
    fim: date
    dias: list[DiaClima]


def chave_cache(lat_c: float, lon_c: float, inicio: date, fim: date) -> str:
    return f"power_{CACHE_VERSAO}_{lat_c:.3f}_{lon_c:.3f}_{inicio:%Y%m%d}_{fim:%Y%m%d}.json"


@dataclass(frozen=True)
class CachedSeries:
    """Cell and window of a cache file name written by `chave_cache`."""

    lat: float
    lon: float
    start: date
    end: date
    path: Path


def parse_cache_name(path: Path) -> CachedSeries | None:
    prefix = f"power_{CACHE_VERSAO}_"
    if not (path.name.startswith(prefix) and path.name.endswith(".json")):
        return None
    try:
        lat, lon, start, end = path.name.removeprefix(prefix).removesuffix(".json").split("_")
        return CachedSeries(
            float(lat),
            float(lon),
            datetime.strptime(start, "%Y%m%d").date(),
            datetime.strptime(end, "%Y%m%d").date(),
            path,
        )
    except ValueError:
        return None


def _valor(v: float | None) -> float | None:
    return None if v is None or v <= FILL_VALUE + 0.5 else float(v)


def interpretar(bruto: dict, lat_c: float, lon_c: float) -> SerieDiaria:
    """Converte o JSON do POWER na série diária, com -999 → None e ET0 calculada."""
    try:
        params = bruto["properties"]["parameter"]
        elev = float(bruto["geometry"]["coordinates"][2])
        unidade = bruto["parameters"]["ALLSKY_SFC_SW_DWN"]["units"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RespostaInvalida(f"JSON do POWER sem campo esperado: {exc}") from exc
    if unidade != UNIDADE_RAD:
        raise RespostaInvalida(f"radiação em '{unidade}', esperado '{UNIDADE_RAD}' (comunidade AG)")
    faltando = set(PARAMETROS) - set(params)
    if faltando:
        raise RespostaInvalida(f"parâmetros ausentes na resposta: {sorted(faltando)}")

    datas = sorted(params["T2M_MAX"])
    dias = []
    for chave in datas:
        v = {nome: _valor(params[p].get(chave)) for p, nome in PARAMETROS.items()}
        d = datetime.strptime(chave, "%Y%m%d").date()
        et0, metodo = et0_dia(
            data=d,
            tmax=v["tmax"],
            tmin=v["tmin"],
            tdew=v["tdew"],
            ur=v["ur"],
            u2=v["vento"],
            rs=v["rad"],
            elev=elev,
            lat=lat_c,
        )
        dias.append(DiaClima(data=d, et0=et0, et0_metodo=metodo, **v))
    if not dias:
        raise RespostaInvalida("POWER devolveu série vazia")
    return SerieDiaria(
        lat=lat_c, lon=lon_c, elev=elev, inicio=dias[0].data, fim=dias[-1].data, dias=dias
    )


class PowerClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        seed_dir: Path,
        cache_dir: Path,
        backoff_s: tuple[float, ...] = BACKOFF_S,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._seed_dir = seed_dir
        self._cache_dir = cache_dir
        self._backoff_s = backoff_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._memoria: dict[str, SerieDiaria] = {}
        self._breaker = CircuitBreaker(cooldown_s)

    def mark_unavailable(self) -> None:
        """Skip the network for the cooldown (e.g. after a caller gave up waiting)."""
        self._breaker.trip()

    def nearest_cached(self, lat: float, lon: float, janela: Janela) -> SerieDiaria | None:
        """Closest cached cell (seed or disk cache, any distance), no network.

        Fallback when POWER is down. Files with the requested window come first; otherwise the
        closest cell, latest window first. Unreadable files are skipped.
        """
        files: dict[str, CachedSeries] = {}
        for pasta in (self._cache_dir, self._seed_dir):  # seed wins on duplicates
            if pasta.is_dir():
                for arquivo in pasta.glob(f"power_{CACHE_VERSAO}_*.json"):
                    if (cached := parse_cache_name(arquivo)) is not None:
                        files[arquivo.name] = cached

        def order(cached: CachedSeries) -> tuple:
            same_window = (cached.start, cached.end) == (janela.inicio, janela.fim)
            distance = distance_km(lat, lon, cached.lat, cached.lon)
            return (not same_window, round(distance, 3), -cached.end.toordinal())

        for cached in sorted(files.values(), key=order):
            chave = cached.path.name
            if chave in self._memoria:
                return self._memoria[chave]
            try:
                bruto = json.loads(cached.path.read_text(encoding="utf-8"))
                serie = interpretar(bruto, cached.lat, cached.lon)
            except (OSError, ValueError, RespostaInvalida):
                log.warning("unreadable POWER cache, ignoring: %s", cached.path)
                continue
            self._memoria[chave] = serie
            return serie
        return None

    async def serie(self, lat: float, lon: float, janela: Janela | None = None) -> SerieDiaria:
        """Série diária da célula do ponto na janela de safras (padrão: 10 últimas)."""
        janela = janela or janela_safras()
        lat_c, lon_c = celula(lat, lon)
        chave = chave_cache(lat_c, lon_c, janela.inicio, janela.fim)
        if chave in self._memoria:
            return self._memoria[chave]
        async with self._locks.setdefault(chave, asyncio.Lock()):
            if chave not in self._memoria:
                bruto = self._ler_disco(chave)
                if bruto is None:
                    bruto = await self._buscar(lat_c, lon_c, janela.inicio, janela.fim)
                    if date.today() - janela.fim >= CARENCIA:
                        self._gravar(self._cache_dir / chave, bruto)
                self._memoria[chave] = interpretar(bruto, lat_c, lon_c)
        return self._memoria[chave]

    async def bruto(self, lat: float, lon: float, janela: Janela | None = None) -> tuple[str, dict]:
        """JSON bruto do POWER e o nome do arquivo de cache (usado pelo script de semente)."""
        janela = janela or janela_safras()
        lat_c, lon_c = celula(lat, lon)
        chave = chave_cache(lat_c, lon_c, janela.inicio, janela.fim)
        bruto = self._ler_disco(chave) or await self._buscar(
            lat_c, lon_c, janela.inicio, janela.fim
        )
        interpretar(bruto, lat_c, lon_c)  # valida antes de alguém gravar
        return chave, bruto

    def _ler_disco(self, chave: str) -> dict | None:
        for pasta in (self._seed_dir, self._cache_dir):
            arquivo = pasta / chave
            if arquivo.is_file():
                try:
                    return json.loads(arquivo.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    log.warning("cache do POWER ilegível, ignorando: %s", arquivo)
        return None

    @staticmethod
    def _gravar(arquivo: Path, bruto: dict) -> None:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(bruto, separators=(",", ":")), encoding="utf-8")
        tmp.replace(arquivo)

    async def _buscar(self, lat: float, lon: float, inicio: date, fim: date) -> dict:
        params = {
            "parameters": ",".join(PARAMETROS),
            "community": "AG",
            "latitude": lat,
            "longitude": lon,
            "start": f"{inicio:%Y%m%d}",
            "end": f"{fim:%Y%m%d}",
            "format": "JSON",
        }
        url = f"{self._base_url}/api/temporal/daily/point"
        if self._breaker.is_open:
            raise PowerIndisponivel("NASA POWER unavailable recently; retry later")
        erro: str = ""
        for tentativa in range(TENTATIVAS):
            try:
                resp = await self._http.get(url, params=params)
            except httpx.TransportError as exc:
                erro = f"rede: {exc!r}"
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code != 429 and resp.status_code < 500:
                    raise RespostaInvalida(f"POWER {resp.status_code}: {resp.text[:300]}")
                erro = f"HTTP {resp.status_code}"
            if tentativa < TENTATIVAS - 1:
                espera = self._backoff_s[min(tentativa, len(self._backoff_s) - 1)]
                log.warning("POWER falhou (%s), nova tentativa em %.0f s", erro, espera)
                await asyncio.sleep(espera)
        self._breaker.trip()
        raise PowerIndisponivel(f"NASA POWER indisponível após {TENTATIVAS} tentativas ({erro})")
