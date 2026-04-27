from __future__ import annotations

import base64
import csv
import difflib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import ITEM_DOCUMENT
from openai import OpenAI

SKIP_PARENT_TAGS = {
    "script", "style", "code", "pre", "kbd", "samp",
    "head", "title", "meta", "link", "noscript", "nav",
    "header", "footer", "aside", "address",
}


class StopProcessing(Exception):
    """Raised when the user requests to stop processing."""


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


_REWRITE_SYSTEM_PROMPT = (
    "You are a professional fiction editor specializing in translated literature.\n\n"
    "Your goal is to produce clean, natural, fluent English prose that reads as if it were written by a native English author — not translated.\n\n"
    "You MAY:\n"
    "- Restructure awkward or unnatural sentences\n"
    "- Reorder clauses for better flow\n"
    "- Replace unnatural phrasing with idiomatic English equivalents\n"
    "- Correct grammar, punctuation, capitalization, and obvious typos\n\n"
    "You must NEVER:\n"
    "- Change names, places, or any factual information\n"
    "- Add or remove facts, events, or details\n"
    "- Change the meaning, tone, or intent of any sentence or dialogue\n"
    "- Alter numbers, dates, or chronology\n"
    "- Summarize or condense content\n"
    "- Add commentary, notes, or explanations\n\n"
    "Formatting rules:\n"
    "- Preserve all quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear\n"
    "- Do not alter markup, formatting structure, or whitespace\n"
    "- Output clean prose only — no bolding, no annotations"
    "- Do not remove quotation marks from short standalone lines — these may represent internal thought or narration conventions from the source text."
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a strict grammar and spelling corrector. "
    "Do not remove quotation marks from short standalone lines — these may represent internal thought or narration conventions from the source text."
    "Correct only grammar, punctuation, capitalization, and obvious typos. "
    "Do NOT change numbers, dates, entities, or formatting. "
    "Do NOT under any circumstances change names, places, or any other factual information. "
    "Do not change formatting, markup, or whitespace. "
    "Adopt the tone of a professional fiction editor. Provide clean, flowing prose without bolding, and ensure the grammar follows standard novel-writing conventions. "
    "Do not add or remove facts, style, tone, meaning, entities, numbers, chronology, or dialogue intent. "
    "Do not summarize. Do not paraphrase unless required for grammar. "
    "Preserve ALL quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear, including at the very start and very end of the text. "
)

_TRANSLATE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a professional literary translator.\n\n"
    "Your task is to translate the provided text into {language}. "
    "Preserve all meaning, tone, style, intent, and factual content exactly. "
    "Do NOT change names, places, numbers, dates, or chronology. "
    "Do NOT add or remove facts, events, or details. "
    "Do NOT summarize or condense content. "
    "Do NOT add commentary, notes, or explanations.\n\n"
    "Formatting rules:\n"
    "- Preserve all quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear\n"
    "- Do not alter markup, formatting structure, or whitespace\n"
    "- Output clean prose only — no bolding, no annotations"
)


