import base64
from datetime import timedelta

import pytest
from app.config import Settings, get_settings
from app.database import SessionLocal, utcnow
from app.main import app
from app.models import Account, ShareToken, WebSession
from app.routers import auth as auth_router
from app.security import (
    CredentialCipher,
    LoginRateLimiter,
    client_identity,
    cookie_names,
    generate_share_token,
    get_auth_context,
    secure_compare,
    share_token_prefix,
    token_digest,
    token_digests,
)
from starlette.requests import Request


def test_development_skips_only_production_mtls_files(tmp_path) -> None:
    missing = tmp_path / "missing.pem"
    common = {
        "credential_key": "development-key",
        "token_pepper": "development-pepper",
        "backend_tls_cert": missing,
        "backend_tls_key": missing,
        "backend_tls_ca": missing,
    }

    Settings(environment="development", **common).validate_secrets()
    with pytest.raises(RuntimeError, match="Required backend mTLS file is missing"):
        Settings(environment="production", **common).validate_secrets()


def test_credentials_are_context_bound() -> None:
    cipher = CredentialCipher(get_settings().credential_key)
    envelope = cipher.encrypt("sensitive", context="imt-password:account-a")
    assert "sensitive" not in envelope
    assert cipher.decrypt(envelope, context="imt-password:account-a") == "sensitive"
    with pytest.raises(RuntimeError):
        cipher.decrypt(envelope, context="imt-password:account-b")


def test_credential_keyring_reads_old_keys_and_writes_only_with_active_key() -> None:
    old_key = base64.urlsafe_b64encode(b"o" * 32).decode()
    new_key = base64.urlsafe_b64encode(b"n" * 32).decode()
    old_cipher = CredentialCipher(old_key)
    rotated_cipher = CredentialCipher(new_key, [old_key])
    old_envelope = old_cipher.encrypt("synthetic-secret", context="test:account")

    assert rotated_cipher.decrypt(old_envelope, context="test:account") == "synthetic-secret"
    assert rotated_cipher.needs_reencryption(old_envelope)

    new_envelope = rotated_cipher.encrypt("synthetic-secret", context="test:account")
    assert new_envelope.startswith(f"v1.{rotated_cipher.active_key_id}.")
    assert not rotated_cipher.needs_reencryption(new_envelope)
    with pytest.raises(RuntimeError):
        old_cipher.decrypt(new_envelope, context="test:account")


def test_token_peppers_are_ordered_active_then_previous() -> None:
    settings = get_settings().model_copy(
        update={
            "token_pepper": "active-synthetic-pepper",
            "token_previous_peppers": ["previous-synthetic-pepper"],
        }
    )

    digests = token_digests("synthetic-token", settings)

    assert len(digests) == 2
    assert digests[0] != digests[1]


def test_web_session_digest_is_migrated_when_previous_pepper_matches() -> None:
    base_settings = get_settings()
    old_settings = base_settings.model_copy(update={"token_pepper": "o" * 32})
    rotated_settings = base_settings.model_copy(
        update={"token_pepper": "n" * 32, "token_previous_peppers": ["o" * 32]}
    )
    raw_session = "synthetic-session-token"
    raw_csrf = "synthetic-csrf-token"
    with SessionLocal() as db:
        account = Account(imt_username="pepper.test", display_name="Pepper Test")
        db.add(account)
        db.flush()
        row = WebSession(
            account_id=account.id,
            digest=token_digest(raw_session, old_settings),
            csrf_digest=token_digest(raw_csrf, old_settings),
            role="owner",
            auth_method="imt",
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        session_cookie, csrf_cookie = cookie_names(rotated_settings)
        cookie_header = f"{session_cookie}={raw_session}; {csrf_cookie}={raw_csrf}".encode()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/auth/session",
                "headers": [(b"cookie", cookie_header)],
                "client": ("192.0.2.20", 1234),
                "server": ("testserver", 443),
                "scheme": "https",
            }
        )

        auth = get_auth_context(request, db, rotated_settings)

        assert auth.session.id == row.id
        assert row.digest == token_digest(raw_session, rotated_settings)
        assert row.csrf_digest == token_digest(raw_csrf, rotated_settings)


