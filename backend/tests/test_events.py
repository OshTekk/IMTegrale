import asyncio
import json
from types import SimpleNamespace

import pytest
from app.config import get_settings
from app.database import SessionLocal
from app.models import Account
from app.routers import events
from app.routers.events import StreamAuth, get_stream_auth, stream_event_payload, stream_events
from app.services.event_visibility import EventVisibilityContext, event_is_visible
from app.services.events import record_event
from fastapi import HTTPException


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
    event = SimpleNamespace(
        id=42,
        public_cursor=f"evc1_{'a' * 32}",
        kind="token:created",
        payload={"prefix": "secret"},
    )

    assert stream_event_payload(event) == {"cursor": f"evc1_{'a' * 32}"}


def test_sse_terminal_private_comparison_payload_is_minimal() -> None:
    public_id = f"pc_{'b' * 24}"
    event = SimpleNamespace(
        id=43,
        public_cursor=f"evc1_{'c' * 32}",
        kind="private_comparison:revoked",
        payload={"public_id": public_id, "ignored": "must-not-leak"},
    )

    assert stream_event_payload(event) == {
        "cursor": f"evc1_{'c' * 32}",
        "kind": "private_comparison:revoked",
        "public_id": public_id,
    }


def test_sse_response_uses_private_no_store_security_headers() -> None:
    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return True

    response = asyncio.run(
        stream_events(
            Request(),
            auth=StreamAuth(
                account_id="synthetic-account",
                session_id="synthetic-session",
                visibility=EventVisibilityContext(
                    role="owner",
                    primary_owner=False,
                    include_simulations=False,
                ),
            ),
        )
    )

    assert response.headers["cache-control"] == "private, no-store, no-transform"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Cookie"
    assert response.headers["x-content-type-options"] == "nosniff"


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


def test_sse_resume_uses_opaque_visible_cursor_across_hidden_event_gaps(monkeypatch) -> None:  # noqa: ANN001
    with SessionLocal() as db:
        account = Account(
            imt_username="sse-event-visibility@example.test",
            display_name="SSE Event Visibility Fixture",
        )
        db.add(account)
        db.flush()
        first_visible_event = record_event(
            db,
            account_id=account.id,
            kind="note:new",
            payload={"ue_code": "SYN100"},
        )
        hidden_events = [
            record_event(
                db,
                account_id=account.id,
                kind="private_comparison:activated",
                payload={"consent_version": f"synthetic-private-marker-{index}"},
            )
            for index in range(25)
        ]
        visible_event = record_event(
            db,
            account_id=account.id,
            kind="sync:completed",
            payload={"total": 1, "inserted": 1, "updated": 0},
        )
        db.commit()
        account_id = account.id
        first_visible_cursor = first_visible_event.public_cursor
        hidden_cursors = {event.public_cursor for event in hidden_events}
        visible_event_cursor = visible_event.public_cursor

    class ConnectedRequest:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    async def first_update() -> str:
        response = await stream_events(
            ConnectedRequest(),
            after=first_visible_cursor,
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
    assert payload == {"cursor": visible_event_cursor}
    assert update.startswith(f"id: {visible_event_cursor}\n")
    assert not hidden_cursors.intersection(update.split())


def test_sse_unknown_cross_account_and_hidden_cursors_share_one_generic_404() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="sse-cursor-owner@example.test",
            display_name="SSE Cursor Owner Fixture",
        )
        other = Account(
            imt_username="sse-cursor-other@example.test",
            display_name="SSE Cursor Other Fixture",
        )
        db.add_all([account, other])
        db.flush()
        hidden = record_event(
            db,
            account_id=account.id,
            kind="private_comparison:activated",
        )
        cross_account = record_event(
            db,
            account_id=other.id,
            kind="note:new",
        )
        db.commit()
        account_id = account.id
        cursors = [
            hidden.public_cursor,
            cross_account.public_cursor,
            f"evc1_{'z' * 32}",
            "17",
            "",
        ]

    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    auth = StreamAuth(
        account_id=account_id,
        session_id="unused-for-cursor-preflight",
        visibility=EventVisibilityContext(
            role="owner",
            primary_owner=False,
            include_simulations=False,
        ),
    )
    observed: list[tuple[int, object]] = []
    for cursor in cursors:
        with pytest.raises(HTTPException) as captured:
            asyncio.run(stream_events(Request(), after=cursor, auth=auth))
        observed.append((captured.value.status_code, captured.value.detail))

    assert observed == [
        (
            404,
            {
                "code": "EVENT_CURSOR_UNAVAILABLE",
                "message": "Curseur d’événement indisponible.",
            },
        )
    ] * len(cursors)
