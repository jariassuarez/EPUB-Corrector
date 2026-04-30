from __future__ import annotations

import contextlib
import logging
import queue
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class GuiLogHandler(logging.Handler):
    """Logging handler that queues records for a GUI log viewer."""

    def __init__(self) -> None:
        super().__init__()
        self._record_queue: queue.Queue[logging.LogRecord] = queue.Queue()

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(queue.Full):
            self._record_queue.put_nowait(record)

    def get_records(self) -> list[logging.LogRecord]:
        """Drain all pending records from the queue."""
        records = []
        while True:
            try:
                records.append(self._record_queue.get_nowait())
            except queue.Empty:
                break
        return records

    def clear(self) -> None:
        """Drain the queue without returning records."""
        self.get_records()


class TeeStream:
    """Wraps a stream and additionally sends writes to a callback."""

    def __init__(self, original: Any, callback: Callable[[str], None]) -> None:
        self._original = original
        self._callback = callback

    def write(self, text: str) -> None:
        self._original.write(text)
        if text:
            self._callback(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def install_tee_stream(callback: Callable[[str], None]) -> TeeStream:
    """Wrap sys.stdout with a TeeStream and return it."""
    tee = TeeStream(sys.stdout, callback)
    sys.stdout = tee
    return tee
