from __future__ import annotations

import pytest
import requests
from app.services.imt import (
    CAS_LOGIN_URL,
    IMT_ATLANTIQUE_IDP_ENTITY_ID,
    ImtFetchError,
    ImtPassClient,
)
from app.services.telegram import (
    MAX_TELEGRAM_CHUNKS,
    MAX_TELEGRAM_RESPONSE_BYTES,
    TelegramError,
    build_new_notes_message,
    send_telegram,
    split_message,
)


def fake_response(
    status: int = 200,
    *,
    url: str = CAS_LOGIN_URL,
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update(headers or {})
    response._content = body
    response._content_consumed = True
    return response


def test_cas_password_form_cannot_forward_credentials_to_untrusted_action(monkeypatch) -> None:
    client = ImtPassClient()
    response = fake_response(
        body=(
            b'<form action="https://cas.imt-atlantique.fr.evil.example/login">'
            b'<input name="username"><input type="password" name="password"></form>'
        )
    )
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("credentials must not be posted")

    monkeypatch.setattr(client, "_post", unexpected_post)
    with pytest.raises(ImtFetchError):
        client._complete_cas(response, "owner", "secret")
    assert called is False


def test_imt_redirect_chain_revalidates_every_destination(monkeypatch) -> None:
    client = ImtPassClient()
    redirect = fake_response(
        302,
        headers={"Location": "https://pass.imt-atlantique.fr.evil.example/steal"},
    )
    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: redirect)
    with client._operation(), pytest.raises(ImtFetchError):
        client._get(CAS_LOGIN_URL)


def test_imt_response_is_bounded_before_buffering(monkeypatch) -> None:
    client = ImtPassClient()
    oversized = fake_response(headers={"Content-Length": "3000000"})
    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: oversized)
    with client._operation(), pytest.raises(ImtFetchError):
        client._get(CAS_LOGIN_URL)


def test_imt_streaming_read_enforces_global_deadline(monkeypatch) -> None:
    client = ImtPassClient()
    response = fake_response(body=b"")
    response.iter_content = lambda **_kwargs: iter([b"slow"])
    client._deadline = 1.0
    monkeypatch.setattr("app.services.imt.time.monotonic", lambda: 2.0)

    with pytest.raises(ImtFetchError, match="délai global"):
        client._read_limited(response, 100)


def test_competencies_url_is_derived_only_from_validated_student_route() -> None:
    client = ImtPassClient()
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b'<a href="https://evil.example/ue">external</a>',
    )

    assert client._competency_ue_url(dashboard) == (
        "https://hub.imt-atlantique.fr/comp2/etudiant/40419/ue"
    )


def test_hub_sso_uses_current_credentials_when_cas_requests_login(monkeypatch) -> None:
    client = ImtPassClient()
    login = fake_response(
        body=(
            b'<form action="https://cas.imt-atlantique.fr/login">'
            b'<input name="username"><input type="password" name="password"></form>'
        )
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b"student dashboard",
    )
    captured: list[tuple[str, str]] = []

    def complete_cas(
        _response: requests.Response,
        username: str,
        password: str,
    ) -> requests.Response:
        captured.append((username, password))
        return dashboard

    monkeypatch.setattr(client, "_complete_cas", complete_cas)

    assert client._complete_hub_sso(login, ("student", "secret")) is dashboard
    assert captured == [("student", "secret")]


def test_hub_sso_refuses_a_password_form_without_current_credentials() -> None:
    client = ImtPassClient()
    login = fake_response(body=b'<form><input type="password" name="password"></form>')

    with pytest.raises(ImtFetchError, match="session IMT"):
        client._complete_hub_sso(login, None)


