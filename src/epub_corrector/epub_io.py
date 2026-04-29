from __future__ import annotations

from typing import TYPE_CHECKING

from ebooklib import ITEM_DOCUMENT
from ebooklib.epub import EpubBook, EpubHtml

if TYPE_CHECKING:
    from collections.abc import Iterable


def iter_document_items(book: EpubBook) -> Iterable:
    """Yield document items in spine order."""
    spine_ids = [idref for idref, _ in book.spine]
    id_to_item = {}
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        id_to_item[item.id] = item
    for idref in spine_ids:
        if idref in id_to_item:
            yield id_to_item[idref]


def reorder_items_by_spine(book: EpubBook) -> None:
    """Reorder book.items to match the spine sequence."""
    spine_order = {idref: idx for idx, (idref, _) in enumerate(book.spine)}
    html_items = [item for item in book.items if isinstance(item, EpubHtml)]
    html_items.sort(key=lambda item: spine_order.get(item.id, float("inf")))

    html_iter = iter(html_items)
    book.items = [next(html_iter) if isinstance(item, EpubHtml) else item for item in book.items]
