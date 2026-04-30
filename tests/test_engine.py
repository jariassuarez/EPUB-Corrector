from unittest.mock import MagicMock, patch

import pytest

from epub_corrector.config import CorrectionConfig
from epub_corrector.engine import BookProcessor, DocumentProcessor
from epub_corrector.types import ProcessingStats, ReviewState, StopProcessing


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


def test_document_processor_manual_review_reject():
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
    review = ReviewState(auto_accept=False)
    callback = MagicMock()
    callback.ask.return_value = "reject"
    records = []
    dp.process(item, "test.xhtml", stats, records, review, callback)

    assert stats.accepted_changes == 0
    assert stats.rejected_changes == 1
    assert len(records) == 1
    assert records[0].accepted is False


def test_document_processor_manual_review_accept_all():
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
    review = ReviewState(auto_accept=False)
    callback = MagicMock()
    callback.ask.return_value = "accept_all"
    dp.process(item, "test.xhtml", stats, None, review, callback)

    assert stats.accepted_changes == 1
    assert review.auto_accept is True


def test_document_processor_manual_review_retry():
    mock_llm = MagicMock()
    mock_llm.request_corrections.side_effect = [["Hello world"], ["Hello world"]]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello wrld</p></body></html>"

    stats = ProcessingStats()
    review = ReviewState(auto_accept=False)
    callback = MagicMock()
    callback.ask.side_effect = ["retry", "accept"]
    dp.process(item, "test.xhtml", stats, None, review, callback)

    assert stats.accepted_changes == 1
    assert mock_llm.request_corrections.call_count == 2


def test_document_processor_stop_auto_accept_via_poll():
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
    callback = MagicMock()
    callback.poll.return_value = "stop_auto_accept"
    dp.process(item, "test.xhtml", stats, None, review, callback)

    assert review.auto_accept is False


def test_document_processor_should_stop_raises():
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello world</p></body></html>"

    stats = ProcessingStats()
    with pytest.raises(StopProcessing):
        dp.process(item, "test.xhtml", stats, None, ReviewState(), None, should_stop=lambda: True)


def test_document_processor_should_stop_after_request():
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
    stop_calls = [False, True]
    call_iter = iter(stop_calls)

    with pytest.raises(StopProcessing):
        dp.process(item, "test.xhtml", stats, None, ReviewState(), None, should_stop=lambda: next(call_iter))


def test_document_processor_context_extension():
    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hello world"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
        max_context=2,
    )
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello wrld</p><p>Good bye</p></body></html>"

    stats = ProcessingStats()
    result = dp.process(item, "test.xhtml", stats, None, ReviewState(auto_accept=True), None)
    assert result == ["Hello wrld", "Good bye"]


def test_document_processor_no_change_identical():
    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hello world"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    dp = DocumentProcessor(mock_llm, cfg)

    item = MagicMock()
    item.get_content.return_value = b"<html><body><p>Hello world</p></body></html>"

    stats = ProcessingStats()
    dp.process(item, "test.xhtml", stats, None, ReviewState(auto_accept=True), None)
    assert stats.accepted_changes == 0


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


@patch("epub_corrector.engine.epub.read_epub")
@patch("epub_corrector.engine.epub.write_epub")
def test_book_processor_from_to_docs(mock_write, mock_read):
    from ebooklib.epub import EpubHtml

    item1 = MagicMock(spec=EpubHtml)
    item1.file_name = "doc1.xhtml"
    item1.id = "id1"
    item1.get_content.return_value = b"<html><body><p>Hello</p></body></html>"
    item2 = MagicMock(spec=EpubHtml)
    item2.file_name = "doc2.xhtml"
    item2.id = "id2"
    item2.get_content.return_value = b"<html><body><p>World</p></body></html>"

    book = MagicMock()
    book.spine = [("id1", True), ("id2", True)]
    book.get_items_of_type.return_value = [item1, item2]
    mock_read.return_value = book

    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hi"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    bp = BookProcessor(mock_llm, cfg)

    stats = bp.process_book("input.epub", "output.epub", from_doc=1, to_doc=1)
    assert stats.docs_seen == 1


@patch("epub_corrector.engine.epub.read_epub")
@patch("epub_corrector.engine.epub.write_epub")
@patch("epub_corrector.engine.write_csv_report")
def test_book_processor_report(mock_write_csv, mock_write, mock_read):
    item1 = MagicMock()
    item1.file_name = "doc1.xhtml"
    item1.get_content.return_value = b"<html><body><p>Hello</p></body></html>"

    book = MagicMock()
    book.spine = [("id1", True)]
    book.get_items_of_type.return_value = [item1]
    mock_read.return_value = book

    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hi"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    bp = BookProcessor(mock_llm, cfg)

    stats = bp.process_book("input.epub", "output.epub", report_path="report.csv")
    mock_write_csv.assert_called_once()


