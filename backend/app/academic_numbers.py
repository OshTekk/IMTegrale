from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SCORE_QUANTUM = Decimal("0.01")
COEFFICIENT_QUANTUM = Decimal("0.001")
ECTS_QUANTUM = Decimal("0.01")


def quantize_academic(value: Decimal | float | int | str, quantum: Decimal) -> Decimal:
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid academic quantity") from exc
    if not decimal.is_finite():
        raise ValueError("Academic quantities must be finite")
    try:
        result = decimal.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Invalid academic quantity") from exc
    return result


def score_decimal(value: Decimal | float | int | str) -> Decimal:
    return quantize_academic(value, SCORE_QUANTUM)


def coefficient_decimal(value: Decimal | float | int | str) -> Decimal:
    return quantize_academic(value, COEFFICIENT_QUANTUM)


def ects_decimal(value: Decimal | float | int | str) -> Decimal:
    return quantize_academic(value, ECTS_QUANTUM)
