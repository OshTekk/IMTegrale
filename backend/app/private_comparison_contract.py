from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

PRIVATE_COMPARISON_CONSENT_VERSION = 3
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
    "eligibility_changed",
)
PRIVATE_COMPARISON_CONSENT_ACTOR_ROLES = ("creator", "acceptor")
PrivateComparisonConsentActorRole = Literal["creator", "acceptor"]


@dataclass(frozen=True)
class PrivateComparisonConsentField:
    response_path: str
    label: str


@dataclass(frozen=True)
class PrivateComparisonConsentSection:
    key: str
    title: str
    fields: tuple[PrivateComparisonConsentField, ...]


@dataclass(frozen=True)
class PrivateComparisonConsentExclusion:
    key: str
    label: str


_PRIVATE_COMPARISON_CONSENT_INCLUDED_SECTIONS = (
    PrivateComparisonConsentSection(
        key="official_identity",
        title="Identité",
        fields=(
            PrivateComparisonConsentField(
                response_path="participant.identity.official_name",
                label="Identité officielle de chaque participant",
            ),
        ),
    ),
    PrivateComparisonConsentSection(
        key="general_summary",
        title="Résumé général",
        fields=(
            PrivateComparisonConsentField("participant.summary.average", "Moyenne générale"),
            PrivateComparisonConsentField("participant.summary.gpa", "GPA général"),
            PrivateComparisonConsentField(
                "participant.summary.validated_ects",
                "ECTS validés",
            ),
            PrivateComparisonConsentField(
                "participant.summary.grade_distribution",
                "Répartition des grades",
            ),
            PrivateComparisonConsentField(
                "participant.summary.academic_verified_at",
                "Date de dernière vérification académique",
            ),
            PrivateComparisonConsentField(
                "participant.summary.freshness",
                "État de fraîcheur des données",
            ),
            PrivateComparisonConsentField(
                "participant.summary.ue_count",
                "Nombre d’UE prises en compte",
            ),
        ),
    ),
    PrivateComparisonConsentSection(
        key="common_ues",
        title="UE communes",
        fields=(
            PrivateComparisonConsentField("common_ues.official_code", "Code officiel"),
            PrivateComparisonConsentField(
                "common_ues.participant.title",
                "Intitulé",
            ),
            PrivateComparisonConsentField("common_ues.participant.year", "Année"),
            PrivateComparisonConsentField(
                "common_ues.participant.semester",
                "Semestre",
            ),
            PrivateComparisonConsentField(
                "common_ues.participant.average",
                "Moyenne",
            ),
            PrivateComparisonConsentField("common_ues.participant.grade", "Grade"),
            PrivateComparisonConsentField("common_ues.participant.gpa", "GPA"),
            PrivateComparisonConsentField(
                "common_ues.participant.earned_ects",
                "ECTS obtenus",
            ),
            PrivateComparisonConsentField(
                "common_ues.participant.allocated_ects",
                "ECTS alloués",
            ),
            PrivateComparisonConsentField(
                "common_ues.participant.validated",
                "État de validation",
            ),
            PrivateComparisonConsentField(
                "common_ues.participant.freshness",
                "État de fraîcheur",
            ),
            PrivateComparisonConsentField(
                "common_ues.participant.verified_at",
                "Date de dernière vérification",
            ),
        ),
    ),
    PrivateComparisonConsentSection(
        key="relation_metadata",
        title="Relation privée",
        fields=(
            PrivateComparisonConsentField(
                "relation.public_id",
                "Identifiant opaque de la comparaison",
            ),
            PrivateComparisonConsentField("relation.status", "Statut"),
            PrivateComparisonConsentField(
                "relation.activated_at",
                "Date d’activation",
            ),
            PrivateComparisonConsentField(
                "relation.expires_at",
                "Date d’expiration",
            ),
            PrivateComparisonConsentField(
                "relation.consent_version",
                "Version du consentement",
            ),
            PrivateComparisonConsentField(
                "relation.calculated_at",
                "Date de calcul de la vue",
            ),
        ),
    ),
)

_PRIVATE_COMPARISON_CONSENT_EXCLUDED_SECTIONS = (
    PrivateComparisonConsentExclusion("detailed_assessments", "Détail des évaluations"),
    PrivateComparisonConsentExclusion("assessment_labels", "Libellés des évaluations"),
    PrivateComparisonConsentExclusion("assessment_coefficients", "Coefficients des évaluations"),
    PrivateComparisonConsentExclusion("non_common_results", "Notes qui ne sont pas communes"),
    PrivateComparisonConsentExclusion("non_common_ues", "UE qui ne sont pas communes"),
    PrivateComparisonConsentExclusion("simulations", "Simulations"),
    PrivateComparisonConsentExclusion("agenda", "Agenda"),
    PrivateComparisonConsentExclusion("learning", "Parcours"),
    PrivateComparisonConsentExclusion("leaderboard_rank", "Rang dans le classement"),
    PrivateComparisonConsentExclusion("competition_score", "Score de compétition"),
    PrivateComparisonConsentExclusion("personal_comments", "Commentaires personnels"),
    PrivateComparisonConsentExclusion("third_party_data", "Données d’un troisième étudiant"),
    PrivateComparisonConsentExclusion("public_sharing", "Publication ou partage public"),
)


