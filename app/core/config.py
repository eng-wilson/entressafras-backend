from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Entressafras API"
    debug: bool = False
    ecocrop_csv_path: Path = Path(__file__).resolve().parent.parent / "data" / "EcoCrop_DB.csv"


@lru_cache
def get_settings() -> Settings:
    return Settings()
