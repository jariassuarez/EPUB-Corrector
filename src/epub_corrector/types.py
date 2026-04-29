from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bs4 import NavigableString


class StopProcessing(Exception):
    """Raised when the user requests to stop processing."""


class ReviewCallback(Protocol):
    def ask(self, original: str, proposed: str, doc_name: str) -> str:
        """Return 'accept', 'reject', 'accept_all', or 'retry'."""
        ...

    def poll(self) -> str | None:
        """Check if user wants to stop auto-accept without blocking."""
        ...


@dataclass
class SegmentRef:
    node: NavigableString
    original_text: str


@dataclass
class ProcessingStats:
    docs_seen: int = 0
    groups_seen: int = 0
    segments_seen: int = 0
    accepted_changes: int = 0
    rejected_changes: int = 0
    failed_groups: int = 0


@dataclass
class ChangeRecord:
    doc_name: str
    original: str
    proposed: str
    accepted: bool


@dataclass
class ReviewState:
    auto_accept: bool = False
