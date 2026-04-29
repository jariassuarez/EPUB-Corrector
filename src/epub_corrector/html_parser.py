from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

from .types import SegmentRef

SKIP_PARENT_TAGS = {
    "script", "style", "code", "pre", "kbd", "samp",
    "head", "title", "meta", "link", "noscript", "nav",
    "header", "footer", "aside", "address",
}


def iter_rewritable_segments(soup: BeautifulSoup) -> list[SegmentRef]:
    segments: list[SegmentRef] = []
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if not isinstance(parent, Tag):
            continue
        if parent.name in SKIP_PARENT_TAGS:
            continue
        raw = str(node)
        if len(raw.strip()) < 2:
            continue
        if not any(ch.isalpha() for ch in raw):
            continue
        segments.append(SegmentRef(node=node, original_text=raw))
    return segments


def split_large_group(
    group: list[SegmentRef], max_segments: int, max_chars: int
) -> list[list[SegmentRef]]:
    result: list[list[SegmentRef]] = []
    current: list[SegmentRef] = []
    current_chars = 0
    for segment in group:
        seg_len = len(segment.original_text)
        would_exceed = len(current) >= max_segments or current_chars + seg_len > max_chars
        if current and would_exceed:
            result.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += seg_len
    if current:
        result.append(current)
    return result


def extract_segment_texts(item) -> list[str]:
    soup = BeautifulSoup(item.get_content(), "xml")
    segments = iter_rewritable_segments(soup)
    return [s.original_text for s in segments]
