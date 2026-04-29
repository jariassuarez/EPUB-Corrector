from unittest.mock import MagicMock

from ebooklib.epub import EpubHtml

from epub_corrector.epub_io import iter_document_items, reorder_items_by_spine


def test_iter_document_items_follows_spine():
    book = MagicMock()
    item1 = MagicMock(spec=EpubHtml)
    item1.id = "id1"
    item2 = MagicMock(spec=EpubHtml)
    item2.id = "id2"
    item3 = MagicMock(spec=EpubHtml)
    item3.id = "id3"

    book.spine = [("id2", True), ("id1", True)]
    book.get_items_of_type.return_value = [item1, item2, item3]

    items = list(iter_document_items(book))
    ids = [i.id for i in items]
    assert ids == ["id2", "id1"]


def test_iter_document_items_skips_missing():
    book = MagicMock()
    item1 = MagicMock(spec=EpubHtml)
    item1.id = "id1"

    book.spine = [("missing", True), ("id1", True)]
    book.get_items_of_type.return_value = [item1]

    items = list(iter_document_items(book))
    assert len(items) == 1
    assert items[0].id == "id1"


def test_reorder_items_by_spine():
    book = MagicMock()
    item1 = MagicMock(spec=EpubHtml)
    item1.id = "id1"
    item2 = MagicMock(spec=EpubHtml)
    item2.id = "id2"
    item3 = MagicMock()  # not EpubHtml

    book.spine = [("id2", True), ("id1", True)]
    book.items = [item1, item3, item2]

    reorder_items_by_spine(book)
    assert book.items == [item2, item3, item1]