def _build_messages(
    text: str,
    context_texts: list[str] | None = None,
    no_thinking: bool = False,
    use_schema: bool = False,
    rewrite: bool = False,
    translate: bool = False,
    target_language: str | None = None,
    glossary_injection: str | None = None,
) -> list[dict[str, str]]:
    if translate and target_language:
        system = _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(language=target_language)
    else:
        system = _REWRITE_SYSTEM_PROMPT if rewrite else _DEFAULT_SYSTEM_PROMPT

    if glossary_injection:
        system += glossary_injection

    if context_texts:
        if translate and target_language:
            system += (
                " The user will provide context paragraphs marked [CONTEXT] followed by a target paragraph marked [TRANSLATE THIS]. "
                "Use the context only for reference (names, pronouns, terminology, style). "
                "Translate ONLY the [TRANSLATE THIS] paragraph. "
                "Return ONLY the translated version of the [TRANSLATE THIS] paragraph, with no extra commentary, explanation, or formatting."
            )
        else:
            system += (
                " The user will provide context paragraphs marked [CONTEXT] followed by a target paragraph marked [CORRECT THIS]. "
                "Use the context only for reference (names, pronouns, terminology, style). "
                "Correct ONLY the [CORRECT THIS] paragraph. "
                "Return ONLY the corrected version of the [CORRECT THIS] paragraph, with no extra commentary, explanation, or formatting."
            )
    else:
        if translate and target_language:
            system += " Return ONLY the translated version of the text you are sent, with no extra commentary, explanation, or formatting."
        else:
            system += " Return ONLY the corrected version of the text you are sent, with no extra commentary, explanation, or formatting."

    if use_schema:
        if translate and target_language:
            system += (
                " Respond with a JSON object containing a single key 'corrected_text' "
                "whose value is the translated text string. Do not include any other keys or commentary."
            )
        else:
            system += (
                " Respond with a JSON object containing a single key 'corrected_text' "
                "whose value is the corrected text string. Do not include any other keys or commentary."
            )
    if no_thinking:
        system = "/no_think\n\n" + system

    user_parts: list[str] = []
    for ctx in context_texts or []:
        user_parts.append(f"[CONTEXT]\n{ctx}")

    if translate and target_language:
        user_parts.append(f"[TRANSLATE THIS]\n{text}")
    else:
        user_parts.append(f"[CORRECT THIS]\n{text}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


# Boundary punctuation that must never be dropped
_BOUNDARY_PUNCT = frozenset('""\'\'-–—…')


def _extract_correction(raw: str, use_schema: bool = False) -> str:
    """Strip optional markdown fences, parse JSON schema output when enabled, and return clean corrected text."""
    text = raw.strip()
    fenced = re.search(r"^```(?:\w+)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if use_schema:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "corrected_text" in data:
                return str(data["corrected_text"])
        except json.JSONDecodeError:
            pass
    return text


def _restore_boundary_punctuation(original: str, corrected: str) -> str:
    """Re-instate leading/trailing quotes, dashes, or ellipsis the model may have stripped."""
    out = corrected
    # Leading
    if original and out and original[0] in _BOUNDARY_PUNCT and out[0] != original[0]:
        out = original[0] + out
    # Trailing
    if original and out and original[-1] in _BOUNDARY_PUNCT and out[-1] != original[-1]:
        out = out + original[-1]
    return out


def _request_corrections(
    client: OpenAI, model: str, texts: list[str], temperature: float,
    no_thinking: bool = False, debug: bool = False, max_retries: int = 3,
    use_schema: bool = False,
    max_context: int = 0,
    max_context_chars: int = 0,
    previous_context: list[str] | None = None,
    rewrite: bool = False,
    translate: bool = False,
    target_language: str | None = None,
    max_workers: int = 1,
    glossary_injection: str | None = None,
) -> list[str]:
    kwargs = {}
    if no_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if use_schema:
        schema_description = (
            "The translated text with the language changed while preserving all meaning, tone, and style."
            if translate and target_language else
            "The corrected text with only grammar, punctuation, capitalization, and typo fixes applied."
        )
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "text_correction",
                "schema": {
                    "type": "object",
                    "properties": {
                        "corrected_text": {
                            "type": "string",
                            "description": schema_description,
                        }
                    },
                    "required": ["corrected_text"],
                    "additionalProperties": False,
                },
            },
        }

    all_prior = list(previous_context or [])
    payloads: list[tuple[str, dict]] = []
    for text in texts:
        context: list[str] = []
        if max_context > 0 and all_prior:
            candidates = all_prior[-max_context:] if len(all_prior) > max_context else list(all_prior)
            total_chars = 0
            for ctx in reversed(candidates):
                if max_context_chars > 0 and total_chars + len(ctx) > max_context_chars:
                    break
                context.insert(0, ctx)
                total_chars += len(ctx)

        messages = _build_messages(
            text,
            context_texts=context or None,
            no_thinking=no_thinking,
            use_schema=use_schema,
            rewrite=rewrite,
            translate=translate,
            target_language=target_language,
            glossary_injection=glossary_injection,
        )
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
            **kwargs,
        }
        payloads.append((text, payload))
        all_prior.append(text)

    def _do_single(item: tuple[str, dict]) -> str:
        text, payload = item
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
                corrected = _extract_correction(content, use_schema=use_schema)
                corrected = _restore_boundary_punctuation(text, corrected)
                return corrected
            except Exception as exc:
                logging.warning("Model request failed (attempt %d/%d): %s", attempt, max_retries, exc)
                last_exc = exc
                if attempt < max_retries:
                    print(f"    Request failed, retrying... ({attempt}/{max_retries})")
                    continue
                raise last_exc

    if max_workers <= 1:
        return [_do_single(item) for item in payloads]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_do_single, payloads))


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


