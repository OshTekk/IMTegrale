from __future__ import annotations

from enum import StrEnum


class EventVisibilityClass(StrEnum):
    SHARED = "shared"
    OWNER = "owner"
    PRIMARY_OWNER = "primary_owner"
    SIMULATION = "simulation"


EVENT_VISIBILITY_CLASSES = tuple(item.value for item in EventVisibilityClass)

# This table is deliberately exhaustive. New event families must make an
# explicit disclosure choice instead of silently becoming visible.
_EVENT_VISIBILITY_BY_PREFIX: tuple[tuple[str, EventVisibilityClass], ...] = (
    ("private_comparison:", EventVisibilityClass.PRIMARY_OWNER),
    ("simulation:", EventVisibilityClass.SIMULATION),
    ("account:", EventVisibilityClass.OWNER),
    ("auth:", EventVisibilityClass.OWNER),
    ("leaderboard:", EventVisibilityClass.OWNER),
    ("learning:", EventVisibilityClass.OWNER),
    ("telegram:", EventVisibilityClass.OWNER),
    ("token:", EventVisibilityClass.OWNER),
    ("calendar:", EventVisibilityClass.SHARED),
    ("note:", EventVisibilityClass.SHARED),
    ("pass_access:", EventVisibilityClass.SHARED),
    ("pass_session:", EventVisibilityClass.SHARED),
    ("passkey:", EventVisibilityClass.SHARED),
    ("security_setup:", EventVisibilityClass.SHARED),
    ("sync:", EventVisibilityClass.SHARED),
    ("sync_credential:", EventVisibilityClass.SHARED),
    ("ue:", EventVisibilityClass.SHARED),
)


def event_visibility_class_for_kind(kind: str) -> EventVisibilityClass:
    for prefix, visibility_class in _EVENT_VISIBILITY_BY_PREFIX:
        if kind.startswith(prefix):
            return visibility_class
    raise ValueError("Unknown event kind visibility")


def conservative_event_visibility_class_for_kind(kind: str) -> EventVisibilityClass:
    try:
        return event_visibility_class_for_kind(kind)
    except ValueError:
        return EventVisibilityClass.PRIMARY_OWNER
