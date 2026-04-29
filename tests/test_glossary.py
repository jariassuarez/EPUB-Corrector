import json
import os
import tempfile

from epub_corrector.glossary import format_glossary_injection, load_glossary


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
