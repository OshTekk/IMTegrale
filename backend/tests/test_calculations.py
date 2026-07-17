from app.calculations import grade_for_average, weighted_average


def test_grade_scale_and_resit_override() -> None:
    assert grade_for_average(17).grade == "A"
    assert grade_for_average(14).gpa == 3.8
    assert grade_for_average(12).grade == "C"
    assert grade_for_average(10).grade == "D"
    assert grade_for_average(9.99).grade == "FX"
    assert grade_for_average(4.99).grade == "F"
    assert grade_for_average(15, used_resit=True).grade == "E"
    assert grade_for_average(15, used_resit=True).gpa == 2.5


def test_ects_weighting() -> None:
    items = [
        {"average": 12.0, "credits_ects": 3},
        {"average": 16.0, "credits_ects": 6},
        {"average": 20.0, "credits_ects": None},
    ]
    assert weighted_average(items, "average") == (14.67, 9.0)
