from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models import Account

FIP_TRAINING_CALENDAR: dict[str, Any] = {
    "academic_year": "2026-2027",
    "title": "Calendrier d'alternance FIP 2026-2027",
    "speciality": "Informatique, réseaux et télécommunications",
    "source": {
        "label": "Calendrier de formation IMT Atlantique / ITII Bretagne",
        "version_date": "2026-04-28",
    },
    "promotions": [
        {
            "promotion_year": 2029,
            "level": "A1",
            "semesters": [
                {"semester": "S5", "start": "2026-09-01", "end": "2027-01-31"},
                {"semester": "S6", "start": "2027-02-01", "end": "2027-08-27"},
            ],
            "totals": {"school_weeks": 23, "company_weeks": 29},
            "periods": [
                {
                    "kind": "school",
                    "start": "2026-09-01",
                    "end": "2026-10-02",
                    "weeks": 5,
                    "campus": None,
                },
                {
                    "kind": "company",
                    "start": "2026-10-05",
                    "end": "2026-10-30",
                    "weeks": 4,
                    "campus": None,
                },
                {
                    "kind": "school",
                    "start": "2026-11-02",
                    "end": "2026-12-11",
                    "weeks": 6,
                    "campus": None,
                },
                {
                    "kind": "company",
                    "start": "2026-12-14",
                    "end": "2027-02-12",
                    "weeks": 9,
                    "campus": None,
                },
                {
                    "kind": "school",
                    "start": "2027-02-15",
                    "end": "2027-05-07",
                    "weeks": 12,
                    "campus": None,
                },
                {
                    "kind": "company",
                    "start": "2027-05-10",
                    "end": "2027-08-27",
                    "weeks": 16,
                    "campus": None,
                },
            ],
            "milestones": [
                {
                    "kind": "international_project",
                    "title": "PSI - Projet de séjour à l'international",
                    "start": "2027-05-10",
                    "end": "2027-08-27",
                    "detail": "Séjour de 9 à 12 semaines ; 12 semaines recommandées.",
                }
            ],
        },
        {
            "promotion_year": 2028,
            "level": "A2",
            "semesters": [
                {"semester": "S7", "start": "2026-08-31", "end": "2027-01-17"},
                {"semester": "S8", "start": "2027-01-18", "end": "2027-09-03"},
            ],
            "totals": {"school_weeks": 23, "company_weeks": 30},
            "periods": [
                {
                    "kind": "school",
                    "start": "2026-08-31",
                    "end": "2026-10-23",
                    "weeks": 8,
                    "campus": None,
                },
                {
                    "kind": "company",
                    "start": "2026-10-26",
                    "end": "2027-01-15",
                    "weeks": 12,
                    "campus": None,
                },
                {
                    "kind": "school",
                    "start": "2027-01-18",
                    "end": "2027-04-30",
                    "weeks": 15,
                    "campus": "Rennes",
                },
                {
                    "kind": "company",
                    "start": "2027-05-03",
                    "end": "2027-09-03",
                    "weeks": 18,
                    "campus": None,
                },
            ],
            "milestones": [],
        },
        {
            "promotion_year": 2027,
            "level": "A3",
            "semesters": [
                {"semester": "S9", "start": "2026-09-07", "end": "2027-03-26"},
                {"semester": "S10", "start": "2027-03-29", "end": "2027-08-27"},
            ],
            "totals": {"school_weeks": 23, "company_weeks": 28},
            "periods": [
                {
                    "kind": "school",
                    "start": "2026-09-07",
                    "end": "2026-12-18",
                    "weeks": 15,
                    "campus": "Brest",
                },
                {
                    "kind": "company",
                    "start": "2026-12-21",
                    "end": "2027-01-29",
                    "weeks": 6,
                    "campus": None,
                },
                {
                    "kind": "school",
                    "start": "2027-02-01",
                    "end": "2027-03-26",
                    "weeks": 8,
                    "campus": None,
                },
                {
                    "kind": "company",
                    "start": "2027-03-29",
                    "end": "2027-08-27",
                    "weeks": 22,
                    "campus": None,
                },
            ],
            "milestones": [
                {
                    "kind": "academic_mobility",
                    "title": "Semestre académique à l'étranger",
                    "start": "2026-09-07",
                    "end": "2027-03-26",
                    "detail": (
                        "Les dates de mobilité sont fixées par la convention ; hors période "
                        "de mobilité, l'apprenti est en entreprise."
                    ),
                }
            ],
        },
    ],
    "campus_note": (
        "Un campus n'est affiché que lorsqu'il est explicitement indiqué dans le calendrier source."
    ),
}


def is_fip(account: Account) -> bool:
    return account.program.strip().upper() == "FIP"


def training_calendar_view(account: Account) -> dict[str, Any]:
    result = deepcopy(FIP_TRAINING_CALENDAR)
    promotions = {item["promotion_year"] for item in result["promotions"]}
    result["default_promotion_year"] = (
        account.promotion_year if account.promotion_year in promotions else None
    )
    return result
