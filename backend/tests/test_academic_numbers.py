from __future__ import annotations

from decimal import Decimal

import pytest
from app.academic_numbers import coefficient_decimal, ects_decimal, score_decimal
from app.database import SessionLocal
from app.models import Account, Note, UeSetting
from sqlalchemy import select


def test_academic_rounding_uses_half_up_at_each_storage_boundary() -> None:
    assert score_decimal("14.125") == Decimal("14.13")
    assert coefficient_decimal("1.2345") == Decimal("1.235")
    assert ects_decimal("3.005") == Decimal("3.01")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_academic_rounding_rejects_non_finite_values(value: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        score_decimal(value)


def test_academic_quantities_round_trip_as_decimals() -> None:
    with SessionLocal() as db:
        account = Account(imt_username="decimal-fictif", display_name="Decimal fictif")
        db.add(account)
        db.flush()
        db.add(
            Note(
                account_id=account.id,
                source="pass",
                source_key="decimal-note",
                ue_code="UE100",
                raw_label="Évaluation fictive",
                raw_score=score_decimal("12.345"),
                raw_coefficient=coefficient_decimal("1.2345"),
            )
        )
        db.add(
            UeSetting(
                account_id=account.id,
                code="UE100",
                credits_ects=ects_decimal("6.005"),
                earned_credits_ects=ects_decimal("6.005"),
            )
        )
        db.commit()
        note = db.scalars(select(Note)).one()
        setting = db.scalars(select(UeSetting)).one()

        assert note.raw_score == Decimal("12.35")
        assert note.raw_coefficient == Decimal("1.235")
        assert setting.credits_ects == Decimal("6.01")
