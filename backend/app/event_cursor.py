from __future__ import annotations

import re
import secrets

EVENT_CURSOR_PREFIX = "evc1_"
EVENT_CURSOR_RANDOM_BYTES = 24
EVENT_CURSOR_LENGTH = len(EVENT_CURSOR_PREFIX) + 32
EVENT_CURSOR_PATTERN = re.compile(r"^evc1_[A-Za-z0-9_-]{32}$")


def generate_event_cursor() -> str:
    """Return a non-ordered, 192-bit public cursor for one account event."""

    value = EVENT_CURSOR_PREFIX + secrets.token_urlsafe(EVENT_CURSOR_RANDOM_BYTES)
    if len(value) != EVENT_CURSOR_LENGTH or EVENT_CURSOR_PATTERN.fullmatch(value) is None:
        raise RuntimeError("event cursor generation violated its public contract")
    return value


def valid_event_cursor(value: object) -> bool:
    return isinstance(value, str) and EVENT_CURSOR_PATTERN.fullmatch(value) is not None
