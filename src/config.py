from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    source_db_url: str
    local_db_url: str
    api_token: str = ""
    metricas_modelo: dict[str, str] = {}
    lease_seconds: int = 900
    max_attempts: int = 3
    heartbeat_interval_s: int = 10
    heartbeat_timeout_s: int = 60
    auto_reasignar: bool = True
    batch_por_modelo: bool = True
    mantener_modelo_sin_trabajo: bool = False
    sync_enabled: bool = False
    sync_interval_s: int = 30
    sync_batch: int = 500
    sync_from_id: int = 0
    prompts_path: Path = SRC_DIR / "prompts.json"
    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()

TORTOISE_CONFIG = {
    "connections": {
        "default": settings.local_db_url,
        "source": settings.source_db_url,
    },
    "apps": {
        "local": {
            "models": ["src.local_models"],
            "default_connection": "default",
            "migrations": "src.migrations",
        },
        "source": {
            "models": ["src.source_models"],
            "default_connection": "source",
        },
    },
}

# Solo para el CLI de migraciones: nunca abre conexión con la base original.
MIGRATIONS_CONFIG = {
    "connections": {"default": settings.local_db_url},
    "apps": {
        "local": {
            "models": ["src.local_models"],
            "default_connection": "default",
            "migrations": "src.migrations",
        }
    },
}
