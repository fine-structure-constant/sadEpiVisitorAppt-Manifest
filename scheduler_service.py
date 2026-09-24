"""Single-worker scheduler for persistent appointment jobs."""

from __future__ import annotations

import threading
from collections.abc import Callable

from queue_store import AppointmentQueue


class QueueScheduler:
    def __init__(
        self,
        queue: AppointmentQueue,
        processor: Callable[[dict], None],
        *,
        poll_seconds: float = 1,
    ):
        self.queue = queue
        self.processor = processor
        self.poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> threading.Thread:
        with self._lock:
            if not self.running:
                self._thread = threading.Thread(
                    target=self._loop,
                    name="appointment-scheduler",
                    daemon=True,
                )
                self._thread.start()
            return self._thread

    def _loop(self) -> None:
        while True:
            job = self.queue.claim_due()
            if job is not None:
                self.processor(job)
                continue
            self._wake.wait(timeout=self.poll_seconds)
            self._wake.clear()
