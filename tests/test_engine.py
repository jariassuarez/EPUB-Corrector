from unittest.mock import MagicMock, patch

from epub_corrector.config import CorrectionConfig
from epub_corrector.engine import BookProcessor, DocumentProcessor
from epub_corrector.types import ProcessingStats, ReviewState


def test_document_processor_no_segments():
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body>   </body></html>"

    stats = ProcessingStats()
    result = dp.process(item, "test.xhtml", stats, None, ReviewState(), None)
    assert result == []
    assert stats.docs_seen == 0
    mock_llm.request_corrections.assert_not_called()


def test_document_processor_applies_changes_auto_accept():
    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hello world"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello wrld</p></body></html>"

    stats = ProcessingStats()
    review = ReviewState(auto_accept=True)
    dp.process(item, "test.xhtml", stats, None, review, None)

    assert stats.docs_seen == 1
    assert stats.accepted_changes == 1
    assert stats.rejected_changes == 0
    item.set_content.assert_called_once()


def test_document_processor_rejects_unsafe_change():
    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["completely different text here"]
    cfg = CorrectionConfig(
        similarity_threshold=0.99,
        max_change_ratio=0.01,
    )
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello world</p></body></html>"

    stats = ProcessingStats()
    review = ReviewState(auto_accept=True)
    dp.process(item, "test.xhtml", stats, None, review, None)

    assert stats.docs_seen == 1
    assert stats.accepted_changes == 0
    assert stats.rejected_changes == 1


@patch("epub_corrector.engine.epub.read_epub")
@patch("epub_corrector.engine.epub.write_epub")
def test_book_processor_empty_book(mock_write, mock_read):
    book = MagicMock()
    book.spine = []
    book.get_items_of_type.return_value = []
    mock_read.return_value = book

    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    stats = bp.process_book("input.epub", "output.epub")
    assert stats.docs_seen == 0
    mock_read.assert_called_once_with("input.epub")
    mock_write.assert_called()


@patch("epub_corrector.engine.epub.read_epub")
@patch("epub_corrector.engine.epub.write_epub")
def test_book_processor_with_checkpoint(mock_write, mock_read):
    book = MagicMock()
    book.spine = []
    book.get_items_of_type.return_value = []
    mock_read.return_value = book

    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    with patch("epub_corrector.engine.load_checkpoint") as mock_load:
        mock_load.return_value = {"doc1": "abc"}
        stats = bp.process_book("input.epub", "output.epub", checkpoint_path="chk.json")
        mock_load.assert_called_once_with("chk.json")
        assert stats.docs_seen == 0
