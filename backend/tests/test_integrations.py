from __future__ import annotations

import json

import pytest
import requests
from app.services.imt import (
    CAS_LOGIN_URL,
    COMPETENCIES_CSRF_URL,
    COMPETENCIES_HOME_URL,
    COMPETENCIES_LOGIN_URL,
    COMPETENCIES_LOGOUT_URL,
    COMPETENCIES_RESULTS_BASE_URL,
    COMPETENCIES_USER_URL,
    IMT_ATLANTIQUE_IDP_ENTITY_ID,
    MAX_REQUESTS_PER_OPERATION,
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


def fake_json_response(payload: object, *, url: str) -> requests.Response:
    return fake_response(
        url=url,
        body=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


class RecordingSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.trust_env = True
        self.closed = False

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        self.close()

    def post(self, *_args, **_kwargs) -> requests.Response:  # noqa: ANN002, ANN003
        assert self.trust_env is False
        return self.response

    def close(self) -> None:
        self.closed = True


def test_imt_http_session_ignores_environment_proxies() -> None:
    client = ImtPassClient()
    try:
        assert client.session.trust_env is False
    finally:
        client.session.close()


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


def test_imt_operation_keeps_a_hard_request_ceiling(monkeypatch) -> None:
    client = ImtPassClient()
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *_args, **_kwargs: fake_response(url=CAS_LOGIN_URL),
    )

    with client._operation():
        for _ in range(MAX_REQUESTS_PER_OPERATION):
            client._get(CAS_LOGIN_URL)
        with pytest.raises(ImtFetchError, match="anormalement long"):
            client._get(CAS_LOGIN_URL)


def test_competencies_api_uses_csrf_bearer_and_current_student_only(monkeypatch) -> None:
    client = ImtPassClient()
    dashboard = fake_response(url=COMPETENCIES_HOME_URL, body=b"student dashboard")
    csrf = fake_response(status=204, url=COMPETENCIES_CSRF_URL, body=b"")
    login = fake_json_response(
        {"token": "42|abcdefghijklmnopqrstuvwxyz"},
        url=COMPETENCIES_LOGIN_URL,
    )
    logout = fake_response(status=204, url=COMPETENCIES_LOGOUT_URL, body=b"")
    user = fake_json_response(
        {"roles": [{"name": "etudiant"}], "etudiant": {"etudiant_id": 40419}},
        url=COMPETENCIES_USER_URL,
    )
    results_url = f"{COMPETENCIES_RESULTS_BASE_URL}/40419"
    results = fake_json_response(
        {
            "data": [
                {
                    "nom": "Outils mathématiques pour l'ingénieur S5",
                    "semestre": "Semestre 1",
                    "valide": "Validé",
                    "code": "FIP-SIT130-BR-2025",
                    "grade_calcule": "E",
                    "credit_calcule": "4.00",
                    "credit_presente": "4.00",
                }
            ]
        },
        url=results_url,
    )
    gets: list[tuple[str, dict]] = []
    posts: list[tuple[str, tuple, dict]] = []

    def get(url: str, **kwargs) -> requests.Response:
        gets.append((url, kwargs))
        if url == COMPETENCIES_HOME_URL:
            return dashboard
        if url == COMPETENCIES_CSRF_URL:
            client.session.cookies.set(
                "XSRF-TOKEN",
                "opaque%7Ccsrf-token-1234567890",
                domain="hub.imt-atlantique.fr",
                path="/",
                secure=True,
            )
            return csrf
        if url == COMPETENCIES_USER_URL:
            return user
        if url == results_url:
            return results
        raise AssertionError(f"unexpected GET {url}")

    def post(url: str, *, data: tuple, **kwargs) -> requests.Response:
        posts.append((url, data, kwargs))
        if url == COMPETENCIES_LOGIN_URL:
            return login
        if url == COMPETENCIES_LOGOUT_URL:
            return logout
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(client, "_get", get)
    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(client, "_complete_hub_sso", lambda response, _credentials: response)

    entries = client.fetch_competency_ues_authenticated(credentials=("student", "secret"))

    assert entries[0].ue_code == "SIT130"
    assert entries[0].semester == "S5"
    assert entries[0].grade == "E"
    assert posts[0][0] == COMPETENCIES_LOGIN_URL
    assert posts[0][1] == ()
    assert posts[0][2]["headers"]["X-XSRF-TOKEN"] == "opaque|csrf-token-1234567890"
    assert posts[0][2]["headers"]["Origin"] == "https://hub.imt-atlantique.fr"
    assert gets[-2][0] == COMPETENCIES_USER_URL
    assert gets[-1][0] == results_url
    assert gets[-1][1]["headers"]["Authorization"] == "Bearer 42|abcdefghijklmnopqrstuvwxyz"
    assert posts[-1][0] == COMPETENCIES_LOGOUT_URL
    assert posts[-1][2]["headers"]["Authorization"] == "Bearer 42|abcdefghijklmnopqrstuvwxyz"
    assert "Authorization" not in client.session.headers


def test_competencies_api_accepts_current_no_xsrf_flow_and_string_student_id(
    monkeypatch,
) -> None:
    client = ImtPassClient()
    dashboard = fake_response(url=COMPETENCIES_HOME_URL, body=b"student dashboard")
    csrf = fake_response(status=204, url=COMPETENCIES_CSRF_URL, body=b"")
    login = fake_json_response(
        {"token": "42|abcdefghijklmnopqrstuvwxyz"},
        url=COMPETENCIES_LOGIN_URL,
    )
    logout = fake_response(status=204, url=COMPETENCIES_LOGOUT_URL, body=b"")
    user = fake_json_response(
        {"etudiant": {"etudiant_id": "40419"}},
        url=COMPETENCIES_USER_URL,
    )
    results_url = f"{COMPETENCIES_RESULTS_BASE_URL}/40419"
    results = fake_json_response({"data": []}, url=results_url)
    gets: list[str] = []
    posts: list[tuple[str, dict]] = []

    def get(url: str, **_kwargs) -> requests.Response:
        gets.append(url)
        return {
            COMPETENCIES_HOME_URL: dashboard,
            COMPETENCIES_CSRF_URL: csrf,
            COMPETENCIES_USER_URL: user,
            results_url: results,
        }[url]

    def post(url: str, *, data: tuple, **kwargs) -> requests.Response:
        assert data == ()
        posts.append((url, kwargs))
        return login if url == COMPETENCIES_LOGIN_URL else logout

    monkeypatch.setattr(client, "_get", get)
    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(client, "_complete_hub_sso", lambda response, _credentials: response)

    assert client.fetch_competency_ues_authenticated(credentials=("student", "secret")) == []
    assert gets[-1] == results_url
    assert "X-XSRF-TOKEN" not in posts[0][1]["headers"]
    assert "X-XSRF-TOKEN" not in posts[-1][1]["headers"]
    assert posts[-1][1]["headers"]["Authorization"] == "Bearer 42|abcdefghijklmnopqrstuvwxyz"


@pytest.mark.parametrize(
    ("domain", "path", "secure"),
    [
        (".imt-atlantique.fr", "/", True),
        ("hub.imt-atlantique.fr", "/other", True),
        ("hub.imt-atlantique.fr", "/", False),
    ],
)
def test_competencies_api_rejects_untrusted_xsrf_cookie(
    domain: str,
    path: str,
    secure: bool,
) -> None:
    client = ImtPassClient()
    client.session.cookies.set(
        "XSRF-TOKEN",
        "opaque-csrf-token-1234567890",
        domain=domain,
        path=path,
        secure=secure,
    )

    with pytest.raises(ImtFetchError, match="protection CSRF invalide"):
        client._hub_xsrf_token()


def test_competencies_api_rejects_ambiguous_xsrf_cookies() -> None:
    client = ImtPassClient()
    for path in ("/", "/comp2"):
        client.session.cookies.set(
            "XSRF-TOKEN",
            "opaque-csrf-token-1234567890",
            domain="hub.imt-atlantique.fr",
            path=path,
            secure=True,
        )

    with pytest.raises(ImtFetchError, match="protection CSRF ambiguë"):
        client._hub_xsrf_token()


@pytest.mark.parametrize(
    "student_id",
    [True, 0, 10**12 + 1, 40419.0, "", "٤٠٤١٩", "40419/../1", None],
)
def test_competencies_api_rejects_invalid_student_ids(student_id: object) -> None:
    with pytest.raises(ImtFetchError, match="identifiant étudiant valide"):
        ImtPassClient._hub_student_id({"etudiant": {"etudiant_id": student_id}})


def test_competencies_api_rejects_a_non_json_user_response(monkeypatch) -> None:
    client = ImtPassClient()
    dashboard = fake_response(url=COMPETENCIES_HOME_URL, body=b"student dashboard")
    csrf = fake_response(status=204, url=COMPETENCIES_CSRF_URL, body=b"")
    login = fake_json_response(
        {"token": "42|abcdefghijklmnopqrstuvwxyz"},
        url=COMPETENCIES_LOGIN_URL,
    )
    html_user = fake_response(
        url=COMPETENCIES_USER_URL,
        body=b"<html>unexpected</html>",
        headers={"Content-Type": "text/html"},
    )

    def get(url: str, **_kwargs) -> requests.Response:
        if url == COMPETENCIES_HOME_URL:
            return dashboard
        if url == COMPETENCIES_CSRF_URL:
            client.session.cookies.set(
                "XSRF-TOKEN",
                "opaque-csrf-token-1234567890",
                domain="hub.imt-atlantique.fr",
                path="/",
                secure=True,
            )
            return csrf
        return html_user

    monkeypatch.setattr(client, "_get", get)
    monkeypatch.setattr(client, "_post", lambda *_args, **_kwargs: login)
    monkeypatch.setattr(client, "_complete_hub_sso", lambda response, _credentials: response)

    with pytest.raises(ImtFetchError, match="format inattendu"):
        client.fetch_competency_ues_authenticated(credentials=("student", "secret"))


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


def test_prime_competency_session_opens_sso_without_reading_results(monkeypatch) -> None:
    client = ImtPassClient()
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b"student dashboard",
    )
    calls: list[tuple[requests.Response, tuple[str, str] | None]] = []

    monkeypatch.setattr(client, "_get", lambda url: dashboard if url == COMPETENCIES_HOME_URL else None)
    monkeypatch.setattr(
        client,
        "_complete_hub_sso",
        lambda response, credentials: calls.append((response, credentials)),
    )

    client.prime_competency_session("student", "secret")

    assert calls == [(dashboard, ("student", "secret"))]
    assert client.last_competency_attempted is True
    assert client.last_competency_succeeded is True
    assert client.last_competency_ues is None


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


