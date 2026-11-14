from datetime import date

from pydantic import BaseModel, Field

AVISO_RESOLUCAO = (
    "Dados do NASA POWER numa grade de ~50 km (0,5° × 0,625°): o resultado vale para a região, "
    "não para o talhão. Talhões vizinhos caem na mesma célula."
)
FONTE = "NASA POWER (comunidade AG), série diária; ET0 FAO-56 Penman-Monteith"
FONTE_SMAP = (
    "NASA SMAP L4 (SPL4SMGP v8) via Google Earth Engine, pixel de ~11 km: modelo GEOS Catchment "
    "com assimilação das observações do satélite SMAP; médias diárias no fuso de MT."
)


class Ponto(BaseModel):
    lat: float
    lon: float


class Celula(Ponto):
    elev: float = Field(description="Elevação da célula, m")


class Decendio(BaseModel):
    """Climatologia de um decêndio nas safras da janela. `null` = "Sem dado"."""

    idx: int = Field(description="0 = 1–10/set … 35 = 21–31/ago")
    rotulo: str
    dias: int = Field(description="Dias do decêndio (o 3º do mês tem de 8 a 11)")
    anos_validos: int = Field(description="Safras com chuva válida no decêndio")
    dias_preenchidos: int = Field(description="Dias de chuva ausentes preenchidos pela média")
    chuva_media: float | None = Field(description="Chuva total média, mm")
    chuva_p20: float | None = Field(description="Chuva superada em 8 de cada 10 anos, mm")
    et0_media: float | None = Field(description="ET0 total média, mm")
    tmin_media: float | None = Field(description="Média das mínimas diárias, °C")
    tmin_extremo_medio: float | None = Field(description="Média do menor Tmin de cada safra, °C")
    tmin_absoluto: float | None = Field(description="Menor Tmin entre todas as safras, °C")
    tmax_media: float | None = Field(description="Média das máximas diárias, °C")
    tmax_extremo_medio: float | None = Field(description="Média do maior Tmax de cada safra, °C")
    tmax_absoluto: float | None = Field(description="Maior Tmax entre todas as safras, °C")
    gwettop_media: float | None = Field(description="Umidade do solo superficial, fração 0–1")
    gwetroot_media: float | None = Field(description="Umidade da zona das raízes, fração 0–1")
    sm_surface_media: float | None = Field(
        description="SMAP: umidade do solo 0–5 cm, m³/m³ (null sem SMAP)"
    )
    sm_rootzone_media: float | None = Field(
        description="SMAP: umidade da zona das raízes 0–100 cm, m³/m³"
    )
    sm_rootzone_wetness_media: float | None = Field(
        description="SMAP: saturação da zona das raízes, fração 0–1 (comparável a gwetroot)"
    )
    sm_rootzone_pctl_media: float | None = Field(
        description="SMAP: percentil climatológico da zona das raízes, 0–100 (50 = normal)"
    )


class ClimaDecendios(BaseModel):
    ponto: Ponto
    celula: Celula
    safras: list[str]
    fonte: str = FONTE
    aviso_resolucao: str = AVISO_RESOLUCAO
    pixel_smap: Ponto | None = Field(
        default=None, description="Pixel SMAP amostrado; null = umidade SMAP indisponível"
    )
    fonte_umidade_solo: str | None = None
    decendios: list[Decendio]


class Dia(BaseModel):
    data: date
    tmax: float | None = Field(description="°C")
    tmin: float | None = Field(description="°C")
    tdew: float | None = Field(description="Ponto de orvalho, °C")
    ur: float | None = Field(description="Umidade relativa, %")
    vento: float | None = Field(description="Vento a 2 m, m/s")
    rad: float | None = Field(description="Radiação solar global, MJ/m²/dia")
    chuva: float | None = Field(description="mm")
    gwettop: float | None = Field(description="Fração 0–1")
    gwetroot: float | None = Field(description="Fração 0–1")
    et0: float | None = Field(description="mm/dia")
    et0_metodo: str | None = Field(description="pm-orvalho, pm-ur ou hargreaves (estimado)")


class ClimaDiario(BaseModel):
    ponto: Ponto
    celula: Celula
    fonte: str = FONTE
    aviso_resolucao: str = AVISO_RESOLUCAO
    dias: list[Dia]
