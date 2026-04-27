from __future__ import annotations

import json
import logging
import re
from typing import Callable

from openai import OpenAI

from .core import _iter_document_items, _iter_rewritable_segments

_GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {
        "names":         {"type": "array", "items": {"type": "string"}},
        "places":        {"type": "array", "items": {"type": "string"}},
        "organizations": {"type": "array", "items": {"type": "string"}},
        "terms":         {"type": "array", "items": {"type": "string"}},
        "other":         {"type": "array", "items": {"type": "string"}},
    },
    "required": ["names", "places", "organizations", "terms", "other"],
    "additionalProperties": False,
}

_GLOSSARY_SYSTEM_PROMPT = (
    "You are a literary analyst. Extract a structured glossary of proper nouns, "
    "nicknames, and special terms from the provided text.\n\n"
    "Categorize into:\n"
    "- names: character names, personal names, nicknames for people\n"
    "- places: locations, cities, buildings, regions, realms — including invented or informal names and epithets (e.g. 'Dying Realm', 'the Shattered Keep')\n"
    "- organizations: factions, guilds, groups, institutions, governments\n"
    "- terms: special vocabulary, magic systems, invented words, technical jargon\n"
    "- other: other important and recurrent terms that don't fit the above categories, including capitalization. No random terms\n\n"
    "Rules:\n"
    "- Only include terms that appear explicitly in the provided text\n"
    "- Capture the exact canonical form and capitalization as it appears in the text\n"
    "- Include informal nicknames and epithets that have specific capitalization\n"
    "- Exclude common English words, generic nouns, and pronouns\n\n"
    "Respond with ONLY a JSON object with exactly these five keys: "
    "\"names\", \"places\", \"organizations\", \"terms\", \"other\". "
    "Each value is a JSON array of strings. No commentary, no markdown fences."
)

_GLOSSARY_SUMMARY_SYSTEM_PROMPT = (
    "You are cleaning up a glossary extracted from an EPUB novel. "
    "The glossary may contain noise introduced by automated extraction.\n\n"
    "Remove entries that are clearly meaningless or noise:\n"
    "- Website navigation artifacts (e.g. '< report chapter >')\n"
    "- Raw HTML/format strings (e.g. 'html', 'html5')\n"
    "- Generic sequential numbers used as level labels (e.g. 'Level 0', 'Level 1') "
    "unless they refer to a specific named tier in the story\n"
    "- Real-world brand/product names that are clearly out of place in the story world "
    "(e.g. 'iPhone 12')\n"
    "- Stat dump strings combining multiple attributes (e.g. 'Strength 5, Agility 3, Endurance 4') "
    "— keep individual stat names that appear elsewhere\n"
    "- Exact duplicates: if an entry appears in multiple categories keep it only in the most specific one\n\n"
    "Keep all character names, place names, organization names, unique skill/item/mechanic "
    "names, and any capitalized terms that would need to be preserved for consistency in "
    "translation or editing.\n\n"
    "Return the same JSON structure with cleaned arrays. No commentary, no markdown fences."
)

_GLOSSARY_INJECTION_TEMPLATE = (
    "\n\nThe following terms appear in this text and must be preserved exactly as written "
    "(including capitalization and spelling):\n{entries}"
)


def _parse_glossary_response(content: str) -> dict[str, list[str]]:
    text = content.strip()
    fenced = re.search(r"^```(?:\w+)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def _make_glossary_kwargs(no_thinking: bool, schema_name: str = "glossary_extraction") -> dict:
    kwargs: dict = {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": _GLOSSARY_SCHEMA,
            },
        }
    }
    if no_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return kwargs


def format_glossary_injection(glossary: dict[str, list[str]]) -> str:
    label_map = {
        "names": "Names",
        "places": "Places",
        "organizations": "Organizations",
        "terms": "Terms",
        "other": "Other",
    }
    lines = [
        f"{label}: {', '.join(glossary.get(key, []))}"
        for key, label in label_map.items()
        if glossary.get(key)
    ]
    if not lines:
        return ""
    return _GLOSSARY_INJECTION_TEMPLATE.format(entries="\n".join(lines))