@patch("epub_corrector.engine.epub.read_epub")
@patch("epub_corrector.engine.epub.write_epub")
def test_book_processor_checkpoint_conserve_context(mock_write, mock_read):

    item1 = MagicMock()
    item1.file_name = "doc1.xhtml"
    item1.get_content.return_value = b"<html><body><p>Hello</p></body></html>"

    book = MagicMock()
    book.spine = [("id1", True)]
    book.get_items_of_type.return_value = [item1]
    mock_read.return_value = book

    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    with patch("epub_corrector.engine.load_checkpoint") as mock_load:
        mock_load.return_value = {"doc1": "YWJj"}  # base64 of "abc"
        with patch("epub_corrector.engine.base64.b64decode", return_value=b"abc"):
            stats = bp.process_book(
                "input.epub",
                "output.epub",
                checkpoint_path="chk.json",
                conserve_context=True,
            )
            assert stats.docs_seen == 0


@patch("epub_corrector.engine.epub.read_epub")
def test_book_processor_stop_processing(mock_read):
    from ebooklib.epub import EpubHtml

    item1 = MagicMock(spec=EpubHtml)
    item1.file_name = "doc1.xhtml"
    item1.id = "id1"
    item1.get_content.return_value = b"<html><body><p>Hello</p></body></html>"

    book = MagicMock()
    book.spine = [("id1", True)]
    book.get_items_of_type.return_value = [item1]
    mock_read.return_value = book

    mock_llm = MagicMock()
    mock_llm.request_corrections.return_value = ["Hi"]
    cfg = CorrectionConfig(
        similarity_threshold=0.0,
        max_change_ratio=1.0,
    )
    bp = BookProcessor(mock_llm, cfg)

    with pytest.raises(StopProcessing):
        bp.process_book("input.epub", "output.epub", should_stop=lambda: True)


@patch("epub_corrector.engine.os.path.isdir")
def test_process_batch_folder_not_found(mock_isdir):
    mock_isdir.return_value = False
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    with pytest.raises(ValueError, match="Batch folder not found"):
        bp.process_batch("/nonexistent")


@patch("epub_corrector.engine.os.path.isdir")
@patch("epub_corrector.engine.os.listdir")
def test_process_batch_no_epubs(mock_listdir, mock_isdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["not_an_epub.txt"]
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    with pytest.raises(ValueError, match="No EPUB files found"):
        bp.process_batch("/some/folder")


@patch("epub_corrector.engine.os.path.isdir")
@patch("epub_corrector.engine.os.listdir")
@patch("epub_corrector.engine.BookProcessor.process_book")
def test_process_batch_success(mock_process_book, mock_listdir, mock_isdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["book1.epub"]
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    successes, failures = bp.process_batch("/some/folder")
    assert successes == ["book1.epub"]
    assert failures == []


@patch("epub_corrector.engine.os.path.isdir")
@patch("epub_corrector.engine.os.listdir")
@patch("epub_corrector.engine.BookProcessor.process_book")
def test_process_batch_failure(mock_process_book, mock_listdir, mock_isdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["book1.epub"]
    mock_process_book.side_effect = RuntimeError("boom")
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    successes, failures = bp.process_batch("/some/folder")
    assert successes == []
    assert len(failures) == 1
    assert failures[0][0] == "book1.epub"


@patch("epub_corrector.engine.os.path.isdir")
@patch("epub_corrector.engine.os.listdir")
@patch("epub_corrector.engine.BookProcessor.process_book")
def test_process_batch_stop_processing(mock_process_book, mock_listdir, mock_isdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["book1.epub", "book2.epub"]
    mock_process_book.side_effect = StopProcessing()
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    successes, failures = bp.process_batch("/some/folder")
    assert successes == []
    assert failures == []


@patch("epub_corrector.engine.os.path.isdir")
@patch("epub_corrector.engine.os.listdir")
@patch("epub_corrector.engine.BookProcessor.process_book")
def test_process_batch_should_stop(mock_process_book, mock_listdir, mock_isdir):
    mock_isdir.return_value = True
    mock_listdir.return_value = ["book1.epub", "book2.epub"]
    mock_llm = MagicMock()
    cfg = CorrectionConfig()
    bp = BookProcessor(mock_llm, cfg)

    stop_calls = [False, True]
    call_iter = iter(stop_calls)
    successes, failures = bp.process_batch("/some/folder", should_stop=lambda: next(call_iter))
    assert successes == ["book1.epub"]
    assert failures == []
