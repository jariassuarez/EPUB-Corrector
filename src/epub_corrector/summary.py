from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ebooklib import epub

from epub_corrector.i18n import _

from .epub_io import iter_document_items
from .html_parser import extract_segment_texts

if TYPE_CHECKING:
    from collections.abc import Iterable


_WORDS_PER_PAGE = 275
_WORDS_PER_MINUTE = 225


@dataclass
class EpubSummary:
    """Statistics summary for an EPUB book."""

    chapter_count: int
    total_words: int
    total_chars: int
    avg_words_per_chapter: float
    estimated_pages: int
    estimated_reading_time_minutes: float

    def format(self) -> str:
        """Return a human-readable summary string."""
        hours = int(self.estimated_reading_time_minutes // 60)
        mins = int(self.estimated_reading_time_minutes % 60)
        time_str = _("{}h {}m").format(hours, mins) if hours else _("{}m").format(mins)

        lines = [
            _("Chapters: {}").format(self.chapter_count),
            _("Total words: {}").format(f"{self.total_words:,}"),
            _("Total characters: {}").format(f"{self.total_chars:,}"),
            _("Average words per chapter: {}").format(f"{self.avg_words_per_chapter:,.1f}"),
            _("Estimated pages: {}").format(f"{self.estimated_pages:,}"),
            _("Estimated reading time: {}").format(time_str),
        ]
        return "\n".join(lines)


def _count_words(text: str) -> int:
    """Count words in a text string."""
    # Split on whitespace and filter out empty strings
    return len(text.split())


def summarize_epub(path: str) -> EpubSummary:
    """Analyze an EPUB file and return summary statistics."""
    book = epub.read_epub(path)

    chapter_count = 0
    total_words = 0
    total_chars = 0

    for item in iter_document_items(book):
        chapter_count += 1
        texts = extract_segment_texts(item)
        for text in texts:
            total_chars += len(text)
            total_words += _count_words(text)

    avg_words_per_chapter = total_words / chapter_count if chapter_count else 0.0
    estimated_pages = round(total_words / _WORDS_PER_PAGE) if total_words else 0
    estimated_reading_time_minutes = total_words / _WORDS_PER_MINUTE if total_words else 0.0

    return EpubSummary(
        chapter_count=chapter_count,
        total_words=total_words,
        total_chars=total_chars,
        avg_words_per_chapter=avg_words_per_chapter,
        estimated_pages=estimated_pages,
        estimated_reading_time_minutes=estimated_reading_time_minutes,
    )
