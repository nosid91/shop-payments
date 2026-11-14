from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_file=".env", extra="ignore")

    url: str


class BreakerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BREAKER_", env_file=".env", extra="ignore")

    # Defaults match pybreaker's own.
    fail_max: int = 5
    reset_timeout: float = 60


class Settings(BaseModel):
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    breaker: BreakerSettings = Field(default_factory=BreakerSettings)
