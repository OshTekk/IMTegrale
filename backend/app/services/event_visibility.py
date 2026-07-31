from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.sql.elements import ColumnElement

from app.event_contract import (
    EventVisibilityClass,
    conservative_event_visibility_class_for_kind,
)
from app.models import Event

if TYPE_CHECKING:
    from app.security import AuthContext

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


def visible_event_classes(context: EventVisibilityContext) -> tuple[str, ...]:
    classes = [EventVisibilityClass.SHARED.value]
    if context.role == "owner":
        classes.append(EventVisibilityClass.OWNER.value)
    if context.primary_owner:
        classes.append(EventVisibilityClass.PRIMARY_OWNER.value)
    if context.primary_owner and context.include_simulations:
        classes.append(EventVisibilityClass.SIMULATION.value)
    return tuple(classes)


def event_is_visible(kind: str, context: EventVisibilityContext) -> bool:
    visibility_class = conservative_event_visibility_class_for_kind(kind).value
    return visibility_class in visible_event_classes(context)


def event_visibility_filters(context: EventVisibilityContext) -> tuple[ColumnElement[bool], ...]:
    return (Event.visibility_class.in_(visible_event_classes(context)),)
