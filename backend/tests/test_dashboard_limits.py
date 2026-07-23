from app.limits import MAX_DASHBOARD_NOTES
from app.services.imt import MAX_PASS_ENTRIES


def test_dashboard_can_expose_every_active_pass_entry() -> None:
    assert MAX_DASHBOARD_NOTES >= MAX_PASS_ENTRIES
