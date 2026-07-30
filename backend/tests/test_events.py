import asyncio
import json
from types import SimpleNamespace

from app.config import get_settings
from app.database import SessionLocal
from app.models import Account
from app.routers import events
from app.routers.events import StreamAuth, get_stream_auth, stream_event_payload, stream_events
from app.services.event_visibility import EventVisibilityContext, event_is_visible
from app.services.events import record_event


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
            role="owner",
            session=SimpleNamespace(
                id="session-id",
                auth_method="token",
                share_token_id="synthetic-share-token",
            ),
        ),
    )

    auth = get_stream_auth(object(), get_settings())

    assert state["exited"] is True
    assert auth.account_id == "account-id"
    assert auth.visibility == EventVisibilityContext(
        role="owner",
        primary_owner=False,
        include_simulations=False,
    )


def test_sse_update_payload_contains_no_event_metadata() -> None:
    event = SimpleNamespace(id=42, kind="token:created", payload={"prefix": "secret"})

    assert stream_event_payload(event) == {"id": 42}


def test_event_visibility_keeps_existing_role_policy_and_adds_primary_assurance() -> None:
    primary = EventVisibilityContext(role="owner", primary_owner=True, include_simulations=True)
    delegated_owner = EventVisibilityContext(role="owner", primary_owner=False, include_simulations=False)
    delegated_viewer = EventVisibilityContext(role="viewer", primary_owner=False, include_simulations=False)
    delegated_editor = EventVisibilityContext(role="editor", primary_owner=False, include_simulations=False)

    assert event_is_visible("private_comparison:activated", primary) is True
    assert event_is_visible("private_comparison:activated", delegated_owner) is False
    assert event_is_visible("private_comparison:activated", delegated_viewer) is False
    assert event_is_visible("private_comparison:activated", delegated_editor) is False
    assert event_is_visible("token:created", delegated_owner) is True
    assert event_is_visible("token:created", delegated_viewer) is False
    assert event_is_visible("simulation:saved", delegated_owner) is False
    assert event_is_visible("note:new", delegated_viewer) is True
    assert event_is_visible("sync:completed", delegated_editor) is True


def test_sse_query_skips_private_comparison_ids_for_delegated_owner(monkeypatch) -> None:  # noqa: ANN001
    with SessionLocal() as db:
        account = Account(
            imt_username="sse-event-visibility@example.test",
            display_name="SSE Event Visibility Fixture",
        )
        db.add(account)
        db.flush()
        private_event = record_event(
            db,
            account_id=account.id,
            kind="private_comparison:activated",
            payload={"consent_version": "synthetic-private-marker"},
        )
        visible_event = record_event(
            db,
            account_id=account.id,
            kind="note:new",
            payload={"ue_code": "SYN101"},
        )
        db.commit()
        account_id = account.id
        private_event_id = private_event.id
        visible_event_id = visible_event.id

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def first_update() -> str:
        response = await stream_events(
            ConnectedRequest(),
            after=0,
            auth=StreamAuth(
                account_id=account_id,
                session_id="unused-by-active-session-mock",
                visibility=EventVisibilityContext(
                    role="owner",
                    primary_owner=False,
                    include_simulations=False,
                ),
            ),
        )
        iterator = response.body_iterator
        retry = await anext(iterator)
        update = await anext(iterator)
        await iterator.aclose()
        assert retry == "retry: 3000\n\n"
        return update

    monkeypatch.setattr(events, "session_is_active", lambda *_args: True)
    update = asyncio.run(first_update())

    payload_line = next(line for line in update.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.removeprefix("data: "))
    assert payload == {"id": visible_event_id}
    assert payload["id"] != private_event_id