def test_cas_accepts_the_official_shibboleth_consent_form(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/Shibboleth/SSO?execution=e1s2",
        body=(
            b'<form action="?execution=e1s2">'
            b'<input type="hidden" name="csrf_token" value="opaque">'
            b'<button name="_eventId_proceed" value="Accept">Continuer</button></form>'
        ),
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419/home",
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
            "https://idp.imt-atlantique.fr/idp/profile/Shibboleth/SSO?execution=e1s2",
            [("csrf_token", "opaque"), ("_eventId_proceed", "Accept")],
        )
    ]


def test_cas_accepts_the_official_shibboleth_session_path(monkeypatch) -> None:
    client = ImtPassClient()
    session_path = "/idp/profile/Shibboleth/SSO;jsessionid=0123456789ABCDEF0123456789ABCDEF"
    consent = fake_response(
        url=f"https://idp.imt-atlantique.fr{session_path}?execution=e1s2",
        body=(
            f'<form action="{session_path}?execution=e1s2">'
            '<input type="hidden" name="csrf_token" value="opaque">'
            '<button name="_eventId_proceed" value="Accept">Continuer</button></form>'
        ).encode(),
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419/home",
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
            f"https://idp.imt-atlantique.fr{session_path}?execution=e1s2",
            [("csrf_token", "opaque"), ("_eventId_proceed", "Accept")],
        )
    ]


