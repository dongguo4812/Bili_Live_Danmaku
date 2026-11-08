# -*- coding: utf-8 -*-
"""Single-worker scheduler that coalesces bursts of background refresh requests."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable


class CoalescingWorker:
    """Run one callback at a time and collapse concurrent requests into one follow-up run."""

    def __init__(self, callback: Callable[[], object], *, name: str) -> None:
        self._callback = callback
        self._name = name
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = False
        self._stopping = False
        self._logger = logging.getLogger(__name__)

    def request(self) -> bool:
        """Queue a refresh, starting the worker lazily; return False after shutdown begins."""
        with self._lock:
            if self._stopping:
                return False
            self._pending = True
            if self._thread is not None and self._thread.is_alive():
                return True

            worker = threading.Thread(
                target=self._run,
                name=self._name,
                daemon=True,
            )
            self._thread = worker
            try:
                worker.start()
            except Exception:
                self._thread = None
                self._pending = False
                raise
            return True

    def _run(self) -> None:
        current = threading.current_thread()
        while True:
            with self._lock:
                if self._stopping or not self._pending:
                    if self._thread is current:
                        self._thread = None
                    return
                self._pending = False

            try:
                self._callback()
            except Exception:
                self._logger.exception("后台合并任务执行失败：%s", self._name)

    def stop(self, timeout: float | None = None) -> bool:
        """Reject future requests and wait at most timeout seconds for the active callback."""
        with self._lock:
            self._stopping = True
            self._pending = False
            worker = self._thread

        if worker is None or worker is threading.current_thread():
            return True
        worker.join(timeout=max(0.0, timeout) if timeout is not None else None)
        return not worker.is_alive()
