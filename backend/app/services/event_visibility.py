from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.models import Event

if TYPE_CHECKING:
    from app.security import AuthContext

OWNER_ONLY_EVENT_PREFIXES = (
    "account:",
    "auth:",
    "leaderboard:",
    "simulation:",
    "telegram:",
    "token:",
)
PRIMARY_OWNER_ONLY_EVENT_PREFIXES = ("private_comparison:",)
SIMULATION_EVENT_PREFIXES = ("simulation:",)


@dataclass(frozen=True, slots=True)
class EventVisibilityContext:
    role: str
    primary_owner: bool
    include_simulations: bool


def event_visibility_for(auth: AuthContext) -> EventVisibilityContext:
    from app.security import is_primary_owner

    return EventVisibilityContext(
        role=auth.role,
        primary_owner=is_primary_owner(auth),
        include_simulations=auth.session.share_token_id is None,
    )


def hidden_event_prefixes(context: EventVisibilityContext) -> tuple[str, ...]:
    prefixes: list[str] = []
    if context.role != "owner":
        prefixes.extend(OWNER_ONLY_EVENT_PREFIXES)
    if not context.primary_owner:
        prefixes.extend(PRIMARY_OWNER_ONLY_EVENT_PREFIXES)
    if not context.include_simulations:
        prefixes.extend(SIMULATION_EVENT_PREFIXES)
    return tuple(dict.fromkeys(prefixes))


def event_is_visible(kind: str, context: EventVisibilityContext) -> bool:
    return not kind.startswith(hidden_event_prefixes(context))


def event_visibility_filters(context: EventVisibilityContext) -> tuple[ColumnElement[bool], ...]:
    hidden = hidden_event_prefixes(context)
    if not hidden:
        return ()
    return (~or_(*(Event.kind.startswith(prefix) for prefix in hidden)),)