def test_cas_rejects_a_shibboleth_lookalike_terminal_page(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/Shibboleth/SSO?execution=e1s2",
        body=(
            b'<form action="/idp/profile/Shibboleth/SSO/continue?execution=e1s2">'
            b'<button name="_eventId_proceed" value="Accept">Continuer</button></form>'
        ),
    )
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("an unexpected IdP path must not be submitted")

    monkeypatch.setattr(client, "_post", unexpected_post)

    with pytest.raises(ImtFetchError, match="service protégé"):
        client._complete_cas(consent, "student", "secret")
    assert called is False


def test_cas_rejects_an_unbounded_shibboleth_terminal_page(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/Shibboleth/SSO",
        body=(
            b'<form action="/idp/profile/Shibboleth/SSO;jsessionid=short">'
            b'<button name="_eventId_proceed" value="Accept">Continuer</button></form>'
        ),
    )
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("an invalid IdP session path must not be submitted")

    monkeypatch.setattr(client, "_post", unexpected_post)

    with pytest.raises(ImtFetchError, match="service protégé"):
        client._complete_cas(consent, "student", "secret")
    assert called is False


def test_cas_rejects_an_unknown_allowed_origin_terminal_page() -> None:
    client = ImtPassClient()
    terminal = fake_response(
        url="https://pass.imt-atlantique.fr/unexpected/terminal",
        body=b"generic upstream page",
    )

    with pytest.raises(ImtFetchError, match="service protégé"):
        client._complete_cas(terminal, "student", "fictional-password")


