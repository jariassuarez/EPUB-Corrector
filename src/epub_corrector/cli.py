from __future__ import annotations

import argparse
import base64
import csv
import difflib
import json
import logging
import os
import re
import select
import shutil
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import ITEM_DOCUMENT, epub
from openai import OpenAI

SKIP_PARENT_TAGS = {
    "script", "style", "code", "pre", "kbd", "samp",
    "head", "title", "meta", "link", "noscript", "nav",
    "header", "footer", "aside", "address",
}

_RST  = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM  = "\x1b[2m"
_BG_RED = "\x1b[41m"
_BG_GRN = "\x1b[42m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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
    client: OpenAI, model: str, texts: list[str], temperature: float, no_thinking: bool = False, debug: bool = False
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
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=payload["messages"],
        **kwargs,
    )
    content = response.choices[0].message.content or ""
    if debug:
        print("\n--- MODEL RESPONSE ---")
        print(content)
        print("--- END RESPONSE ---\n")
    try:
        return _extract_corrections(content, originals=texts)
    except ValueError:
        logging.warning(
            "Failed to parse model response. Raw content:\n%s",
            content,
        )
        raise


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


# --- diff display ---

def _colored_diff(original: str, proposed: str) -> tuple[str, str]:
    matcher = difflib.SequenceMatcher(None, original, proposed)
    orig_parts: list[str] = []
    prop_parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            orig_parts.append(original[i1:i2])
            prop_parts.append(proposed[j1:j2])
        elif tag == "replace":
            orig_parts.append(f"{_BG_RED}{original[i1:i2]}{_RST}")
            prop_parts.append(f"{_BG_GRN}{proposed[j1:j2]}{_RST}")
        elif tag == "delete":
            orig_parts.append(f"{_BG_RED}{original[i1:i2]}{_RST}")
        elif tag == "insert":
            prop_parts.append(f"{_BG_GRN}{proposed[j1:j2]}{_RST}")
    return "".join(orig_parts), "".join(prop_parts)


def _wrap_ansi(text: str, width: int) -> list[str]:
    """Word-wrap ANSI-colored text to `width` visible characters per line."""
    lines: list[str] = []
    line_parts: list[str] = []
    line_vis = 0
    word_parts: list[str] = []
    word_vis = 0
    i = 0

    while i <= len(text):
        if i < len(text):
            m = ANSI_RE.match(text, i)
            if m:
                word_parts.append(m.group())
                i = m.end()
                continue
            ch: str | None = text[i]
            i += 1
        else:
            ch = None

        if ch is None or ch in (" ", "\n"):
            if word_parts:
                if line_vis == 0:
                    line_parts.extend(word_parts)
                    line_vis = word_vis
                elif line_vis + 1 + word_vis <= width:
                    line_parts.append(" ")
                    line_parts.extend(word_parts)
                    line_vis += 1 + word_vis
                else:
                    lines.append("".join(line_parts))
                    line_parts = list(word_parts)
                    line_vis = word_vis
                word_parts = []
                word_vis = 0
            if ch == "\n":
                lines.append("".join(line_parts))
                line_parts = []
                line_vis = 0
            if ch is None:
                break
        else:
            word_parts.append(ch)
            word_vis += 1

    if line_parts:
        lines.append("".join(line_parts))

    return lines or [""]


def _pad_ansi(text: str, width: int) -> str:
    vis = len(ANSI_RE.sub("", text))
    return text + " " * max(0, width - vis)