def test_share_token_is_lazily_migrated_from_previous_pepper(client) -> None:  # noqa: ANN001
    base_settings = get_settings()
    old_settings = base_settings.model_copy(update={"token_pepper": "o" * 32})
    rotated_settings = base_settings.model_copy(
        update={"token_pepper": "n" * 32, "token_previous_peppers": ["o" * 32]}
    )
    prefix, raw_token = generate_share_token()
    with SessionLocal() as db:
        account = Account(imt_username="shared.pepper", display_name="Shared Pepper")
        db.add(account)
        db.flush()
        share = ShareToken(
            account_id=account.id,
            name="Token synthétique",
            prefix=prefix,
            digest=token_digest(raw_token, old_settings),
            role="viewer",
        )
        db.add(share)
        db.commit()
        share_id = share.id

    app.dependency_overrides[get_settings] = lambda: rotated_settings
    try:
        response = client.post("/api/v1/auth/login/token", json={"token": raw_token})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    with SessionLocal() as db:
        share = db.get(ShareToken, share_id)
        assert share is not None
        assert share.digest == token_digest(raw_token, rotated_settings)


def test_production_configuration_rejects_unsafe_invariants(tmp_path) -> None:
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    ca = tmp_path / "ca.crt"
    for path in (certificate, private_key, ca):
        path.write_text("synthetic", encoding="ascii")
    private_key.chmod(0o640)
    valid = Settings(
        environment="production",
        database_url="postgresql+psycopg:///synthetic",
        credential_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        credential_previous_keys=[base64.urlsafe_b64encode(b"o" * 32).decode()],
        token_pepper="p" * 32,
        token_previous_peppers=["q" * 32],
        public_origin="https://imtegrale.example.test",
        allowed_hosts=["imtegrale.example.test"],
        trusted_proxy_ips=["192.0.2.5"],
        admin_allowed_identities=["tailnet:admin@example.test"],
        backend_tls_cert=certificate,
        backend_tls_key=private_key,
        backend_tls_ca=ca,
    )
    valid.validate_secrets()

    unsafe = (
        valid.model_copy(update={"secure_cookies": False}),
        valid.model_copy(update={"database_url": "sqlite:///production.db"}),
        valid.model_copy(update={"allowed_hosts": ["*"]}),
        valid.model_copy(update={"trusted_proxy_ips": ["0.0.0.0"]}),
        valid.model_copy(update={"admin_allowed_identities": ["internet:192.0.2.20"]}),
        valid.model_copy(update={"token_previous_peppers": ["p" * 32]}),
    )
    for settings in unsafe:
        with pytest.raises(RuntimeError):
            settings.validate_secrets()


def test_share_token_has_lookup_prefix_without_weakening_secret() -> None:
    prefix, token = generate_share_token()
    assert token.startswith(f"bn1_{prefix}_")
    assert share_token_prefix(token) == prefix
    assert len(token) > 50


@pytest.mark.parametrize(
    ("left", "right"),
    [("é", "ascii"), ("ascii", "é"), ("🔐", "🔐")],
)
def test_secure_compare_rejects_non_ascii_without_raising(left: str, right: str) -> None:
    assert secure_compare(left, right) is False


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
    client_limiter = LoginRateLimiter(limit=1, window_seconds=900)
    global_limiter = LoginRateLimiter(limit=10, window_seconds=900)
    monkeypatch.setattr(auth_router, "login_rate_limiter", client_limiter)
    monkeypatch.setattr(auth_router, "login_global_rate_limiter", global_limiter)

    target = "limited@imt-atlantique.fr"
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
    client_limiter.check(auth_router._rate_key("peer:192.0.2.20|imt"))

    with pytest.raises(Exception) as rejected:
        auth_router._check_login_limits(request, "imt", target, get_settings())
    assert getattr(rejected.value, "status_code", None) == 429
    assert client_limiter.tracked_keys == 1
    assert global_limiter.tracked_keys == 0


def test_login_limit_does_not_create_attacker_controlled_target_lockout(monkeypatch) -> None:
    client_limiter = LoginRateLimiter(limit=1, window_seconds=900)
    global_limiter = LoginRateLimiter(limit=10, window_seconds=900)
    monkeypatch.setattr(auth_router, "login_rate_limiter", client_limiter)
    monkeypatch.setattr(auth_router, "login_global_rate_limiter", global_limiter)

    def request(peer: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/login/imt",
                "headers": [],
                "client": (peer, 1234),
                "server": ("testserver", 443),
                "scheme": "https",
            }
        )

    target = "victim@imt-atlantique.fr"
    auth_router._check_login_limits(
        request("192.0.2.20"),
        "imt",
        target,
        get_settings(),
    )
    auth_router._check_login_limits(
        request("192.0.2.21"),
        "imt",
        target,
        get_settings(),
    )

    assert client_limiter.tracked_keys == 2
