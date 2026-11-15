from pydantic import BaseModel, Field


class NumericRange(BaseModel):
    optimal_min: float | None = None
    optimal_max: float | None = None
    absolute_min: float | None = None
    absolute_max: float | None = None


class TemperatureRequirements(NumericRange):
    """Temperaturas em °C."""

    killing_rest: float | None = Field(None, description="Temperatura letal no repouso (KTMPR), °C")
    killing_early_growth: float | None = Field(
        None, description="Temperatura letal no início do crescimento (KTMP), °C"
    )


class TextRange(BaseModel):
    optimal_min: str | None = None
    optimal_max: str | None = None
    absolute_min: str | None = None
    absolute_max: str | None = None


class TextOptions(BaseModel):
    optimal: list[str] = Field(default_factory=list)
    absolute: list[str] = Field(default_factory=list)


class SoilRequirements(BaseModel):
    depth: TextOptions = Field(description="Profundidade do solo (DEP/DEPR)")
    texture: TextOptions = Field(description="Textura do solo (TEXT/TEXTR)")
    fertility: TextOptions = Field(description="Fertilidade (FER/FERR)")
    al_toxicity: TextOptions = Field(description="Toxicidade de alumínio (TOX/TOXR)")
    salinity: TextOptions = Field(description="Salinidade (SAL/SALR)")
    drainage: TextOptions = Field(description="Drenagem (DRA/DRAR)")


class CropCycle(BaseModel):
    min_days: int | None = Field(None, description="Ciclo mínimo (GMIN), dias")
    max_days: int | None = Field(None, description="Ciclo máximo (GMAX), dias")


class CropCoefficient(BaseModel):
    """Kc da cultura: ETc = Kc × ET0. Na fase de desenvolvimento, interpola-se linearmente."""

    initial: float = Field(description="Kc na fase inicial (Kini)")
    mid_season: float = Field(description="Kc no meio do ciclo (Kmax)")
    late_season: float = Field(description="Kc no fim do ciclo (Kend)")


class StageLengths(BaseModel):
    initial: int = Field(description="Fase inicial (Lini), dias")
    development: int = Field(description="Desenvolvimento (Ldev), dias")
    mid_season: int = Field(description="Meio do ciclo (Lmid), dias")
    late_season: int = Field(description="Fim do ciclo (Lend), dias")
    total: int = Field(description="Soma das fases, dias")


class RootDepth(BaseModel):
    initial: float = Field(description="Profundidade efetiva das raízes no plantio (Zini), m")
    max: float = Field(description="Profundidade efetiva máxima das raízes (Zend), m")


class AllowableDepletion(BaseModel):
    """Fração p da água disponível no solo que pode ser consumida sem estresse hídrico."""

    initial: float = Field(description="p na fase inicial (p_ini)")
    mid_season: float = Field(description="p no meio do ciclo (p_mid)")
    late_season: float = Field(description="p no fim do ciclo (p_end)")


class YieldResponseFactor(BaseModel):
    """Ky: queda relativa de produtividade por unidade de déficit relativo de evapotranspiração."""

    initial: float | None = Field(None, description="Ky na fase inicial (Yini)")
    development: float | None = Field(None, description="Ky no desenvolvimento (Ydev)")
    mid_season: float | None = Field(None, description="Ky no meio do ciclo (Ymid)")
    late_season: float | None = Field(None, description="Ky no fim do ciclo (Yend)")
    season: float | None = Field(None, description="Ky do ciclo inteiro (Ytot)")


class WaterRequirements(BaseModel):
    """Parâmetros de referência FAO-56 (via CropWat/INRAE); não calibrados por região."""

    profile: str = Field(description="Código do perfil no CropWat, ex.: 'FAO-MAIZE'")
    profile_name: str = Field(description="Nome do perfil no CropWat")
    crop_coefficient: CropCoefficient
    stage_length_days: StageLengths
    root_depth_m: RootDepth
    allowable_depletion: AllowableDepletion
    yield_response_factor: YieldResponseFactor
    max_height_m: float | None = Field(None, description="Altura máxima da cultura (h), m")


class CropSummary(BaseModel):
    eco_port_code: int
    scientific_name: str
    common_names: list[str] = Field(description="Primeiros nomes populares (até 5)")
    category: list[str]
    life_span: list[str]


class CropDetail(BaseModel):
    eco_port_code: int
    scientific_name: str
    authority: str | None
    family: str | None
    synonyms: str | None
    common_names: list[str]
    life_form: list[str]
    habit: list[str]
    life_span: list[str]
    physiology: list[str]
    category: list[str]
    plant_attributes: list[str]
    temperature: TemperatureRequirements = Field(description="Temperatura, °C")
    rainfall: NumericRange = Field(description="Precipitação anual, mm")
    soil_ph: NumericRange
    latitude: NumericRange = Field(description="Latitude, graus")
    altitude_max: float | None = Field(description="Altitude máxima (ALTMX), m")
    light_intensity: TextRange
    soil: SoilRequirements
    photoperiod: list[str]
    climate_zones: list[str]
    abiotic_tolerance: list[str]
    abiotic_susceptibility: list[str]
    introduction_risk: str | None
    production_system: list[str]
    crop_cycle: CropCycle
    water_requirements: WaterRequirements | None = Field(
        None, description="Coeficientes de consumo hídrico (FAO-56); null se não houver perfil"
    )


class CropPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CropSummary]
