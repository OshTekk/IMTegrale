from types import SimpleNamespace

from app.config import get_settings
from app.routers import events
from app.routers.events import get_stream_auth, stream_event_payload


def test_stream_auth_closes_its_database_context_before_return(monkeypatch) -> None:
    state = {"exited": False}

    class SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            state["exited"] = True

    monkeypatch.setattr(events, "SessionLocal", SessionContext)
    monkeypatch.setattr(
        events,
        "get_auth_context",
        lambda *_args: SimpleNamespace(
            account=SimpleNamespace(id="account-id"),
            session=SimpleNamespace(id="session-id"),
        ),
    )

    auth = get_stream_auth(object(), get_settings())

    assert state["exited"] is True
    assert auth.account_id == "account-id"


def test_sse_update_payload_contains_no_event_metadata() -> None:
    event = SimpleNamespace(id=42, kind="token:created", payload={"prefix": "secret"})

    assert stream_event_payload(event) == {"id": 42}
