from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

if TYPE_CHECKING:
    import tkinter as tk

from epub_corrector.gui.review_bridge import GuiReview


class WorkerController:
    """Manages a single background worker thread and cross-thread communication."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.review = GuiReview(self._stop_event)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, target: Callable[[], None], *, on_done: Callable[[], None] | None = None) -> None:
        if self.is_running():
            return
        self._stop_event.clear()

        def wrapper() -> None:
            try:
                target()
            finally:
                if on_done:
                    self.root.after(0, on_done)

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def get_stop_check(self) -> Callable[[], bool]:
        return self._stop_event.is_set