def _private_comparison_consent_manifest_body(
    *,
    actor_role: PrivateComparisonConsentActorRole,
) -> dict[str, object]:
    if actor_role == "creator":
        identity_description = (
            "Ton identité officielle sera visible dans l’aperçu de l’invitation. "
            "Si un étudiant qui l’accepte confirme son accord, son identité officielle "
            "te sera alors affichée et vous verrez réciproquement les données décrites "
            "ci-dessous."
        )
        identity_confirmation = (
            "Je comprends que mon identité officielle sera montrée à l’étudiant qui "
            "ouvre l’invitation avant son acceptation."
        )
    elif actor_role == "acceptor":
        identity_description = (
            "Tu vois l’identité officielle du créateur avant d’accepter. Si tu acceptes, "
            "ton identité officielle sera affichée au créateur et vous verrez "
            "réciproquement les données décrites ci-dessous."
        )
        identity_confirmation = (
            "J’accepte que mon identité officielle soit affichée au créateur après "
            "l’acceptation."
        )
    else:
        raise ValueError("Unsupported private comparison consent actor role")

    return {
        "consent_version": PRIVATE_COMPARISON_CONSENT_VERSION,
        "actor_role": actor_role,
        "identity_disclosure": {
            "description": identity_description,
            "confirmation": identity_confirmation,
        },
        "included_sections": [
            {
                "key": section.key,
                "title": section.title,
                "fields": [
                    {
                        "response_path": field.response_path,
                        "label": field.label,
                    }
                    for field in section.fields
                ],
            }
            for section in _PRIVATE_COMPARISON_CONSENT_INCLUDED_SECTIONS
        ],
        "excluded_sections": [
            {"key": section.key, "label": section.label}
            for section in _PRIVATE_COMPARISON_CONSENT_EXCLUDED_SECTIONS
        ],
        "duration_and_revocation": {
            "duration": ("La durée choisie commence après l’acceptation et ne dépasse jamais 90 jours."),
            "expiration": "La consultation cesse automatiquement à l’expiration.",
            "immediate_revocation": ("Chaque participant peut révoquer immédiatement la comparaison."),
            "minimal_history": (
                "Les identités et résultats bilatéraux sont visibles uniquement tant que la "
                "relation est active. Après sa fin, l’historique conserve seulement un statut "
                "minimal et sa date, sans identité ni résultat vivant."
            ),
            "private_only": (
                "La comparaison reste privée aux deux participants tant que la relation est active."
            ),
        },
        "academic_scope_confirmation": (
            "J’accepte le partage réciproque des seules données académiques incluses "
            "ci-dessus, à l’exclusion de toutes les autres données."
        ),
        "copy_risk": {
            "description": (
                "L’autre participant peut recopier ou capturer les informations visibles "
                "avant une révocation."
            ),
            "confirmation": (
                "Je comprends que les informations déjà copiées ou capturées ne peuvent "
                "pas être effacées par une révocation."
            ),
        },
    }


def _private_comparison_manifest_digest(manifest: dict[str, object]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def private_comparison_consent_manifest(
    *,
    actor_role: PrivateComparisonConsentActorRole,
) -> dict[str, object]:
    manifest = _private_comparison_consent_manifest_body(actor_role=actor_role)
    return {
        **manifest,
        "manifest_digest": _private_comparison_manifest_digest(manifest),
    }


def valid_private_comparison_consent(
    *,
    actor_role: str,
    consent_version: int,
    manifest_digest: str,
) -> bool:
    if (
        actor_role not in PRIVATE_COMPARISON_CONSENT_ACTOR_ROLES
        or consent_version != PRIVATE_COMPARISON_CONSENT_VERSION
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
    ):
        return False
    expected = private_comparison_consent_manifest(
        actor_role=actor_role,  # type: ignore[arg-type]
    )["manifest_digest"]
    return isinstance(expected, str) and secrets.compare_digest(
        manifest_digest,
        expected,
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
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


def valid_private_comparison_token(value: str) -> bool:
    return bool(_INVITATION_TOKEN_PATTERN.fullmatch(value))


def valid_private_comparison_invitation_public_id(value: str) -> bool:
    return bool(_INVITATION_PUBLIC_ID_PATTERN.fullmatch(value))


def valid_private_comparison_public_id(value: str) -> bool:
    return bool(_COMPARISON_PUBLIC_ID_PATTERN.fullmatch(value))
