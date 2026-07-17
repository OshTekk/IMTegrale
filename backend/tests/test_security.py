import pytest
from app.config import get_settings
from app.routers import auth as auth_router
from app.security import (
    CredentialCipher,
    LoginRateLimiter,
    client_identity,
    generate_share_token,
    share_token_prefix,
)
from starlette.requests import Request


def test_credentials_are_context_bound() -> None:
    cipher = CredentialCipher(get_settings().credential_key)
    envelope = cipher.encrypt("sensitive", context="imt-password:account-a")
    assert "sensitive" not in envelope
    assert cipher.decrypt(envelope, context="imt-password:account-a") == "sensitive"
    with pytest.raises(RuntimeError):
        cipher.decrypt(envelope, context="imt-password:account-b")


def test_share_token_has_lookup_prefix_without_weakening_secret() -> None:
    prefix, token = generate_share_token()
    assert token.startswith(f"bn1_{prefix}_")
    assert share_token_prefix(token) == prefix
    assert len(token) > 50


def test_client_identity_only_trusts_the_configured_proxy_header() -> None:
    settings = get_settings().model_copy(update={"trusted_proxy_ips": ["192.0.2.5"]})
    trusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-botnote-client-identity", b"tailnet:student@example.test")],
            "client": ("192.0.2.5", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )
    untrusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-botnote-client-identity", b"tailnet:spoofed@example.test")],
            "client": ("192.0.2.44", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )

    assert client_identity(trusted, settings) == "tailnet:student@example.test"
    assert client_identity(untrusted, settings) == "peer:192.0.2.44"


def test_login_rate_limiter_preflight_does_not_consume_and_key_count_is_bounded() -> None:
    limiter = LoginRateLimiter(limit=2, window_seconds=900, max_keys=3)
    limiter.check("preflight", consume=False)
    assert limiter.tracked_keys == 0

    for key in ("one", "two", "three", "four"):
        limiter.check(key)
    assert limiter.tracked_keys == 3


def test_rejected_narrow_login_limit_does_not_spend_shared_budget(monkeypatch) -> None:
    client_limiter = LoginRateLimiter(limit=2, window_seconds=900)
    target_limiter = LoginRateLimiter(limit=1, window_seconds=900)
    global_limiter = LoginRateLimiter(limit=10, window_seconds=900)
    monkeypatch.setattr(auth_router, "login_rate_limiter", client_limiter)
    monkeypatch.setattr(auth_router, "login_target_rate_limiter", target_limiter)
    monkeypatch.setattr(auth_router, "login_global_rate_limiter", global_limiter)

    target = "limited@imt-atlantique.fr"
    target_limiter.check(auth_router._rate_key(f"imt|{target}"))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login/imt",
            "headers": [],
            "client": ("192.0.2.20", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )

    with pytest.raises(Exception) as rejected:
        auth_router._check_login_limits(request, "imt", target, get_settings())
    assert getattr(rejected.value, "status_code", None) == 429
    assert client_limiter.tracked_keys == 0
    assert global_limiter.tracked_keys == 0
