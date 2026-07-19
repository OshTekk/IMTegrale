from __future__ import annotations

import html
from datetime import UTC, datetime

from app.calculations import grade_for_average, weighted_average
from app.services.calendar_feed import CalendarFeedError, parse_feed
from app.services.imt import parse_competency_api_payload, parse_pass_export
from hypothesis import given, settings
from hypothesis import strategies as st


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=-400, max_value=800).map(lambda value: value / 4),
            st.integers(min_value=1, max_value=240).map(lambda value: value / 4),
        ),
        min_size=1,
        max_size=40,
    )
)
def test_weighted_average_stays_within_inputs_and_tracks_credits(values: list[tuple[float, float]]) -> None:
    items = [{"score": score, "credits_ects": credits} for score, credits in values]

    average, credits = weighted_average(items, "score")

    assert average is not None
    assert min(score for score, _credits in values) <= average <= max(score for score, _credits in values)
    assert credits == round(sum(item_credits for _score, item_credits in values), 2)


@given(
    st.floats(min_value=0, max_value=20, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0, max_value=20, allow_nan=False, allow_infinity=False),
)
def test_grade_points_are_monotonic_for_regular_assessments(left: float, right: float) -> None:
    lower, upper = sorted((left, right))
    lower_grade = grade_for_average(lower)
    upper_grade = grade_for_average(upper)

    assert lower_grade is not None
    assert upper_grade is not None
    assert lower_grade.gpa <= upper_grade.gpa


@given(
    label=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" -_"),
        min_size=1,
        max_size=80,
    ).filter(lambda value: value.strip() != ""),
    score_quarters=st.integers(min_value=0, max_value=80),
    coefficient_quarters=st.integers(min_value=1, max_value=400),
)
@settings(max_examples=80)
def test_pass_export_parser_preserves_bounded_valid_rows(
    label: str,
    score_quarters: int,
    coefficient_quarters: int,
) -> None:
    score = score_quarters / 4
    coefficient = coefficient_quarters / 4
    document = (
        "<table><tr><td>Evaluation FIC123</td></tr>"
        f"<tr><td>{html.escape(label)}</td><td>Classique /20</td>"
        f"<td>{coefficient}</td><td>{score}</td></tr></table>"
    )

    entries = parse_pass_export(document)

    assert len(entries) == 1
    assert entries[0].ue_code == "FIC123"
    assert entries[0].label == " ".join(label.split())
    assert entries[0].score == score
    assert entries[0].coefficient == coefficient


@given(
    semester=st.integers(min_value=5, max_value=10),
    grade=st.sampled_from(["A", "B", "C", "D", "E", "FX", "F", None]),
    credits_quarters=st.integers(min_value=1, max_value=240),
    earned_ratio=st.integers(min_value=0, max_value=100),
)
def test_competency_parser_normalizes_valid_engineering_semesters(
    semester: int,
    grade: str | None,
    credits_quarters: int,
    earned_ratio: int,
) -> None:
    credits = credits_quarters / 4
    earned = round(credits * earned_ratio / 100, 2)
    row = {
        "valide": "Validé",
        "semestre": f"S{semester}",
        "nom": f"UE fictive S{semester}",
        "code": "FIP-FIC123-BR-2099",
        "grade_calcule": grade,
        "credit_presente": credits,
        "credit_calcule": earned,
    }

    entries = parse_competency_api_payload({"data": [row, dict(row)]})

    assert len(entries) == 1
    assert entries[0].ue_code == "FIC123"
    assert entries[0].semester == f"S{semester}"
    assert entries[0].credits_ects == credits
    assert entries[0].earned_credits_ects == earned
    assert entries[0].grade == grade


@given(st.binary(max_size=2_048))
@settings(max_examples=100, deadline=None)
def test_calendar_parser_contains_arbitrary_bounded_input(body: bytes) -> None:
    try:
        parsed = parse_feed(body, now=datetime(2099, 1, 1, tzinfo=UTC))
    except CalendarFeedError:
        return

    assert len(parsed.events) <= 10_000
    assert list(parsed.events) == sorted(
        parsed.events,
        key=lambda item: (item.starts_at, item.ends_at, item.title),
    )
