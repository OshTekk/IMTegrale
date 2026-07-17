from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOTNOTE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["production", "development", "test"] = "production"
    database_url: str = "postgresql+psycopg:///botnote"
    credential_key: str = ""
    token_pepper: str = ""
    public_origin: str = "https://localhost"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost"])
    frontend_dist: Path = Path("frontend/dist")
    secure_cookies: bool = True
    allow_imt_signup: bool = True
    session_ttl_days: int = 30
    session_touch_minutes: int = 15
    max_request_bytes: int = 1_048_576
    imt_timeout_seconds: int = 30
    pass_operation_lease_seconds: int = Field(default=180, ge=60, le=600)
    pass_quiet_period_seconds: int = Field(default=60, ge=0, le=600)
    pass_session_max_hours: int = Field(default=24, ge=1, le=24)
    pass_profile_refresh_days: int = Field(default=30, ge=1, le=90)
    pass_hourly_quota: int = Field(default=3, ge=1, le=12)
    pass_daily_quota: int = Field(default=8, ge=1, le=48)
    scheduler_poll_seconds: int = Field(default=60, ge=15, le=900)
    sync_lock_dir: Path = Path("/run/botnote")
    trusted_proxy_ips: list[str] = Field(default_factory=lambda: ["127.0.0.1"])
    admin_allowed_identities: list[str] = Field(default_factory=list)
    admin_session_ttl_hours: int = Field(default=8, ge=1, le=24)
    backend_tls_cert: Path = Path("/etc/botnote/mtls/server.crt")
    backend_tls_key: Path = Path("/etc/botnote/mtls/server.key")
    backend_tls_ca: Path = Path("/etc/botnote/mtls/ca.crt")
    database_pool_size: int = Field(default=10, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)

    @field_validator("allowed_hosts", "trusted_proxy_ips", "admin_allowed_identities", mode="before")
    @classmethod
    def parse_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("public_origin")
    @classmethod
    def clean_origin(cls, value: str) -> str:
        return value.rstrip("/")

    def validate_secrets(self) -> None:
        if self.environment == "test":
            return
        if not self.credential_key:
            raise RuntimeError("BOTNOTE_CREDENTIAL_KEY is required")
        if not self.token_pepper:
            raise RuntimeError("BOTNOTE_TOKEN_PEPPER is required")
        if self.environment == "production":
            for path in (self.backend_tls_cert, self.backend_tls_key, self.backend_tls_ca):
                if not path.is_file():
                    raise RuntimeError(f"Required backend mTLS file is missing: {path}")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_secrets()
    return settings
