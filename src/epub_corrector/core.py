from __future__ import annotations

import base64
import csv
import difflib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import ITEM_DOCUMENT, epub
from openai import OpenAI

SKIP_PARENT_TAGS = {
    "script", "style", "code", "pre", "kbd", "samp",
    "head", "title", "meta", "link", "noscript", "nav",
    "header", "footer", "aside", "address",
}


class ReviewCallback(Protocol):
    def ask(self, original: str, proposed: str, doc_name: str) -> str:
        """Return 'accept', 'reject', 'accept_all', or 'retry'."""
        ...


@dataclass
class SegmentRef:
    node: NavigableString
    original_text: str


@dataclass
class ProcessingStats:
    docs_seen: int = 0
    groups_seen: int = 0
    segments_seen: int = 0
    accepted_changes: int = 0
    rejected_changes: int = 0
    failed_groups: int = 0


@dataclass
class ChangeRecord:
    doc_name: str
    original: str
    proposed: str
    accepted: bool


@dataclass
class ReviewState:
    auto_accept: bool = False


def _contains_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _iter_rewritable_segments(soup: BeautifulSoup) -> list[SegmentRef]:
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
        if not _contains_letters(raw):
            continue
        segments.append(SegmentRef(node=node, original_text=raw))
    return segments


def _split_large_group(
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


def _build_messages(texts: list[str], no_thinking: bool = False) -> list[dict[str, str]]:
    system = (
        "You are a strict grammar and spelling corrector. "
        "Correct only grammar, punctuation, capitalization, and obvious typos. "
        "Do not change formatting, markup, or whitespace. "
        "Adopt the tone of a professional fiction editor. Provide clean, flowing prose without bolding, and ensure the grammar follows standard novel-writing conventions."
        "Do not add or remove facts, style, tone, meaning, entities, numbers, chronology, or dialogue intent. "
        "Do not summarize. Do not paraphrase unless required for grammar. "
        "Return ONLY a JSON array of objects for segments that need changes. "
        "Each object must have \"i\" (0-based index) and \"t\" (corrected text). "
        "Omit unchanged segments. If nothing needs correction, return []."
    )
    if no_thinking:
        system = "/no_think\n\n" + system
    user_payload = {
        "segments": [{"i": i, "t": t} for i, t in enumerate(texts)],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


_SMART_PUNCT_MAP = str.maketrans({
    "\u201c": '"',   # left double quotation mark
    "\u201d": '"',   # right double quotation mark
    "\u2018": "'",   # left single quotation mark
    "\u2019": "'",   # right single quotation mark
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2026": "...", # horizontal ellipsis
})


# Characters to strip entirely (invisible/confusing)
_STRIP_CHARS = "\ufeff\u200b\u200c\u200d\u00ad"


def _normalize_json_text(text: str) -> str:
    """Normalize smart Unicode punctuation and escape literal newlines inside JSON strings."""
    # Remove BOM and other invisible characters
    for ch in _STRIP_CHARS:
        text = text.replace(ch, "")
    # Replace non-breaking spaces with regular spaces
    text = text.replace("\u00a0", " ")
    # Replace smart quotes, dashes, ellipsis with ASCII equivalents
    text = text.translate(_SMART_PUNCT_MAP)
    result: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escaped:
            result.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            result.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if in_string and ch in "\n\r":
            result.append("\\n" if ch == "\n" else "\\r")
            i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _extract_corrections(raw: str, originals: list[str]) -> list[str]:
    result = list(originals)
    candidates = [raw.strip()]
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    bracketed = re.search(r"(\[.*\])", raw, re.DOTALL)
    if bracketed:
        candidates.append(bracketed.group(1).strip())
    for candidate in candidates:
        if any(c in candidate for c in "\n\r\u201c\u201d\u2018\u2019"):
            candidate = _normalize_json_text(candidate)
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, list):
                continue
            if parsed == []:
                return result
            if all(
                isinstance(item, dict) and "i" in item and "t" in item
                for item in parsed
            ):
                for item in parsed:
                    idx = item["i"]
                    if isinstance(idx, int) and 0 <= idx < len(result) and isinstance(item["t"], str):
                        result[idx] = item["t"]
                return result
        except json.JSONDecodeError:
            continue
    raise ValueError("Could not parse a valid corrections response from model output")


def _request_corrections(
    client: OpenAI, model: str, texts: list[str], temperature: float,
    no_thinking: bool = False, debug: bool = False, max_retries: int = 3,
) -> list[str]:
    kwargs = {}
    if no_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": _build_messages(texts, no_thinking=no_thinking),
        **kwargs,
    }
    if debug:
        print("\n--- REQUEST PAYLOAD ---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("--- END REQUEST ---\n")

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=payload["messages"],
                **kwargs,
            )
            content = response.choices[0].message.content or ""
            if debug:
                print(f"\n--- MODEL RESPONSE (attempt {attempt}) ---")
                print(content)
                print("--- END RESPONSE ---\n")
            return _extract_corrections(content, originals=texts)
        except ValueError:
            logging.warning(
                "Failed to parse model response (attempt %d/%d). Raw content:\n%s",
                attempt, max_retries, content,
            )
            last_exc = ValueError(
                f"Could not parse model response after {max_retries} attempts. Last raw content:\n{content}"
            )
            if attempt < max_retries:
                print(f"    Parse failed, retrying... ({attempt}/{max_retries})")
                continue
            raise last_exc
        except Exception as exc:
            logging.warning("Model request failed (attempt %d/%d): %s", attempt, max_retries, exc)
            last_exc = exc
            if attempt < max_retries:
                print(f"    Request failed, retrying... ({attempt}/{max_retries})")
                continue
            raise last_exc