def _show_diff(original: str, proposed: str, doc_name: str) -> None:
    W = shutil.get_terminal_size((80, 24)).columns
    col = max(10, (W - 3) // 2)

    orig_c, prop_c = _colored_diff(original, proposed)
    orig_lines = _wrap_ansi(orig_c, col)
    prop_lines = _wrap_ansi(prop_c, col)

    n = max(len(orig_lines), len(prop_lines))
    orig_lines += [""] * (n - len(orig_lines))
    prop_lines += [""] * (n - len(prop_lines))

    bar = "─" * W
    out = [
        "",
        bar,
        _pad_ansi(f"{_BOLD}ORIGINAL{_RST}", col) + " │ " + f"{_BOLD}PROPOSED{_RST}",
        bar,
    ]
    for l, r in zip(orig_lines, prop_lines):
        out.append(_pad_ansi(l, col) + " │ " + r)
    out += [
        bar,
        f"{_DIM}{doc_name}{_RST}",
        f"[{_BOLD}Enter{_RST}] Accept  [{_BOLD}n{_RST}] Skip  "
        f"[{_BOLD}Shift+Enter{_RST} or {_BOLD}a{_RST}] Accept all",
    ]
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def _read_review_key() -> str:
    """Return 'accept', 'reject', or 'accept_all'."""
    if not sys.stdin.isatty():
        return "accept"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x03":
            raise KeyboardInterrupt
        if ch == b"\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                rest = os.read(fd, 16)
                if ch + rest == b"\x1b[13;2u":
                    return "accept_all"
            return "reject"
        if ch in (b"\r", b"\n"):
            return "accept"
        if ch.lower() == b"a":
            return "accept_all"
        if ch.lower() in (b"n", b"s"):
            return "reject"
        return "reject"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# --- report ---

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


# --- checkpoint helpers ---

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


# --- core processing ---

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

        for segment, new_text in zip(batch, corrected):
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
                    continue

                if segment.original_text == new_text:
                    continue

                if not review.auto_accept:
                    _show_diff(segment.original_text, new_text, doc_name)
                    action = _read_review_key()
                    if action == "accept_all":
                        review.auto_accept = True
                        sys.stdout.write(
                            f"\n{_DIM}Auto-accepting all remaining changes.{_RST}\n"
                        )
                        sys.stdout.flush()
                    elif action == "reject":
                        stats.rejected_changes += 1
                        if records is not None:
                            records.append(ChangeRecord(
                                doc_name=doc_name,
                                original=segment.original_text,
                                proposed=new_text,
                                accepted=False,
                            ))
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epub-corrector",
        description="Correct grammar in EPUB text using LM Studio without changing meaning.",
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Input EPUB path. If omitted, books in ./books/ are listed for selection.",
    )
    parser.add_argument(
        "output", nargs="?", default=None,
        help="Output EPUB path. Defaults to <input-stem>_corrected.epub.",
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:1234/v1",
        help="LM Studio OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-key", default="lm-studio",
        help="API key (LM Studio accepts any non-empty value).",
    )
    parser.add_argument("--model", default="local-model", help="Model name in LM Studio.")
    parser.add_argument(
        "--temperature", type=float, default=0.0,
        help="Generation temperature (keep near 0 for minimal rewriting).",
    )
    parser.add_argument(
        "--max-segments-per-request", type=int, default=60,
        help="Hard limit of segments per model request.",
    )
    parser.add_argument(
        "--max-chars-per-request", type=int, default=6000,
        help="Hard character budget per model request.",
    )
    parser.add_argument(
        "--similarity-threshold", type=float, default=0.88,
        help="Auto-reject edits below this sequence similarity.",
    )
    parser.add_argument(
        "--max-change-ratio", type=float, default=0.20,
        help="Auto-reject edits exceeding this character-change ratio.",
    )
    parser.add_argument(
        "--report", metavar="PATH",
        help="Write a CSV change report to PATH.",
    )
    parser.add_argument(
        "--checkpoint", metavar="PATH",
        help="Checkpoint file for resuming interrupted runs.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    parser.add_argument("--debug", action="store_true", help="Print sent and received raw prompts/responses for every model request.")
    parser.add_argument(
        "--no-thinking", action="store_true",
        help="Disable reasoning/thinking mode (passes thinking.type=disabled to the API).",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    input_path: str = args.input or _select_epub_from_books_folder()
    output_path: str = args.output or (
        os.path.splitext(os.path.basename(input_path))[0] + "_corrected.epub"
    )

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    book = epub.read_epub(input_path)
    stats = ProcessingStats()
    records: list[ChangeRecord] | None = [] if args.report else None
    review = ReviewState()

    checkpoint: dict[str, str] = {}
    if args.checkpoint:
        checkpoint = _load_checkpoint(args.checkpoint)
        if checkpoint:
            logging.info(
                "Resuming from checkpoint: %d document(s) already processed.",
                len(checkpoint),
            )

    for item in _iter_document_items(book):
        doc_name: str = item.file_name

        if doc_name in checkpoint:
            logging.info("Skipping already-processed document: %s", doc_name)
            item.set_content(base64.b64decode(checkpoint[doc_name]))
            continue

        _process_document(
            item=item,
            doc_name=doc_name,
            client=client,
            model=args.model,
            temperature=args.temperature,
            max_segments_per_request=args.max_segments_per_request,
            max_chars_per_request=args.max_chars_per_request,
            similarity_threshold=args.similarity_threshold,
            max_change_ratio=args.max_change_ratio,
            stats=stats,
            records=records,
            review=review,
            no_thinking=args.no_thinking,
            debug=args.debug,
        )

        if args.checkpoint:
            checkpoint[doc_name] = base64.b64encode(item.get_content()).decode()
            _save_checkpoint(args.checkpoint, checkpoint)

        epub.write_epub(output_path, book, {})

    _reorder_items_by_spine(book)
    epub.write_epub(output_path, book, {})

    if records is not None and args.report:
        _write_csv_report(records, args.report)
        print(f"Change report written to {args.report} ({len(records)} edits)")

    print(
        "Processed documents={docs}, groups={groups}, segments={segments}, "
        "accepted={accepted}, rejected={rejected}, failed_groups={failed}".format(
            docs=stats.docs_seen,
            groups=stats.groups_seen,
            segments=stats.segments_seen,
            accepted=stats.accepted_changes,
            rejected=stats.rejected_changes,
            failed=stats.failed_groups,
        )
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)
