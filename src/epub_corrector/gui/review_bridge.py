from __future__ import annotations

import queue
from typing import TYPE_CHECKING, Any

from epub_corrector.types import ReviewCallback, StopProcessing

if TYPE_CHECKING:
    import threading


class GuiReview(ReviewCallback):
    """Thread-safe review callback using queues."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.request_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.response_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = stop_event

    def ask(self, original: str, proposed: str, doc_name: str) -> str:
        self.request_queue.put(
            {
                "original": original,
                "proposed": proposed,
                "doc_name": doc_name,
            }
        )
        while True:
            try:
                return self.response_queue.get(timeout=0.2)
            except queue.Empty:
                if self.stop_event.is_set():
                    raise StopProcessing() from None

    def poll(self) -> str | None:
        """No-op in GUI mode; stopping is handled via stop_event."""
        return None
