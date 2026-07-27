from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_FORM_SOURCES = (
    PROJECT_ROOT
    / "frontend"
    / "src"
    / "components"
    / "sync"
    / "AutonomousSyncEnrollmentModal.tsx",
    PROJECT_ROOT / "frontend" / "src" / "components" / "PassReconnectModal.tsx",
)


def test_imt_password_is_not_persisted_logged_or_cached_in_frontend() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in SECRET_FORM_SOURCES)

    forbidden_patterns = (
        r"(?:localStorage|sessionStorage)\s*\.\s*setItem",
        r"console\.(?:log|info|warn|error)[^(]*\([^)]*password",
        r"queryClient\.setQueryData\([^)]*password",
        r"useState(?:<[^>]+>)?\([^)]*password",
        r"[?&]password=",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL) is None

    assert 'type={visible ? "text" : "password"}' in source
    assert 'autoComplete="current-password"' in source
    assert "passwordRef" in source
