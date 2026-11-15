from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Entressafras API"
    debug: bool = False
    ecocrop_csv_path: Path = APP_DIR / "data" / "EcoCrop_DB.csv"
    cropwat_tsv_path: Path = APP_DIR / "data" / "cropwat_crop_params.tsv"

    # Any origin: the API is public and uses no cookies or auth.
    cors_origins: list[str] = ["*"]

    power_base_url: str = "https://power.larc.nasa.gov"
    power_timeout_s: float = 60.0
    # Séries pré-baixadas e versionadas (demo sem rede); lidas antes do cache local.
    power_seed_dir: Path = APP_DIR / "data" / "power"
    # Cache local de respostas do POWER (fora do git).
    power_cache_dir: Path = PROJECT_DIR / ".cache" / "power"

    # ISRIC SoilGrids 2.0 (textura do solo). API beta com uso justo de 5 chamadas/min.
    soilgrids_base_url: str = "https://rest.isric.org"
    soilgrids_timeout_s: float = 90.0
    soilgrids_min_interval_s: float = 12.0
    soilgrids_seed_dir: Path = APP_DIR / "data" / "soilgrids"
    soilgrids_cache_dir: Path = PROJECT_DIR / ".cache" / "soilgrids"

    # Google Earth Engine (umidade do solo SMAP L4). Sem projeto nem chave, só lê semente/cache.
    ee_project: str | None = None
    # JSON da conta de serviço registrada no Earth Engine; sem ele, usa `earthengine authenticate`.
    ee_service_account_key: Path | None = None
    # A mesma chave como texto (JSON puro ou base64): para servidores sem arquivo, como o Railway.
    ee_service_account_json: str | None = None
    smap_asset: str = "NASA/SMAP/SPL4SMGP/008"
    smap_seed_dir: Path = APP_DIR / "data" / "smap"
    smap_cache_dir: Path = PROJECT_DIR / ".cache" / "smap"

    @field_validator(
        "ee_project", "ee_service_account_key", "ee_service_account_json", mode="before"
    )
    @classmethod
    def _vazio_e_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def ee_configurado(self) -> bool:
        return bool(self.ee_project or self.ee_service_account_key or self.ee_service_account_json)


@lru_cache
def get_settings() -> Settings:
    return Settings()
