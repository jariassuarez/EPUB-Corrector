from __future__ import annotations

import argparse
import os
import shutil
import sys

from dotenv import load_dotenv
from openai import OpenAI

from .config import CorrectionConfig
from .engine import BookProcessor
from .glossary import extract_glossary, format_glossary_injection, load_glossary, summarize_glossary
from .llm import LLMClient
from .types import ReviewCallback

try:
    import termios
    import tty
    import select
    _POSIX = True
except ImportError:
    _POSIX = False

try:
    import msvcrt
    _WINDOWS = True
except ImportError:
    _WINDOWS = False

_DEFAULT_BASE_URL = os.environ.get("EPUB_CORRECTOR_BASE_URL", "http://127.0.0.1:1234/v1")
_DEFAULT_API_KEY = os.environ.get("EPUB_CORRECTOR_API_KEY", "lm-studio")
_DEFAULT_MODEL = os.environ.get("EPUB_CORRECTOR_MODEL", "local-model")

_RST = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_BG_RED = "\x1b[41m"
_BG_GRN = "\x1b[42m"

ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


class TerminalReview(ReviewCallback):
    def ask(self, original: str, proposed: str, doc_name: str) -> str:
        self._show_diff(original, proposed, doc_name)
        return self._read_review_key()

    @staticmethod
    def _colored_diff(original: str, proposed: str) -> tuple[str, str]:
        import difflib
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

    @staticmethod
    def _wrap_ansi(text: str, width: int) -> list[str]:
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

    @staticmethod
    def _pad_ansi(text: str, width: int) -> str:
        vis = len(ANSI_RE.sub("", text))
        return text + " " * max(0, width - vis)

    @classmethod
    def _show_diff(cls, original: str, proposed: str, doc_name: str) -> None:
        W = shutil.get_terminal_size((80, 24)).columns
        col = max(10, (W - 3) // 2)

        orig_c, prop_c = cls._colored_diff(original, proposed)
        orig_lines = cls._wrap_ansi(orig_c, col)
        prop_lines = cls._wrap_ansi(prop_c, col)

        n = max(len(orig_lines), len(prop_lines))
        orig_lines += [""] * (n - len(orig_lines))
        prop_lines += [""] * (n - len(prop_lines))

        bar = "─" * W
        out = [
            "",
            bar,
            cls._pad_ansi(f"{_BOLD}ORIGINAL{_RST}", col) + " │ " + f"{_BOLD}PROPOSED{_RST}",
            bar,
        ]
        for l, r in zip(orig_lines, prop_lines):
            out.append(cls._pad_ansi(l, col) + " │ " + r)
        out += [
            bar,
            f"{_DIM}{doc_name}{_RST}",
            f"[{_BOLD}Enter{_RST}] Accept  [{_BOLD}n{_RST}] Skip  "
            f"[{_BOLD}r{_RST}] Retry  [{_BOLD}a{_RST}] Accept all  [{_BOLD}p{_RST}] Pause auto-accept",
        ]
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    @staticmethod
    def _read_review_key() -> str:
        if not sys.stdin.isatty():
            return "accept"
        if _POSIX:
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
                if ch.lower() == b"r":
                    return "retry"
                if ch.lower() == b"p":
                    return "stop_auto_accept"
                if ch.lower() in (b"n", b"s"):
                    return "reject"
                return "reject"
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        elif _WINDOWS:
            ch = msvcrt.getch()
            if ch == b"\x03":
                raise KeyboardInterrupt
            if ch == b"\r" or ch == b"\n":
                return "accept"
            if ch.lower() == b"a":
                return "accept_all"
            if ch.lower() == b"r":
                return "retry"
            if ch.lower() == b"p":
                return "stop_auto_accept"
            if ch.lower() in (b"n", b"s"):
                return "reject"
            return "reject"
        else:
            line = sys.stdin.readline().strip().lower()
            if line in ("", "y", "yes"):
                return "accept"
            if line == "a":
                return "accept_all"
            if line == "r":
                return "retry"
            if line == "p":
                return "stop_auto_accept"
            if line in ("n", "s", "no", "skip"):
                return "reject"
            return "accept"

    def poll(self) -> str | None:
        """Check if user wants to stop auto-accept without blocking."""
        if not sys.stdin.isatty():
            return None
        if _POSIX:
            if select.select([sys.stdin], [], [], 0)[0]:
                try:
                    sys.stdin.readline()
                except Exception:
                    pass
                return "stop_auto_accept"
            return None
        elif _WINDOWS:
            if msvcrt.kbhit():
                while msvcrt.kbhit():
                    msvcrt.getch()
                return "stop_auto_accept"
            return None
        else:
            return None


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
        "--base-url", default=_DEFAULT_BASE_URL,
        help="LM Studio OpenAI-compatible base URL. Overrides EPUB_CORRECTOR_BASE_URL env var.",
    )
    parser.add_argument(
        "--api-key", default=_DEFAULT_API_KEY,
        help="API key (LM Studio accepts any non-empty value). Overrides EPUB_CORRECTOR_API_KEY env var.",
    )
    parser.add_argument("--model", default=_DEFAULT_MODEL, help="Model name in LM Studio. Overrides EPUB_CORRECTOR_MODEL env var.")
    parser.add_argument(
        "--temperature", type=float, default=0.0,
        help="Generation temperature (keep near 0 for minimal rewriting).",
    )
    parser.add_argument(
        "--max-segments-per-request", type=int, default=1,
        help="Hard limit of segments per model request.",
    )
    parser.add_argument(
        "--max-chars-per-request", type=int, default=6000,
        help="Hard character budget per model request.",
    )
    parser.add_argument(
        "--max-context", type=int, default=0,
        help="Number of previous text segments to include as context for each correction. 0 disables context.",
    )
    parser.add_argument(
        "--max-context-chars", type=int, default=3000,
        help="Maximum total characters of context to send per request.",
    )
    parser.add_argument(
        "--conserve-context", action="store_true",
        help="Preserve context across documents/chapters instead of resetting it for each HTML file.",
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
    parser.add_argument(
        "--no-schema", action="store_true",
        help="Disable structured JSON output. By default the tool uses response_format=json_schema to isolate corrected text from model commentary/reasoning.",
    )
    parser.add_argument(
        "--rewrite", nargs="?", const="normal", default=None, metavar="MODE",
        help="Use the rewrite prompt aimed at improving translated literature into natural, fluent English prose instead of the strict grammar-only corrector prompt. Pass 'aggressive' to disable safety filters (sets --similarity-threshold 0 and --max-change-ratio 1.0).",
    )
    parser.add_argument(
        "--translate", metavar="LANGUAGE",
        help="Translate the book into the specified language. This automatically sets --similarity-threshold to 0.0 and --max-change-ratio to 1.0.",
    )
    parser.add_argument(
        "--max-workers", type=int, default=1,
        help="Maximum number of concurrent model requests to send in parallel within a batch. Defaults to 1 (sequential).",
    )
    parser.add_argument(
        "--max-retries", type=int, default=3,
        help="Maximum number of retries for a failed model request before aborting. Defaults to 3.",
    )
    parser.add_argument(
        "--batch", metavar="FOLDER",
        help="Process all EPUB files in FOLDER sequentially. Output and checkpoint paths are auto-derived per book.",
    )
    parser.add_argument(
        "--from", dest="from_doc", type=int, default=None, metavar="N",
        help="Start processing from the Nth HTML document (1-based). Documents before N are skipped.",
    )
    parser.add_argument(
        "--to", dest="to_doc", type=int, default=None, metavar="N",
        help="Stop after processing the Nth HTML document (1-based, inclusive). Documents after N are skipped.",
    )
    parser.add_argument(
        "--glossary", nargs="?", const=True, metavar="INPUT_FILE",
        help=(
            "Extract a glossary of proper nouns, names, and special terms from the EPUB "
            "instead of correcting it. Saved to glossaries/<stem>_glossary.json. "
            "Optionally pass an explicit EPUB path; otherwise uses the positional 'input' argument."
        ),
    )
    parser.add_argument(
        "--glossary-context-length", type=int, default=20000, metavar="CHARS",
        help="Characters per chunk sent to the LLM during glossary extraction (default: 20000).",
    )
    parser.add_argument(
        "--input-glossary", metavar="PATH",
        help=(
            "Path to a glossary JSON file (produced by --glossary). "
            "Injects the glossary terms into the system prompt so the LLM preserves them exactly."
        ),
    )
    parser.add_argument(
        "--summarize-glossary", metavar="PATH",
        help=(
            "Path to a glossary JSON file. Sends it to the LLM and asks it to remove clearly "
            "meaningless entries (web artifacts, generic level numbers, HTML tags, etc.). "
            "Overwrites the file in-place."
        ),
    )
    return parser


def run(args: argparse.Namespace) -> int:
    import logging

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    if args.summarize_glossary:
        gpath = args.summarize_glossary
        if not os.path.isfile(gpath):
            print(f"ERROR: Glossary file not found: {gpath}", file=sys.stderr)
            return 1
        glossary_data = load_glossary(gpath)
        before_total = sum(len(v) for v in glossary_data.values())
        print(f"Summarizing glossary: {gpath} ({before_total} terms)")
        client = OpenAI(base_url=args.base_url, api_key=args.api_key)
        cleaned = summarize_glossary(
            glossary=glossary_data,
            client=client,
            model=args.model,
            temperature=args.temperature,
            no_thinking=args.no_thinking,
            debug=args.debug,
            max_retries=args.max_retries,
        )
        after_total = sum(len(v) for v in cleaned.values())
        import json
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        print(f"Done. {before_total} → {after_total} terms ({before_total - after_total} removed). Saved to {gpath}")
        return 0

    if args.glossary:
        glossary_epub = (
            args.glossary if args.glossary is not True
            else (args.input or _select_epub_from_books_folder())
        )
        if not os.path.isfile(glossary_epub):
            print(f"ERROR: Input EPUB not found: {glossary_epub}", file=sys.stderr)
            return 1
        g_stem = os.path.splitext(os.path.basename(glossary_epub))[0]
        os.makedirs("glossaries", exist_ok=True)
        out_path = os.path.join("glossaries", f"{g_stem}_glossary.json")
        print(f"Extracting glossary from: {glossary_epub}")
        print(f"Output: {out_path}")
        client = OpenAI(base_url=args.base_url, api_key=args.api_key)
        glossary = extract_glossary(
            input_path=glossary_epub,
            client=client,
            model=args.model,
            temperature=args.temperature,
            context_length=args.glossary_context_length,
            no_thinking=args.no_thinking,
            debug=args.debug,
            max_retries=args.max_retries,
        )
        import json
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(glossary, f, ensure_ascii=False, indent=2)
        total_terms = sum(len(v) for v in glossary.values())
        print(f"Glossary saved to {out_path} ({total_terms} terms)")
        return 0

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    config = CorrectionConfig(
        temperature=args.temperature,
        max_segments_per_request=args.max_segments_per_request,
        max_chars_per_request=args.max_chars_per_request,
        similarity_threshold=args.similarity_threshold,
        max_change_ratio=args.max_change_ratio,
        max_context=args.max_context,
        max_context_chars=args.max_context_chars,
        max_workers=args.max_workers,
        max_retries=args.max_retries,
        no_thinking=args.no_thinking,
        debug=args.debug,
        use_schema=not args.no_schema,
        rewrite=args.rewrite is not None,
        translate=bool(args.translate),
        target_language=args.translate,
        aggressive=args.rewrite == "aggressive",
    )

    if args.input_glossary:
        if not os.path.isfile(args.input_glossary):
            print(f"WARNING: Glossary file not found: {args.input_glossary}", file=sys.stderr)
        else:
            glossary_data = load_glossary(args.input_glossary)
            config.glossary_injection = format_glossary_injection(glossary_data) or None
            if config.glossary_injection:
                total_terms = sum(len(v) for v in glossary_data.values())
                print(f"Loaded glossary: {total_terms} terms from {args.input_glossary}")
            else:
                print(f"WARNING: Glossary file {args.input_glossary!r} is empty or has no recognized keys.", file=sys.stderr)

    llm = LLMClient(client, args.model, config)
    processor = BookProcessor(llm, config)
    review_callback = TerminalReview()

    if args.batch:
        if args.output:
            print("ERROR: --output cannot be used with --batch", file=sys.stderr)
            return 1
        if args.checkpoint:
            print("ERROR: --checkpoint cannot be used with --batch", file=sys.stderr)
            return 1
        if args.report:
            print("ERROR: --report cannot be used with --batch", file=sys.stderr)
            return 1

        successes, failures = processor.process_batch(
            args.batch,
            review_callback=review_callback,
            auto_accept=False,
            conserve_context=args.conserve_context,
            should_stop=None,
            from_doc=args.from_doc,
            to_doc=args.to_doc,
        )
        return 0 if not failures else 1

    input_path = args.input or _select_epub_from_books_folder()
    basename = os.path.basename(input_path)
    stem, ext = os.path.splitext(basename)

    translate_suffix = ""
    if config.translate and config.target_language:
        translate_suffix = f"_{config.target_language.replace(' ', '_')}"

    if args.output:
        output_path = args.output
    else:
        os.makedirs("output", exist_ok=True)
        output_path = os.path.join("output", f"{stem}{translate_suffix}{ext}")

    checkpoint_path = args.checkpoint
    if not checkpoint_path:
        os.makedirs("checkpoints", exist_ok=True)
        checkpoint_path = os.path.join("checkpoints", f"{stem}{translate_suffix}.json")

    processor.process_book(
        input_path=input_path,
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        from_doc=args.from_doc,
        to_doc=args.to_doc,
        report_path=args.report,
        review_callback=review_callback,
        auto_accept=False,
        conserve_context=args.conserve_context,
    )
    return 0


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    return run(args)
