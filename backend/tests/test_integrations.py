from __future__ import annotations

import pytest
import requests
from app.services.imt import CAS_LOGIN_URL, ImtFetchError, ImtPassClient
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
