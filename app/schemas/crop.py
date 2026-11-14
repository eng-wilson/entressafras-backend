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


class CropPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CropSummary]
