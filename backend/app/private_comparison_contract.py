from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

PRIVATE_COMPARISON_CONSENT_VERSION = 2
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


def private_comparison_consent_manifest() -> dict[str, object]:
    return {
        "consent_version": PRIVATE_COMPARISON_CONSENT_VERSION,
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
        "copy_risk": (
            "L’autre participant peut recopier ou capturer les informations visibles avant une révocation."
        ),
    }


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
