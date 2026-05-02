from unittest.mock import MagicMock, patch

import pytest

from epub_corrector.summary import EpubSummary, _count_words, summarize_epub


class TestCountWords:
    def test_empty(self):
        assert _count_words("") == 0

    def test_simple(self):
        assert _count_words("Hello world") == 2

    def test_multiple_spaces(self):
        assert _count_words("Hello   world") == 2

    def test_newlines(self):
        assert _count_words("Hello\nworld\nfoo") == 3

    def test_punctuation(self):
        assert _count_words("Hello, world! How are you?") == 5


class TestEpubSummaryFormat:
    def test_format_short(self):
        summary = EpubSummary(
            chapter_count=5,
            total_words=1000,
            total_chars=5500,
            avg_words_per_chapter=200.0,
            estimated_pages=4,
            estimated_reading_time_minutes=4.44,
        )
        text = summary.format()
        assert "Chapters: 5" in text
        assert "Total words: 1,000" in text
        assert "Total characters: 5,500" in text
        assert "Average words per chapter: 200.0" in text
        assert "Estimated pages: 4" in text
        assert "Estimated reading time: 4m" in text

    def test_format_long(self):
        summary = EpubSummary(
            chapter_count=10,
            total_words=45000,
            total_chars=250000,
            avg_words_per_chapter=4500.0,
            estimated_pages=164,
            estimated_reading_time_minutes=200.0,
        )
        text = summary.format()
        assert "Estimated reading time: 3h 20m" in text

    def test_format_zero_chapters(self):
        summary = EpubSummary(
            chapter_count=0,
            total_words=0,
            total_chars=0,
            avg_words_per_chapter=0.0,
            estimated_pages=0,
            estimated_reading_time_minutes=0.0,
        )
        text = summary.format()
        assert "Chapters: 0" in text
        assert "Estimated reading time: 0m" in text


@patch("epub_corrector.summary.epub.read_epub")
@patch("epub_corrector.summary.iter_document_items")
@patch("epub_corrector.summary.extract_segment_texts")
def test_summarize_epub(mock_extract, mock_iter, mock_read):
    book = MagicMock()
    mock_read.return_value = book

    item1 = MagicMock()
    item2 = MagicMock()
    mock_iter.return_value = [item1, item2]

    def fake_extract(item):
        if item is item1:
            return ["Hello world", "Foo bar baz"]
        return ["Another chapter here"]

    mock_extract.side_effect = fake_extract

    result = summarize_epub("book.epub")

    assert isinstance(result, EpubSummary)
    assert result.chapter_count == 2
    assert result.total_words == 8  # 2 + 3 + 3
    assert result.total_chars == len("Hello world") + len("Foo bar baz") + len("Another chapter here")
    assert result.avg_words_per_chapter == 4.0
    assert result.estimated_pages == round(8 / 275)
    assert result.estimated_reading_time_minutes == 8 / 225


@patch("epub_corrector.summary.epub.read_epub")
@patch("epub_corrector.summary.iter_document_items")
@patch("epub_corrector.summary.extract_segment_texts")
def test_summarize_epub_empty(mock_extract, mock_iter, mock_read):
    book = MagicMock()
    mock_read.return_value = book
    mock_iter.return_value = []
    mock_extract.return_value = []

    result = summarize_epub("empty.epub")

    assert result.chapter_count == 0
    assert result.total_words == 0
    assert result.total_chars == 0
    assert result.avg_words_per_chapter == 0.0
    assert result.estimated_pages == 0
    assert result.estimated_reading_time_minutes == 0.0
