import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from epub_corrector.glossary import (
    _make_glossary_kwargs,
    _parse_glossary_response,
    _request_glossary_with_retry,
    extract_glossary,
    format_glossary_injection,
    load_glossary,
    summarize_glossary,
)


def test_format_glossary_injection():
    glossary = {
        "names": ["Alice", "Bob"],
        "places": ["Wonderland"],
        "organizations": [],
        "terms": ["Magic Sword"],
        "other": [],
    }
    injection = format_glossary_injection(glossary)
    assert "Names: Alice, Bob" in injection
    assert "Places: Wonderland" in injection
    assert "Terms: Magic Sword" in injection
    assert "Organizations" not in injection


def test_format_glossary_injection_empty():
    assert format_glossary_injection({}) == ""
    assert format_glossary_injection({"names": [], "places": []}) == ""


def test_load_glossary_missing():
    assert load_glossary("/nonexistent/file.json") == {}


def test_load_glossary_valid():
    data = {
        "names": ["Alice"],
        "places": ["Wonderland"],
        "organizations": [],
        "terms": [],
        "other": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        result = load_glossary(path)
        assert result["names"] == ["Alice"]
        assert result["places"] == ["Wonderland"]
    finally:
        os.unlink(path)


def test_load_glossary_ignores_extra_keys():
    data = {
        "names": ["Alice"],
        "unexpected": ["value"],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        result = load_glossary(path)
        assert "names" in result
        assert "unexpected" not in result
    finally:
        os.unlink(path)


def test_load_glossary_filters_non_string_entries():
    data = {
        "names": ["Alice", "", None, 123],
        "places": [],
        "organizations": [],
        "terms": [],
        "other": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        result = load_glossary(path)
        assert result["names"] == ["Alice", "123"]
    finally:
        os.unlink(path)


def test_parse_glossary_response_plain():
    result = _parse_glossary_response('{"names": ["A"], "places": [], "organizations": [], "terms": [], "other": []}')
    assert result["names"] == ["A"]


def test_parse_glossary_response_fenced():
    result = _parse_glossary_response('```json\n{"names": ["A"], "places": [], "organizations": [], "terms": [], "other": []}\n```')
    assert result["names"] == ["A"]


def test_parse_glossary_response_non_object():
    with pytest.raises(ValueError, match="Expected JSON object"):
        _parse_glossary_response('["not", "an", "object"]')


def test_make_glossary_kwargs_default():
    kwargs = _make_glossary_kwargs(no_thinking=False)
    assert "response_format" in kwargs
    assert "extra_body" not in kwargs


def test_make_glossary_kwargs_no_thinking():
    kwargs = _make_glossary_kwargs(no_thinking=True)
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_request_glossary_with_retry_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"names": ["A"], "places": [], "organizations": [], "terms": [], "other": []}'
    mock_client.chat.completions.create.return_value = mock_response

    result = _request_glossary_with_retry(
        client=mock_client,
        model="test-model",
        temperature=0.0,
        messages=[{"role": "user", "content": "test"}],
        kwargs={},
        max_retries=1,
        debug=False,
    )
    assert "names" in result


def test_request_glossary_with_retry_all_fail():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not valid json"
    mock_client.chat.completions.create.return_value = mock_response

    with pytest.raises(RuntimeError, match="failed after"):
        _request_glossary_with_retry(
            client=mock_client,
            model="test-model",
            temperature=0.0,
            messages=[{"role": "user", "content": "test"}],
            kwargs={},
            max_retries=2,
            debug=False,
        )


@patch("ebooklib.epub.read_epub")
@patch("epub_corrector.glossary._request_glossary_with_retry")
def test_extract_glossary(mock_request, mock_read_epub):
    from ebooklib.epub import EpubHtml

    mock_book = MagicMock()
    mock_item = MagicMock(spec=EpubHtml)
    mock_item.get_content.return_value = b"<html><body><p>Hello Alice from Wonderland</p></body></html>"
    mock_item.id = "item1"
    mock_book.spine = [("item1", True)]
    mock_book.get_items_of_type.return_value = [mock_item]
    mock_read_epub.return_value = mock_book

    mock_request.return_value = '{"names": ["Alice"], "places": ["Wonderland"], "organizations": [], "terms": [], "other": []}'

    mock_client = MagicMock()
    result = extract_glossary(
        input_path="test.epub",
        client=mock_client,
        model="test-model",
        temperature=0.0,
        max_retries=1,
    )
    assert "Alice" in result["names"]
    assert "Wonderland" in result["places"]


@patch("ebooklib.epub.read_epub")
def test_extract_glossary_should_stop(mock_read_epub):
    from ebooklib.epub import EpubHtml

    mock_book = MagicMock()
    mock_item = MagicMock(spec=EpubHtml)
    mock_item.get_content.return_value = b"<html><body><p>Hello world</p></body></html>"
    mock_item.id = "item1"
    mock_book.spine = [("item1", True)]
    mock_book.get_items_of_type.return_value = [mock_item]
    mock_read_epub.return_value = mock_book

    mock_client = MagicMock()
    result = extract_glossary(
        input_path="test.epub",
        client=mock_client,
        model="test-model",
        temperature=0.0,
        should_stop=lambda: True,
        max_retries=1,
    )
    assert result == {"names": [], "places": [], "organizations": [], "terms": [], "other": []}


@patch("epub_corrector.glossary._request_glossary_with_retry")
def test_summarize_glossary(mock_request):
    mock_request.return_value = '{"names": ["Alice"], "places": [], "organizations": [], "terms": [], "other": []}'

    mock_client = MagicMock()
    result = summarize_glossary(
        glossary={
            "names": ["Alice", "Bob"],
            "places": [],
            "organizations": [],
            "terms": [],
            "other": [],
        },
        client=mock_client,
        model="test-model",
        temperature=0.0,
        max_retries=1,
    )
    assert result["names"] == ["Alice"]
