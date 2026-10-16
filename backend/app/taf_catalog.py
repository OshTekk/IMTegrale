from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TafCatalogItem:
    code: str
    name: str
    campus: str
    category: str
    display_category: str


TAF_CATALOG: tuple[TafCatalogItem, ...] = (
    TafCatalogItem(
        code="NetCloud",
        name="Networks and Cloud",
        campus="Rennes",
        category="CORE",
        display_category="Cœur de métier",
    ),
    TafCatalogItem(
        code="ILSD",
        name="Ingénierie Logicielle et Systèmes Distribués",
        campus="Brest",
        category="CORE",
        display_category="Cœur de métier",
    ),
    TafCatalogItem(
        code="OPE",
        name="Observation et Perception de l’Environnement",
        campus="Brest",
        category="CORE",
        display_category="Cœur de métier",
    ),
    TafCatalogItem(
        code="STAR",
        name="Système de Transmission, Architectures et Réseaux",
        campus="Brest",
        category="CORE",
        display_category="Cœur de métier",
    ),
    TafCatalogItem(
        code="Cyber",
        name="Cybersécurité",
        campus="Rennes",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
    TafCatalogItem(
        code="COUAD",
        name="Conception d’Objets Utiles Accessibles et Durables",
        campus="Brest",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
    TafCatalogItem(
        code="SEH",
        name="Systèmes Embarqués et Hétérogènes",
        campus="Brest",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
    TafCatalogItem(
        code="Dasci",
        name="Data science : des données au décideur",
        campus="Brest",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
    TafCatalogItem(
        code="HEALTH",
        name="Healthcare Engineering",
        campus="Brest",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
    TafCatalogItem(
        code="TERO",
        name="Transformation Ecologique et Robustesse des Organisations",
        campus="Brest",
        category="COMPLEMENTARY",
        display_category="Complémentaires",
    ),
)

TAF_BY_CODE = {item.code: item for item in TAF_CATALOG}
TAF_CODES = frozenset(TAF_BY_CODE)
TAF_CATEGORIES = frozenset({"CORE", "COMPLEMENTARY"})
TAF_SCOPES = frozenset({"v1", "top3", "any"})
TAF_DISCLAIMER = (
    "Déclarations indicatives des participants au classement. Elles ne constituent "
    "pas les vœux officiels ni une affectation IMT Atlantique."
)
