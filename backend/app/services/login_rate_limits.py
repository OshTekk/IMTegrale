from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field

from fastapi import Request

from app.config import Settings
from app.security import (
    LoginRateLimiter,
    client_identity,
    login_global_rate_limiter,
    login_rate_limiter,
)

_LOGIN_LIMITS_LOCK = threading.Lock()
_PASSWORD_KINDS = frozenset({"imt", "pass-reconnect", "sync-credential-enroll"})


def _rate_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LoginRateReservation:
    client_keys: tuple[str, ...]
    client_limiter: LoginRateLimiter = field(repr=False, compare=False)

    @property
    def primary_key(self) -> str:
        return self.client_keys[0]

    def reset_after_success(self) -> None:
        for key in self.client_keys:
            self.client_limiter.reset(key)


def check_login_limits(
    request: Request,
    *,
    kind: str,
    settings: Settings,
    shared_password_bucket: bool = True,
    client_limiter: LoginRateLimiter = login_rate_limiter,
    global_limiter: LoginRateLimiter = login_global_rate_limiter,
) -> LoginRateReservation:
    identity = client_identity(request, settings)
    client_keys = [_rate_key(f"{identity}|{kind}")]
    if shared_password_bucket and kind in _PASSWORD_KINDS:
        client_keys.append(_rate_key(f"{identity}|imt-password"))
    checks = [(client_limiter, key) for key in client_keys]
    checks.append((global_limiter, "all-logins"))
    with _LOGIN_LIMITS_LOCK:
        for limiter, key in checks:
            limiter.check(key, consume=False)
        for limiter, key in checks:
            limiter.check(key)
    return LoginRateReservation(tuple(client_keys), client_limiter)
