"""Application configuration via pydantic-settings.

Settings are split into cohesive groups, each reading its own prefixed
environment variables (and, for local development, a ``.env`` file). Inside
Docker the values are injected as real environment variables through
``env_file`` in ``docker-compose.yml``.
"""

from __future__ import annotations

import enum
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


class LLMProviderName(enum.StrEnum):
    ollama = "ollama"
    openai = "openai"
    claude = "claude"


def _config(prefix: str) -> SettingsConfigDict:
    return SettingsConfigDict(
        env_prefix=prefix,
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSettings(BaseSettings):
    model_config = _config("")

    app_env: str = "production"
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"
    timezone: str = "UTC"
    digest_language: str = "ru"


class TelegramSettings(BaseSettings):
    model_config = _config("TG_")

    api_id: int = 0
    api_hash: str = ""
    session_path: str = "/data/session/tgdigest"
    string_session: str | None = None
    phone: str | None = None
    two_fa_password: str | None = Field(default=None, validation_alias="TG_2FA_PASSWORD")


class DatabaseSettings(BaseSettings):
    model_config = _config("")

    database_url: str | None = None
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "tgdigest"
    postgres_password: str = "tgdigest"
    postgres_db: str = "tgdigest"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Async SQLAlchemy DSN (asyncpg driver)."""
        if self.database_url:
            return _force_asyncpg(self.database_url)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def _force_asyncpg(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


class LLMSettings(BaseSettings):
    model_config = _config("LLM_")

    provider: LLMProviderName = LLMProviderName.ollama
    model: str = "llama3.1:8b"
    base_url: str = "http://ollama:11434"
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1500
    context_window: int | None = None  # override model registry when set
    request_timeout: float = 120.0
    max_retries: int = 4
    stage1_model: str | None = None
    stage2_model: str | None = None

    def model_for(self, stage: int) -> str:
        if stage == 1 and self.stage1_model:
            return self.stage1_model
        if stage == 2 and self.stage2_model:
            return self.stage2_model
        return self.model


_DEFAULT_TRIVIAL = (
    "ок,окей,ok,k,ок.,+,++,+1,-,да,неа,нет,не,ага,угу,угу.,ясно,понятно,спс,спасибо,"
    "пасиб,пжл,плюс,лол,кек,хаха,ахах,хех,ору,👍,👌,🔥,❤,❤️,😂,🙏,✅,.,!,?,...,))"
)


class PreprocessSettings(BaseSettings):
    model_config = _config("PRE_")

    merge_gap_seconds: int = 90
    min_meaningful_len: int = 8
    trivial_tokens: str = _DEFAULT_TRIVIAL
    thread_gap_minutes: int = 30
    dedup_window: int = 50

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trivial_token_set(self) -> frozenset[str]:
        return frozenset(t.strip().lower() for t in self.trivial_tokens.split(",") if t.strip())


class DefaultsSettings(BaseSettings):
    model_config = _config("DEFAULT_")

    interval_minutes: int = 180
    min_msgs: int = 10
    max_msgs: int = 300
    importance_threshold: float = 0.5
    send_empty: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    app: AppSettings = Field(default_factory=AppSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    preprocess: PreprocessSettings = Field(default_factory=PreprocessSettings)
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
