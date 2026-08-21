from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str = Field(min_length=32)
    # Leave empty to allow open registration on your own server.
    registration_invite_code: str = ""
    access_token_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    device_clock_skew_seconds: int = Field(default=300, ge=30, le=900)
    max_content_bytes: int = Field(default=1_048_576, ge=1024, le=5_242_880)
    max_request_bytes: int = Field(default=1_258_291, ge=2048, le=6_291_456)
    auth_rate_limit_attempts: int = Field(default=10, ge=1, le=100)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=10, le=3600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