def _change_is_safe(
    original: str, proposed: str, similarity_threshold: float, max_change_ratio: float
) -> bool:
    if original == proposed:
        return True
    if original.strip() and not proposed.strip():
        return False
    similarity = difflib.SequenceMatcher(a=original, b=proposed).ratio()
    change_ratio = 1.0 - similarity
    if similarity < similarity_threshold:
        return False
    if change_ratio > max_change_ratio:
        return False
    return True


def _write_csv_report(records: list[ChangeRecord], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["document", "status", "original", "proposed"])
        for r in records:
            writer.writerow([
                r.doc_name,
                "accepted" if r.accepted else "rejected",
                r.original,
                r.proposed,
            ])


def _load_checkpoint(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("processed", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_checkpoint(path: str, processed: dict[str, str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"processed": processed}, f, ensure_ascii=False)
    os.replace(tmp, path)


def _reorder_items_by_spine(book) -> None:
    from ebooklib.epub import EpubHtml

    spine_order = {idref: idx for idx, (idref, _) in enumerate(book.spine)}
    html_items = [item for item in book.items if isinstance(item, EpubHtml)]
    html_items.sort(key=lambda item: spine_order.get(item.id, float("inf")))

    html_iter = iter(html_items)
    book.items = [
        next(html_iter) if isinstance(item, EpubHtml) else item
        for item in book.items
    ]


def _process_document(
    item,
    doc_name: str,
    client: OpenAI,
    model: str,
    temperature: float,
    max_segments_per_request: int,
    max_chars_per_request: int,
    similarity_threshold: float,
    max_change_ratio: float,
    stats: ProcessingStats,
    records: list[ChangeRecord] | None,
    review: ReviewState,
    review_callback: ReviewCallback | None,
    no_thinking: bool = False,
    debug: bool = False,
) -> None:
    raw = item.get_content()
    soup = BeautifulSoup(raw, "xml")

    segments = _iter_rewritable_segments(soup)
    if not segments:
        return

    stats.docs_seen += 1

    for batch_idx, batch in enumerate(_split_large_group(
        segments,
        max_segments=max_segments_per_request,
        max_chars=max_chars_per_request,
    ), start=1):
        stats.groups_seen += 1
        stats.segments_seen += len(batch)
        originals = [s.original_text for s in batch]
        print(f"  [{doc_name}] batch {batch_idx} ({len(batch)} segments, {sum(len(s) for s in originals)} chars)...", flush=True)
        try:
            corrected = _request_corrections(
                client=client, model=model, texts=originals, temperature=temperature,
                no_thinking=no_thinking, debug=debug,
            )
        except Exception as exc:
            stats.failed_groups += 1
            logging.warning("Model request failed for one batch: %s", exc)
            print(f"    FAILED: {exc}")
            continue

        seg_idx = 0
        while seg_idx < len(batch):
            segment = batch[seg_idx]
            new_text = corrected[seg_idx]

            if not _change_is_safe(
                original=segment.original_text,
                proposed=new_text,
                similarity_threshold=similarity_threshold,
                max_change_ratio=max_change_ratio,
            ):
                stats.rejected_changes += 1
                if records is not None:
                    records.append(ChangeRecord(
                        doc_name=doc_name,
                        original=segment.original_text,
                        proposed=new_text,
                        accepted=False,
                    ))
                seg_idx += 1
                continue

            if segment.original_text == new_text:
                seg_idx += 1
                continue

            if not review.auto_accept:
                if review_callback is not None:
                    action = review_callback.ask(segment.original_text, new_text, doc_name)
                else:
                    action = "accept"
                if action == "accept_all":
                    review.auto_accept = True
                    print("Auto-accepting all remaining changes.")
                elif action == "reject":
                    stats.rejected_changes += 1
                    if records is not None:
                        records.append(ChangeRecord(
                            doc_name=doc_name,
                            original=segment.original_text,
                            proposed=new_text,
                            accepted=False,
                        ))
                    seg_idx += 1
                    continue
                elif action == "retry":
                    print("  Retrying batch...")
                    try:
                        corrected = _request_corrections(
                            client=client, model=model, texts=originals, temperature=temperature,
                            no_thinking=no_thinking, debug=debug,
                        )
                    except Exception as exc:
                        stats.failed_groups += 1
                        logging.warning("Model request failed on retry: %s", exc)
                        print(f"    FAILED on retry: {exc}")
                        break
                    continue
            else:
                if review_callback is not None:
                    poll_action = getattr(review_callback, "poll", lambda: None)()
                    if poll_action == "stop_auto_accept":
                        review.auto_accept = False
                        print("Auto-accept paused. Resuming manual review.")
                        continue

            segment.node.replace_with(NavigableString(new_text))
            stats.accepted_changes += 1
            if records is not None:
                records.append(ChangeRecord(
                    doc_name=doc_name,
                    original=segment.original_text,
                    proposed=new_text,
                    accepted=True,
                ))
            seg_idx += 1

    item.set_content(str(soup).encode("utf-8"))


def _iter_document_items(book) -> Iterable:
    spine_ids = [idref for idref, _ in book.spine]
    id_to_item = {}
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        id_to_item[item.id] = item
    for idref in spine_ids:
        if idref in id_to_item:
            yield id_to_item[idref]


def _select_epub_from_books_folder() -> str:
    books_dir = os.path.join(os.getcwd(), "books")
    if not os.path.isdir(books_dir):
        raise SystemExit(
            f"No 'books' folder found at {books_dir!r}. Pass an input path explicitly."
        )
    epubs = sorted(f for f in os.listdir(books_dir) if f.lower().endswith(".epub"))
    if not epubs:
        raise SystemExit(
            f"No EPUB files found in {books_dir!r}. Pass an input path explicitly."
        )
    print("Available books:")
    for i, name in enumerate(epubs, 1):
        print(f"  {i}. {name}")
    while True:
        try:
            raw = input(f"Select a book [1-{len(epubs)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(epubs):
                return os.path.join(books_dir, epubs[idx])
        except (ValueError, EOFError):
            pass
        print(f"Please enter a number between 1 and {len(epubs)}.")