def test_cas_selects_the_official_imt_identity_provider_before_login(monkeypatch) -> None:
    client = ImtPassClient()
    wayf = fake_response(
        url="https://idp.imt-atlantique.fr/IMT/WAYF?target=opaque",
        body=(
            b'<form action="/IMT/WAYF?target=opaque">'
            b'<button name="user_idp" value="https://idp.imt-atlantique.fr/idp/shibboleth">'
            b"IMT Atlantique</button></form>"
        ),
    )
    login = fake_response(
        url="https://cas.imt-atlantique.fr/cas/login",
        body=(
            b'<form action="https://cas.imt-atlantique.fr/cas/login">'
            b'<input name="username"><input type="password" name="password"></form>'
        ),
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b"student dashboard",
    )
    posts: list[tuple[str, list[tuple[str, str]]]] = []

    def post(url: str, *, data: list[tuple[str, str]], **_kwargs) -> requests.Response:
        posts.append((url, data))
        return login if len(posts) == 1 else dashboard

    monkeypatch.setattr(client, "_post", post)

    assert client._complete_cas(wayf, "student", "secret") is dashboard
    assert posts[0] == (
        "https://idp.imt-atlantique.fr/IMT/WAYF?target=opaque",
        [("user_idp", IMT_ATLANTIQUE_IDP_ENTITY_ID), ("session", "true")],
    )
    assert ("username", "student") in posts[1][1]
    assert ("password", "secret") in posts[1][1]


def test_hub_wayf_selection_rejects_an_untrusted_action(monkeypatch) -> None:
    client = ImtPassClient()
    wayf = fake_response(
        url="https://idp.imt-atlantique.fr/IMT/WAYF?target=opaque",
        body=(
            b'<form action="https://evil.example/collect">'
            b'<button name="user_idp" value="https://idp.imt-atlantique.fr/idp/shibboleth">'
            b"IMT Atlantique</button></form>"
        ),
    )
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("the WAYF selection must remain on the official IdP")

    monkeypatch.setattr(client, "_post", unexpected_post)

    with pytest.raises(ImtFetchError):
        client._select_imt_identity_provider(wayf)
    assert called is False


def test_cas_accepts_the_official_idp_consent_form(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/SAML2/Redirect/SSO?execution=e1s2",
        body=(
            b'<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2">'
            b'<input type="hidden" name="csrf_token" value="opaque">'
            b'<button name="_eventId_proceed" value="Accept">Continuer</button></form>'
        ),
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b"student dashboard",
    )
    posts: list[tuple[str, list[tuple[str, str]]]] = []

    def post(url: str, *, data: list[tuple[str, str]], **_kwargs) -> requests.Response:
        posts.append((url, data))
        return dashboard

    monkeypatch.setattr(client, "_post", post)

    assert client._complete_cas(consent, "student", "secret") is dashboard
    assert posts == [
        (
            "https://idp.imt-atlantique.fr/idp/profile/SAML2/Redirect/SSO?execution=e1s2",
            [("csrf_token", "opaque"), ("_eventId_proceed", "Accept")],
        )
    ]


def test_telegram_messages_have_stable_chunk_and_note_limits() -> None:
    chunks = split_message("x" * 1000, limit=100, max_chunks=3)
    assert len(chunks) == 3
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert chunks[-1].endswith("[Notification tronquée]")

    notes = [
        {"ue_code": "SIT130", "label": f"Note {index}", "score": 12, "coefficient": 1, "is_resit": False}
        for index in range(70)
    ]
    message = build_new_notes_message(notes, {"SIT130": 12})
    assert "6 autre(s) note(s)" in message
    assert len(split_message(message)) <= MAX_TELEGRAM_CHUNKS


@pytest.mark.parametrize(
    "response",
    [
        fake_response(302, headers={"Location": "https://evil.example/"}),
        fake_response(headers={"Content-Length": str(MAX_TELEGRAM_RESPONSE_BYTES + 1)}),
    ],
)
def test_telegram_rejects_redirects_and_oversized_responses(monkeypatch, response) -> None:
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: response)
    with pytest.raises(TelegramError):
        send_telegram("1234567890:abcdefghijklmnopqrstuvwxyz", "123", "test")


def test_telegram_streaming_read_enforces_global_deadline(monkeypatch) -> None:
    response = fake_response(body=b"")
    response.iter_content = lambda **_kwargs: iter([b"slow"])
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: response)
    values = iter([0.0, 0.0, 46.0])
    monkeypatch.setattr("app.services.telegram.time.monotonic", lambda: next(values))

    with pytest.raises(TelegramError, match="délai global"):
        send_telegram("1234567890:abcdefghijklmnopqrstuvwxyz", "123", "test")