def test_cas_follows_an_official_action_only_saml_relay(monkeypatch) -> None:
    client = ImtPassClient()
    relay = fake_response(
        url="https://cas.imt-atlantique.fr/cas/login",
        body=(
            b'<form action="https://pass.imt-atlantique.fr/SAML2/POST/SSO">'
            b'<input type="hidden" name="RelayState" value="opaque"></form>'
        ),
    )
    report = fake_response(
        url="https://pass.imt-atlantique.fr/OpDotNet/",
        body=b"PASS",
    )
    posts: list[tuple[str, list[tuple[str, str]]]] = []

    def post(url: str, *, data: list[tuple[str, str]], **_kwargs) -> requests.Response:
        posts.append((url, data))
        return report

    monkeypatch.setattr(client, "_post", post)

    assert client._complete_cas(relay, "student", "secret") is report
    assert posts == [
        (
            "https://pass.imt-atlantique.fr/SAML2/POST/SSO",
            [("RelayState", "opaque")],
        )
    ]


def test_cas_rejects_an_untrusted_action_only_saml_relay(monkeypatch) -> None:
    client = ImtPassClient()
    relay = fake_response(
        body=(
            b'<form action="https://evil.example/SAML2/POST/SSO">'
            b'<input type="hidden" name="RelayState" value="opaque"></form>'
        )
    )
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("the SAML relay must remain on an official IMT origin")

    monkeypatch.setattr(client, "_post", unexpected_post)

    with pytest.raises(ImtFetchError):
        client._complete_cas(relay, "student", "secret")
    assert called is False


def test_cas_follows_an_official_idp_proceed_link(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/SAML2/Redirect/SSO?execution=e1s2",
        body=(
            b'<a href="/idp/profile/SAML2/Redirect/SSO?execution=e1s2&amp;'
            b'_eventId=proceed&amp;csrf=opaque">Continuer</a>'
        ),
    )
    dashboard = fake_response(
        url="https://hub.imt-atlantique.fr/comp2/etudiant/40419",
        body=b"student dashboard",
    )
    gets: list[str] = []

    def get(url: str, **_kwargs) -> requests.Response:
        gets.append(url)
        return dashboard

    monkeypatch.setattr(client, "_get", get)

    assert client._complete_cas(consent, "student", "secret") is dashboard
    assert gets == [
        "https://idp.imt-atlantique.fr/idp/profile/SAML2/Redirect/SSO"
        "?execution=e1s2&_eventId=proceed&csrf=opaque"
    ]


def test_cas_accepts_an_eventid_proceed_button(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/SAML2/POST/SSO?execution=e1s2",
        body=(
            b'<form action="?execution=e1s2">'
            b'<input type="hidden" name="csrf" value="opaque">'
            b'<button name="_eventId" value="proceed">Continuer</button></form>'
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
            "https://idp.imt-atlantique.fr/idp/profile/SAML2/POST/SSO?execution=e1s2",
            [("csrf", "opaque"), ("_eventId", "proceed")],
        )
    ]


def test_cas_rejects_an_untrusted_idp_proceed_link(monkeypatch) -> None:
    client = ImtPassClient()
    consent = fake_response(
        url="https://idp.imt-atlantique.fr/idp/profile/SAML2/Redirect/SSO?execution=e1s2",
        body=(
            b'<a href="https://evil.example/idp/profile/SAML2/Redirect/SSO?'
            b'_eventId=proceed">Continuer</a>'
        ),
    )
    called = False

    def unexpected_get(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("the IdP continuation must remain on the official origin")

    monkeypatch.setattr(client, "_get", unexpected_get)

    with pytest.raises(ImtFetchError):
        client._complete_cas(consent, "student", "secret")
    assert called is False


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
    session = RecordingSession(response)
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(TelegramError):
        send_telegram("1234567890:abcdefghijklmnopqrstuvwxyz", "123", "test")
    assert session.closed is True


def test_telegram_streaming_read_enforces_global_deadline(monkeypatch) -> None:
    response = fake_response(body=b"")
    response.iter_content = lambda **_kwargs: iter([b"slow"])
    session = RecordingSession(response)
    monkeypatch.setattr(requests, "Session", lambda: session)
    values = iter([0.0, 0.0, 46.0])
    monkeypatch.setattr("app.services.telegram.time.monotonic", lambda: next(values))

    with pytest.raises(TelegramError, match="délai global"):
        send_telegram("1234567890:abcdefghijklmnopqrstuvwxyz", "123", "test")
    assert session.closed is True
