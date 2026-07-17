from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.services.pass_gateway import cleanup_operational_data
from app.services.sync import sync_due_accounts

logger = logging.getLogger(__name__)


class AutomaticSyncScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycles = 0

    def start(self) -> None:
        if get_settings().environment == "test" or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="botnote-pass-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            self._thread = None

    def _run(self) -> None:
        poll_seconds = get_settings().scheduler_poll_seconds
        while not self._stop.wait(poll_seconds):
            try:
                sync_due_accounts()
                self._cycles += 1
                if self._cycles % max(1, 3600 // poll_seconds) == 0:
                    cleanup_operational_data()
            except Exception:
                logger.exception("Automatic synchronization scheduler cycle failed")


automatic_sync_scheduler = AutomaticSyncScheduler()
