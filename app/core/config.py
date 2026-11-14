from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Entressafras API"
    debug: bool = False
    ecocrop_csv_path: Path = APP_DIR / "data" / "EcoCrop_DB.csv"

    cors_origins: list[str] = ["http://localhost:5173"]

    power_base_url: str = "https://power.larc.nasa.gov"
    power_timeout_s: float = 60.0
    # Séries pré-baixadas e versionadas (demo sem rede); lidas antes do cache local.
    power_seed_dir: Path = APP_DIR / "data" / "power"
    # Cache local de respostas do POWER (fora do git).
    power_cache_dir: Path = PROJECT_DIR / ".cache" / "power"


@lru_cache
def get_settings() -> Settings:
    return Settings()
