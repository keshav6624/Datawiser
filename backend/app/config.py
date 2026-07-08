"""DataWhisper configuration."""
from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_DIR = DATA_DIR / "db"
DB_PATH = DB_DIR / "datawhisper.db"

for _p in (DATA_DIR, UPLOAD_DIR, DB_DIR):
    _p.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAWHISPER_", env_file=".env", extra="ignore")

    app_name: str = "DataWhisper"
    version: str = "1.0"
    # LLM provider: "openai" | "anthropic" | "gemini" | "mock"
    llm_provider: str = os.environ.get("DATAWHISPER_LLM_PROVIDER", "mock")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.environ.get("DATAWHISPER_OPENAI_MODEL", "gpt-4o-mini")
    anthropic_model: str = os.environ.get("DATAWHISPER_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    max_sql_retries: int = 3
    max_sql_rows: int = 1000
    query_timeout_seconds: int = 30
    cors_origins: list[str] = ["*"]


settings = Settings()
