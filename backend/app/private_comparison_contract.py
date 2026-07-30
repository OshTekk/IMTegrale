from __future__ import annotations

import re
from enum import StrEnum

PRIVATE_COMPARISON_CONSENT_VERSION = 1
PRIVATE_COMPARISON_TOKEN_VERSION = 1
PRIVATE_COMPARISON_TOKEN_ENTROPY_BYTES = 32
PRIVATE_COMPARISON_INVITATION_TTL_DAYS = 7
PRIVATE_COMPARISON_DEFAULT_DURATION_DAYS = 30
PRIVATE_COMPARISON_MAX_DURATION_DAYS = 90

PRIVATE_COMPARISON_TOKEN_PREFIX = "pcinv1_"  # noqa: S105 - public token format marker
PRIVATE_COMPARISON_INVITATION_PUBLIC_ID_PREFIX = "pci_"
PRIVATE_COMPARISON_PUBLIC_ID_PREFIX = "pc_"

PRIVATE_COMPARISON_INVITATION_SUPERSEDED_REASON = "superseded_relation_cycle"
PRIVATE_COMPARISON_INVITATION_REVOCATION_REASONS = (
    "creator_revoked",
    "declined",
    "operator_revoked",
    PRIVATE_COMPARISON_INVITATION_SUPERSEDED_REASON,
)
PRIVATE_COMPARISON_REVOCATION_REASONS = (
    "participant_revoked",
    "operator_revoked",
)

_INVITATION_TOKEN_PATTERN = re.compile(r"^pcinv1_[A-Za-z0-9_-]{43}$")
_INVITATION_PUBLIC_ID_PATTERN = re.compile(r"^pci_[A-Za-z0-9_-]{24}$")
_COMPARISON_PUBLIC_ID_PATTERN = re.compile(r"^pc_[A-Za-z0-9_-]{24}$")


class PrivateComparisonInvitationStatus(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class PrivateComparisonStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


def valid_private_comparison_token(value: str) -> bool:
    return bool(_INVITATION_TOKEN_PATTERN.fullmatch(value))


def valid_private_comparison_invitation_public_id(value: str) -> bool:
    return bool(_INVITATION_PUBLIC_ID_PATTERN.fullmatch(value))


def valid_private_comparison_public_id(value: str) -> bool:
    return bool(_COMPARISON_PUBLIC_ID_PATTERN.fullmatch(value))
