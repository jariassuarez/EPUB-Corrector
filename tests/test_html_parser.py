from bs4 import BeautifulSoup

from epub_corrector.html_parser import (
    SKIP_PARENT_TAGS,
    extract_segment_texts,
    iter_rewritable_segments,
    split_large_group,
)
from epub_corrector.types import SegmentRef


class _FakeNode:
    pass


def test_skip_script_tags():
    soup = BeautifulSoup(
        "<html><script>alert('hi')</script><body>Hello</body></html>", "xml"
    )
    segments = iter_rewritable_segments(soup)
    texts = [s.original_text for s in segments]
    assert "alert('hi')" not in texts
    assert "Hello" in texts


def test_skip_short_text():
    soup = BeautifulSoup("<html><body>x</body></html>", "xml")
    segments = iter_rewritable_segments(soup)
    assert len(segments) == 0


def test_skip_no_letters():
    soup = BeautifulSoup("<html><body>123 456</body></html>", "xml")
    segments = iter_rewritable_segments(soup)
    assert len(segments) == 0


def test_keep_long_alpha_text():
    soup = BeautifulSoup("<html><body>Hello world</body></html>", "xml")
    segments = iter_rewritable_segments(soup)
    assert len(segments) == 1
    assert segments[0].original_text == "Hello world"


def test_skip_parent_tags_coverage():
    for tag in SKIP_PARENT_TAGS:
        soup = BeautifulSoup(f"<html><body><{tag}>Some text here</{tag}></body></html>", "xml")
        segments = iter_rewritable_segments(soup)
        assert len(segments) == 0, f"Expected 0 segments for <{tag}>"


def test_split_large_group_by_count():
    group = [SegmentRef(node=_FakeNode(), original_text="a" * 100) for _ in range(5)]
    result = split_large_group(group, max_segments=2, max_chars=10000)
    assert len(result) == 3
    assert len(result[0]) == 2
    assert len(result[1]) == 2
    assert len(result[2]) == 1


def test_split_large_group_by_chars():
    group = [SegmentRef(node=_FakeNode(), original_text="a" * 100) for _ in range(5)]
    result = split_large_group(group, max_segments=10, max_chars=250)
    # Each segment is 100 chars, so 2 segments = 200 chars (fits), 3 = 300 (exceeds 250)
    # So groups should be [2, 2, 1]
    assert len(result) == 3
    assert len(result[0]) == 2
    assert len(result[1]) == 2
    assert len(result[2]) == 1


def test_extract_segment_texts():
    class FakeItem:
        def get_content(self):
            return b"<html><body><p>Hello</p><p>world</p></body></html>"

    texts = extract_segment_texts(FakeItem())
    assert texts == ["Hello", "world"]