def _extract_segment_texts(item) -> list[str]:
    soup = BeautifulSoup(item.get_content(), "xml")
    segments = _iter_rewritable_segments(soup)
    return [s.original_text for s in segments]


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
    use_schema: bool = False,
    max_context: int = 0,
    max_context_chars: int = 0,
    previous_context: list[str] | None = None,
    rewrite: bool = False,
    translate: bool = False,
    target_language: str | None = None,
    max_workers: int = 1,
    should_stop: Callable[[], bool] | None = None,
    glossary_injection: str | None = None,
) -> list[str]:
    raw = item.get_content()
    soup = BeautifulSoup(raw, "xml")

    segments = _iter_rewritable_segments(soup)
    if not segments:
        return list(previous_context) if previous_context else []

    stats.docs_seen += 1
    recent_context: list[str] = list(previous_context) if previous_context else []

    for batch_idx, batch in enumerate(_split_large_group(
        segments,
        max_segments=max_segments_per_request,
        max_chars=max_chars_per_request,
    ), start=1):
        if should_stop and should_stop():
            raise StopProcessing()

        stats.groups_seen += 1
        stats.segments_seen += len(batch)
        originals = [s.original_text for s in batch]
        print(f"  [{doc_name}] batch {batch_idx} ({len(batch)} segments, {sum(len(s) for s in originals)} chars)...", flush=True)
        t0 = time.perf_counter()
        try:
            corrected = _request_corrections(
                client=client, model=model, texts=originals, temperature=temperature,
                no_thinking=no_thinking, debug=debug, use_schema=use_schema,
                max_context=max_context, max_context_chars=max_context_chars,
                previous_context=recent_context,
                rewrite=rewrite,
                translate=translate,
                target_language=target_language,
                max_workers=max_workers,
                glossary_injection=glossary_injection,
            )
            t1 = time.perf_counter()
            elapsed = t1 - t0
            total_chars = sum(len(s) for s in originals)
            print(f"    took {elapsed:.2f}s for 1 request, {elapsed:.2f}s per request, {elapsed / total_chars:.4f}s per char (average)", flush=True)
        except Exception as exc:
            stats.failed_groups += 1
            logging.warning("Model request failed for one batch: %s", exc)
            print(f"    FAILED: {exc}")
            continue

        if should_stop and should_stop():
            raise StopProcessing()

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
                if should_stop and should_stop():
                    raise StopProcessing()
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
                            no_thinking=no_thinking, debug=debug, use_schema=use_schema,
                            max_context=max_context, max_context_chars=max_context_chars,
                            previous_context=recent_context,
                            rewrite=rewrite,
                            translate=translate,
                            target_language=target_language,
                            max_workers=max_workers,
                            glossary_injection=glossary_injection,
                        )
                    except Exception as exc:
                        stats.failed_groups += 1
                        logging.warning("Model request failed on retry: %s", exc)
                        print(f"    FAILED on retry: {exc}")
                        break
                    if should_stop and should_stop():
                        raise StopProcessing()
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

        if max_context > 0:
            recent_context.extend(originals)
            recent_context = recent_context[-max_context:]

    item.set_content(str(soup).encode("utf-8"))
    return recent_context


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