def load_glossary(path: str) -> dict[str, list[str]]:
    """Load a glossary JSON file produced by extract_glossary(). Returns empty dict on error."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        result: dict[str, list[str]] = {}
        for key in ("names", "places", "organizations", "terms", "other"):
            val = data.get(key, [])
            result[key] = [str(e) for e in val if e] if isinstance(val, list) else []
        return result
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logging.warning("Failed to load glossary from %r: %s", path, exc)
        return {}


def extract_glossary(
    input_path: str,
    client: OpenAI,
    model: str,
    temperature: float,
    context_length: int = 20000,
    no_thinking: bool = False,
    debug: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, list[str]]:
    """
    Scan all spine documents of an EPUB and build a glossary of proper nouns,
    nicknames, and special terms by sending text chunks to the LLM.
    Returns a dict with keys: names, places, organizations, terms, other.
    """
    from ebooklib import epub

    book = epub.read_epub(input_path)

    chunks: list[str] = []
    chunk_ranges: list[tuple[int, int]] = []
    current_parts: list[str] = []
    current_chars = 0
    current_first_chapter = 1
    chapter_idx = 0

    for item in _iter_document_items(book):
        chapter_idx += 1
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(item.get_content(), "xml")
        for seg in _iter_rewritable_segments(soup):
            text = seg.original_text
            if current_chars + len(text) > context_length and current_parts:
                chunks.append("\n".join(current_parts))
                chunk_ranges.append((current_first_chapter, chapter_idx - 1))
                current_parts = []
                current_chars = 0
                current_first_chapter = chapter_idx
            current_parts.append(text)
            current_chars += len(text)

    if current_parts:
        chunks.append("\n".join(current_parts))
        chunk_ranges.append((current_first_chapter, chapter_idx))

    merged: dict[str, list[str]] = {
        "names": [], "places": [], "organizations": [], "terms": [], "other": []
    }

    kwargs = _make_glossary_kwargs(no_thinking)
    total = len(chunks)
    try:
        for idx, (chunk, (ch_first, ch_last)) in enumerate(zip(chunks, chunk_ranges), start=1):
            if should_stop and should_stop():
                break
            print(f"Processing chapter {ch_first} to chapter {ch_last} ({idx}/{total})...", flush=True)

            system = _GLOSSARY_SYSTEM_PROMPT
            if no_thinking:
                system = "/no_think\n\n" + system

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": chunk},
            ]

            if debug:
                print("\n--- GLOSSARY REQUEST ---")
                print(json.dumps(messages, ensure_ascii=False, indent=2))
                print("--- END REQUEST ---\n")

            try:
                response = client.chat.completions.create(
                    model=model,
                    temperature=temperature,
                    messages=messages,
                    **kwargs,
                )
                content = response.choices[0].message.content or ""
                if debug:
                    print(f"\n--- GLOSSARY RESPONSE ({idx}/{total}) ---")
                    print(content)
                    print("--- END RESPONSE ---\n")

                data = _parse_glossary_response(content)
                for key in merged:
                    entries = data.get(key, [])
                    if isinstance(entries, list):
                        merged[key].extend(str(e) for e in entries if e)
            except json.JSONDecodeError as exc:
                logging.warning("Failed to parse glossary response for chunk %d: %s", idx, exc)
            except Exception as exc:
                logging.warning("Model request failed for glossary chunk %d: %s", idx, exc)
    except KeyboardInterrupt:
        print(f"\nInterrupted — saving partial glossary ({idx - 1}/{total} chunks processed).", flush=True)

    return {key: sorted(set(vals)) for key, vals in merged.items()}


def summarize_glossary(
    glossary: dict[str, list[str]],
    client: OpenAI,
    model: str,
    temperature: float,
    no_thinking: bool = False,
    debug: bool = False,
) -> dict[str, list[str]]:
    """
    Send a glossary to the LLM and ask it to remove clearly meaningless entries.
    Returns a cleaned glossary dict with the same structure.
    """
    system = _GLOSSARY_SUMMARY_SYSTEM_PROMPT
    if no_thinking:
        system = "/no_think\n\n" + system

    payload = json.dumps(glossary, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": payload},
    ]

    if debug:
        print("\n--- SUMMARIZE GLOSSARY REQUEST ---")
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        print("--- END REQUEST ---\n")

    kwargs = _make_glossary_kwargs(no_thinking, schema_name="glossary_summary")
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=messages,
        **kwargs,
    )
    content = response.choices[0].message.content or ""

    if debug:
        print("\n--- SUMMARIZE GLOSSARY RESPONSE ---")
        print(content)
        print("--- END RESPONSE ---\n")

    data = _parse_glossary_response(content)
    result: dict[str, list[str]] = {}
    for key in ("names", "places", "organizations", "terms", "other"):
        entries = data.get(key, [])
        result[key] = sorted(set(str(e) for e in entries if e)) if isinstance(entries, list) else []
    return result
