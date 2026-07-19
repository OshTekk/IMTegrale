from __future__ import annotations

import base64
import ipaddress
import stat
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    credential_previous_keys: list[str] = Field(default_factory=list)
    token_pepper: str = ""
    token_previous_peppers: list[str] = Field(default_factory=list)
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
    pass_session_max_days: int = Field(default=30, ge=1, le=30)
    pass_profile_refresh_days: int = Field(default=30, ge=1, le=90)
    pass_hourly_quota: int = Field(default=3, ge=1, le=12)
    pass_daily_quota: int = Field(default=8, ge=1, le=48)
    scheduler_poll_seconds: int = Field(default=60, ge=15, le=900)
    worker_heartbeat_ttl_seconds: int = Field(default=180, ge=60, le=900)
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
    owner_imt_username: str = ""
    owner_imt_password_file: Path | None = None
    learning_content_root: Path | None = None
    learning_student_status_max_age_days: int = Field(default=30, ge=1, le=90)
    learning_access_mode: Literal["cohort", "personal"] = "cohort"
    learning_audience_id: str = Field(
        default="fip:2028",
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$",
    )
    learning_audience_label: str = Field(default="FIP 2028", min_length=1, max_length=120)
    learning_level_label: str = Field(default="2A", min_length=1, max_length=80)
    learning_allowed_imt_usernames: list[str] = Field(default_factory=list)
    learning_allowed_identities: list[str] = Field(default_factory=list)

    @field_validator(
        "allowed_hosts",
        "credential_previous_keys",
        "token_previous_peppers",
        "trusted_proxy_ips",
        "admin_allowed_identities",
        "learning_allowed_imt_usernames",
        "learning_allowed_identities",
        mode="before",
    )
    @classmethod
    def parse_list_settings(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("public_origin")
    @classmethod
    def clean_origin(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("owner_imt_username")
    @classmethod
    def clean_owner_username(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("learning_content_root", mode="before")
    @classmethod
    def empty_learning_content_root_disables_feature(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("learning_audience_label", "learning_level_label")
    @classmethod
    def normalize_learning_labels(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("Learning labels must be printable non-empty text")
        return normalized

    @field_validator("learning_allowed_imt_usernames")
    @classmethod
    def normalize_learning_usernames(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            username = value.strip().casefold()
            if not 2 <= len(username) <= 160 or any(ord(character) < 33 for character in username):
                raise ValueError("Learning account allowlist contains an invalid IMT login")
            if username not in normalized:
                normalized.append(username)
        return normalized

    @field_validator("learning_allowed_identities")
    @classmethod
    def normalize_learning_identities(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            identity = value.strip().casefold()
            if (
                not 5 <= len(identity) <= 320
                or not identity.startswith(("lan:", "tailnet:"))
                or any(ord(character) < 33 or ord(character) >= 127 for character in identity)
            ):
                raise ValueError(
                    "Learning ingress identities must be exact LAN or Tailnet identities"
                )
            if identity not in normalized:
                normalized.append(identity)
        return normalized

    @model_validator(mode="after")
    def validate_personal_learning_mode(self) -> Settings:
        if self.learning_access_mode == "cohort":
            if self.learning_audience_id != "fip:2028":
                raise ValueError("Cohort learning mode requires the fip:2028 audience")
            return self

        if self.learning_access_mode == "personal":
            if not self.learning_allowed_imt_usernames:
                raise ValueError("Personal learning mode requires an IMT account allowlist")
            if len(self.learning_allowed_imt_usernames) != 1:
                raise ValueError("Personal learning mode requires exactly one IMT account")
            if not self.learning_allowed_identities:
                raise ValueError("Personal learning mode requires a private ingress allowlist")
            audience_suffix = self.learning_audience_id.removeprefix("personal:")
            if (
                audience_suffix == self.learning_audience_id
                or not audience_suffix
                or not audience_suffix[0].isalnum()
            ):
                raise ValueError(
                    "Personal learning mode requires a distinct personal:<id> audience"
                )
        return self

    def validate_secrets(self) -> None:
        self.validate_learning_content_boundary()
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
            self.validate_production_invariants()

    def validate_production_invariants(self) -> None:
        parsed_origin = urlsplit(self.public_origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.query
            or parsed_origin.fragment
            or parsed_origin.path not in {"", "/"}
        ):
            raise RuntimeError("BOTNOTE_PUBLIC_ORIGIN must be one canonical HTTPS origin")
        if not self.secure_cookies:
            raise RuntimeError("BOTNOTE_SECURE_COOKIES must remain enabled in production")
        if not self.database_url.startswith("postgresql+psycopg:"):
            raise RuntimeError("Production requires PostgreSQL through psycopg")

        normalized_hosts = {host.strip().casefold() for host in self.allowed_hosts}
        if not normalized_hosts or "*" in normalized_hosts:
            raise RuntimeError("BOTNOTE_ALLOWED_HOSTS must contain exact hostnames")
        if parsed_origin.hostname.casefold() not in normalized_hosts:
            raise RuntimeError("BOTNOTE_ALLOWED_HOSTS must include BOTNOTE_PUBLIC_ORIGIN's hostname")

        try:
            trusted_proxies = [ipaddress.ip_address(value) for value in self.trusted_proxy_ips]
        except ValueError as exc:
            raise RuntimeError("BOTNOTE_TRUSTED_PROXY_IPS must contain exact IP addresses") from exc
        if not trusted_proxies or any(
            address.is_unspecified or address.is_multicast for address in trusted_proxies
        ):
            raise RuntimeError("BOTNOTE_TRUSTED_PROXY_IPS contains an unsafe address")
        if not self.admin_allowed_identities or any(
            not identity.casefold().startswith(("lan:", "tailnet:"))
            for identity in self.admin_allowed_identities
        ):
            raise RuntimeError("BOTNOTE_ADMIN_ALLOWED_IDENTITIES must be a private exact allowlist")

        keyring = (self.credential_key, *self.credential_previous_keys)
        decoded_keys: list[bytes] = []
        for encoded_key in keyring:
            try:
                decoded = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
            except Exception as exc:
                raise RuntimeError("Credential keys must be URL-safe base64") from exc
            if len(decoded) != 32:
                raise RuntimeError("Credential keys must decode to exactly 32 bytes")
            decoded_keys.append(decoded)
        if len(set(decoded_keys)) != len(decoded_keys):
            raise RuntimeError("Credential keyring entries must be unique")

        peppers = (self.token_pepper, *self.token_previous_peppers)
        if any(len(pepper.encode("utf-8")) < 32 for pepper in peppers):
            raise RuntimeError("Token peppers must contain at least 32 bytes")
        if len(set(peppers)) != len(peppers):
            raise RuntimeError("Token peppers must be unique")
        if self.token_pepper.encode("utf-8") in decoded_keys:
            raise RuntimeError("Credential encryption and token hashing must use different secrets")

        key_mode = stat.S_IMODE(self.backend_tls_key.stat().st_mode)
        if key_mode & 0o037:
            raise RuntimeError("Backend mTLS private key permissions must be 0640 or stricter")

    def validate_learning_content_boundary(self) -> None:
        """Refuse a content root that a public static surface could expose."""

        if self.learning_content_root is None:
            return
        learning_root = self.learning_content_root.resolve(strict=False)
        public_roots = (
            self.frontend_dist.resolve(strict=False),
            Path("frontend/public").resolve(strict=False),
            Path("backend/app/static").resolve(strict=False),
        )
        if any(
            learning_root == public_root
            or learning_root in public_root.parents
            or public_root in learning_root.parents
            for public_root in public_roots
        ):
            raise RuntimeError("BOTNOTE_LEARNING_CONTENT_ROOT overlaps a public static path")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_secrets()
    return settings
