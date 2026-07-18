from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.services.calendar_feed import cleanup_fetch_attempts, refresh_due_subscriptions

logger = logging.getLogger(__name__)


class CalendarSyncScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycles = 0

    def start(self) -> None:
        if get_settings().environment == "test" or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="botnote-calendar-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None

    def _run(self) -> None:
        poll_seconds = max(30, get_settings().scheduler_poll_seconds)
        while not self._stop.wait(poll_seconds):
            try:
                refresh_due_subscriptions()
                self._cycles += 1
                if self._cycles % max(1, 3600 // poll_seconds) == 0:
                    cleanup_fetch_attempts()
            except Exception:
                logger.exception("Calendar synchronization scheduler cycle failed")


calendar_sync_scheduler = CalendarSyncScheduler()
