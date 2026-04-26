from __future__ import annotations

import base64
import difflib
import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from ebooklib import epub
from openai import OpenAI

load_dotenv()

_DEFAULT_BASE_URL = os.environ.get("EPUB_CORRECTOR_BASE_URL", "http://localhost:1234/v1")
_DEFAULT_API_KEY = os.environ.get("EPUB_CORRECTOR_API_KEY", "lm-studio")
_DEFAULT_MODEL = os.environ.get("EPUB_CORRECTOR_MODEL", "local-model")

from .core import (
    ChangeRecord,
    ProcessingStats,
    ReviewCallback,
    ReviewState,
    StopProcessing,
    _extract_segment_texts,
    _load_checkpoint,
    _process_document,
    _reorder_items_by_spine,
    _save_checkpoint,
    _write_csv_report,
)

DEFAULT_FONT = ("TkDefaultFont", 10)


def fetch_models(base_url: str) -> list[str]:
    """Return model IDs from the OpenAI-compatible /models endpoint."""
    url = base_url.rstrip("/") + "/models"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = []
        for item in data.get("data", []):
            mid = item.get("id")
            if mid:
                models.append(mid)
        return models
    except (URLError, json.JSONDecodeError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to fetch models: {exc}")


class GuiReview(ReviewCallback):
    """Thread-safe review callback using queues."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.request_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.response_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = stop_event

    def ask(self, original: str, proposed: str, doc_name: str) -> str:
        self.request_queue.put({
            "original": original,
            "proposed": proposed,
            "doc_name": doc_name,
        })
        # Block worker thread until GUI thread sends answer or stop is requested
        while True:
            try:
                return self.response_queue.get(timeout=0.2)
            except queue.Empty:
                if self.stop_event.is_set():
                    raise StopProcessing()





class EpubCorrectorGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("EPUB Corrector")
        root.geometry("1200x800")
        try:
            root.state("zoomed")  # Windows / macOS
        except tk.TclError:
            try:
                root.attributes("-zoomed", True)  # Some Linux WMs
            except tk.TclError:
                w = root.winfo_screenwidth()
                h = root.winfo_screenheight()
                root.geometry(f"{w}x{h}+0+0")
        root.minsize(800, 600)

        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.review = GuiReview(self.stop_event)
        self.review_state = ReviewState()

        self._build_ui()
        self._poll_review_queue()

    def _build_ui(self) -> None:
        # Main container - no scrolling
        self.scrollable_frame = ttk.Frame(self.root)
        self.scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Server settings ---
        server_frame = ttk.LabelFrame(self.scrollable_frame, text="Server", padding=10)
        server_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(server_frame, text="Base URL:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.base_url_var = tk.StringVar(value=_DEFAULT_BASE_URL)
        ttk.Entry(server_frame, textvariable=self.base_url_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(server_frame, text="API Key:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.api_key_var = tk.StringVar(value=_DEFAULT_API_KEY)
        self.api_key_entry = ttk.Entry(server_frame, textvariable=self.api_key_var, width=50, show="*")
        self.api_key_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(server_frame, text="Show", variable=self.show_key_var, command=self._toggle_key_visibility).grid(row=1, column=2, sticky="w")

        ttk.Label(server_frame, text="Model:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.model_var = tk.StringVar(value=_DEFAULT_MODEL)
        self.model_combo = ttk.Combobox(server_frame, textvariable=self.model_var, width=48)
        self.model_combo.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(server_frame, text="Refresh Models", command=self._refresh_models).grid(row=2, column=2, padx=5)

        server_frame.columnconfigure(1, weight=1)

        # --- Processing options ---
        opts_frame = ttk.LabelFrame(self.scrollable_frame, text="Options", padding=10)
        opts_frame.pack(fill=tk.X, padx=10, pady=5)

        opts = [
            ("Temperature", self._float_var(0.0)),
            ("Max segments / request", self._int_var(1)),
            ("Max chars / request", self._int_var(6000)),
            ("Similarity threshold", self._float_var(0.88)),
            ("Max change ratio", self._float_var(0.20)),
            ("Max context segments", self._int_var(0)),
            ("Max context chars", self._int_var(3000)),
            ("Max workers", self._int_var(1)),
        ]
        self.option_vars: dict[str, tk.Variable] = {}
        for i, (label, var) in enumerate(opts):
            row = i // 2
            col = (i % 2) * 2
            ttk.Label(opts_frame, text=label + ":").grid(row=row, column=col, sticky="w", padx=5, pady=2)
            ent = ttk.Entry(opts_frame, textvariable=var, width=12)
            ent.grid(row=row, column=col + 1, sticky="w", padx=5, pady=2)
            self.option_vars[label] = var

        # Translate field (spanning full width below the grid)
        translate_row = (len(opts) + 1) // 2
        ttk.Label(opts_frame, text="Translate to:").grid(row=translate_row, column=0, sticky="w", padx=5, pady=2)
        self.translate_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.translate_var, width=30).grid(row=translate_row, column=1, columnspan=3, sticky="w", padx=5, pady=2)

        # Checkboxes
        cb_frame = ttk.Frame(opts_frame)
        cb_row = translate_row + 1
        cb_frame.grid(row=cb_row, column=0, columnspan=4, sticky="w", pady=(5, 0))
        self.no_thinking_var = tk.BooleanVar(value=False)
        self.debug_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)
        self.auto_accept_var = tk.BooleanVar(value=False)
        self.schema_var = tk.BooleanVar(value=False)
        self.conserve_context_var = tk.BooleanVar(value=False)
        self.rewrite_var = tk.BooleanVar(value=False)
        self.aggressive_var = tk.BooleanVar(value=False)
        self.aggressive_var.trace_add("write", self._on_aggressive_toggle)
        self.auto_accept_var.trace_add("write", self._on_auto_accept_toggle)
        ttk.Checkbutton(cb_frame, text="No thinking", variable=self.no_thinking_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Schema", variable=self.schema_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Debug", variable=self.debug_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Verbose", variable=self.verbose_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Auto-accept all", variable=self.auto_accept_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Conserve context", variable=self.conserve_context_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Rewrite", variable=self.rewrite_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Aggressive", variable=self.aggressive_var).pack(side=tk.LEFT, padx=5)

        # --- Files ---
        files_frame = ttk.LabelFrame(self.scrollable_frame, text="Files", padding=10)
        files_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.checkpoint_var = tk.StringVar()
        self.report_var = tk.StringVar()

        for i, (label, var, cmd) in enumerate([
            ("Input EPUB", self.input_path_var, self._browse_input),
            ("Output EPUB", self.output_path_var, self._browse_output),
            ("Checkpoint (optional)", self.checkpoint_var, self._browse_checkpoint),
            ("Report CSV (optional)", self.report_var, self._browse_report),
        ]):
            ttk.Label(files_frame, text=label + ":").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            ttk.Entry(files_frame, textvariable=var, width=60).grid(row=i, column=1, sticky="ew", padx=5, pady=2)
            ttk.Button(files_frame, text="Browse...", command=cmd).grid(row=i, column=2, padx=5)

        files_frame.columnconfigure(1, weight=1)

        # --- Actions ---
        action_frame = ttk.Frame(self.scrollable_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=10)
        self.start_btn = ttk.Button(action_frame, text="Start", command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(action_frame, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # --- Review ---
        self._build_review_panel()

    def _float_var(self, value: float) -> tk.DoubleVar:
        return tk.DoubleVar(value=value)

    def _int_var(self, value: int) -> tk.IntVar:
        return tk.IntVar(value=value)

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.config(show="" if self.show_key_var.get() else "*")

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.input_path_var.set(path)
            if not self.output_path_var.get():
                self.output_path_var.set(
                    os.path.splitext(path)[0] + "_corrected.epub"
                )

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".epub",
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")],
        )
        if path:
            self.output_path_var.set(path)

    def _browse_checkpoint(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.checkpoint_var.set(path)

    def _browse_report(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.report_var.set(path)

    def _refresh_models(self) -> None:
        try:
            models = fetch_models(self.base_url_var.get())
            self.model_combo["values"] = models
            if models and self.model_var.get() not in models:
                self.model_var.set(models[0])
            pass
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def _build_review_panel(self) -> None:
        self.review_frame = ttk.LabelFrame(self.scrollable_frame, text="Review Change", padding=10)
        self.review_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.review_doc_label = ttk.Label(self.review_frame, text="No pending review.", font=("TkDefaultFont", 10, "bold"))
        self.review_doc_label.pack(pady=(5, 10), padx=10, anchor="w")

        paned = ttk.PanedWindow(self.review_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_frame = ttk.LabelFrame(paned, text="Original", padding=5)
        right_frame = ttk.LabelFrame(paned, text="Proposed", padding=5)
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=1)

        self.review_orig_text = tk.Text(
            left_frame, wrap=tk.WORD, font=DEFAULT_FONT, state=tk.DISABLED,
            bg="#fdfdfd", relief=tk.SUNKEN, borderwidth=1, height=10,
        )
        self.review_prop_text = tk.Text(
            right_frame, wrap=tk.WORD, font=DEFAULT_FONT, state=tk.DISABLED,
            bg="#fdfdfd", relief=tk.SUNKEN, borderwidth=1, height=10,
        )
        self.review_orig_text.pack(fill=tk.BOTH, expand=True)
        self.review_prop_text.pack(fill=tk.BOTH, expand=True)

        for txt, tag_name, bg in (
            (self.review_orig_text, "del", "#ffcccc"),
            (self.review_prop_text, "ins", "#ccffcc"),
        ):
            txt.tag_config(tag_name, background=bg)

        btn_frame = ttk.Frame(self.review_frame)
        btn_frame.pack(pady=(10, 5))

        self.review_accept_btn = ttk.Button(
            btn_frame, text="Accept (Enter)", command=lambda: self._on_review_action("accept"),
        )
        self.review_accept_btn.pack(side=tk.LEFT, padx=5)
        self.review_skip_btn = ttk.Button(
            btn_frame, text="Skip (n)", command=lambda: self._on_review_action("reject"),
        )
        self.review_skip_btn.pack(side=tk.LEFT, padx=5)
        self.review_retry_btn = ttk.Button(
            btn_frame, text="Retry (r)", command=lambda: self._on_review_action("retry"),
        )
        self.review_retry_btn.pack(side=tk.LEFT, padx=5)
        self.review_accept_all_btn = ttk.Button(
            btn_frame, text="Accept All (a)", command=lambda: self._on_review_action("accept_all"),
        )
        self.review_accept_all_btn.pack(side=tk.LEFT, padx=5)

        self._review_pending = False
        self.root.bind("<Return>", self._on_review_key)
        self.root.bind("<n>", self._on_review_key)
        self.root.bind("<r>", self._on_review_key)
        self.root.bind("<a>", self._on_review_key)
        self.root.bind("<Escape>", self._on_review_key)

        self._clear_review()

    def _on_review_key(self, event: Any) -> None:
        if not self._review_pending:
            return
        # Only process shortcuts when focus is inside the review panel
        focus = self.root.focus_get()
        if focus is not None:
            w = focus
            while w is not None:
                if w == self.review_frame:
                    break
                try:
                    w = w.master
                except AttributeError:
                    w = None
            else:
                return
        key = event.keysym
        if key in ("Return",):
            self._on_review_action("accept")
        elif key == "n":
            self._on_review_action("reject")
        elif key == "r":
            self._on_review_action("retry")
        elif key == "a":
            self._on_review_action("accept_all")
        elif key == "Escape":
            self._on_review_action("reject")

    def _clear_review(self) -> None:
        self._review_pending = False
        self.review_doc_label.config(text="No pending review.")
        for widget in (self.review_orig_text, self.review_prop_text):
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, "Waiting for next change...")
            widget.config(state=tk.DISABLED)
        for btn in (self.review_accept_btn, self.review_skip_btn, self.review_retry_btn, self.review_accept_all_btn):
            btn.config(state=tk.DISABLED)

    def _show_review(self, original: str, proposed: str, doc_name: str) -> None:
        self._review_pending = True
        self.review_doc_label.config(text=f"Document: {doc_name}")
        self._fill_diff(self.review_orig_text, original, proposed, is_original=True)
        self._fill_diff(self.review_prop_text, original, proposed, is_original=False)
        for btn in (self.review_accept_btn, self.review_skip_btn, self.review_retry_btn, self.review_accept_all_btn):
            btn.config(state=tk.NORMAL)
        self.review_frame.focus_set()

    def _on_auto_accept_toggle(self, *args: Any) -> None:
        self.review_state.auto_accept = self.auto_accept_var.get()

    def _on_aggressive_toggle(self, *args: Any) -> None:
        if self.aggressive_var.get():
            self.rewrite_var.set(True)

    def _on_review_action(self, action: str) -> None:
        if not self._review_pending:
            return
        if action == "accept_all":
            self.auto_accept_var.set(True)
        self.review.response_queue.put(action)
        self._clear_review()

    @staticmethod
    def _fill_diff(
        widget: tk.Text,
        original: str,
        proposed: str,
        is_original: bool,
    ) -> None:
        matcher = difflib.SequenceMatcher(None, original, proposed)
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if is_original:
                if tag == "insert":
                    continue
                text = original[i1:i2]
                tag_name = "del" if tag in ("replace", "delete") else ""
            else:
                if tag == "delete":
                    continue
                text = proposed[j1:j2]
                tag_name = "ins" if tag in ("replace", "insert") else ""

            widget.insert(tk.END, text, tag_name if tag_name else ())
        widget.config(state=tk.DISABLED)

    def _poll_review_queue(self) -> None:
        try:
            while True:
                req = self.review.request_queue.get_nowait()
                self._show_review(req["original"], req["proposed"], req["doc_name"])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_review_queue)


    def _get_option(self, label: str, type_: type) -> Any:
        var = self.option_vars[label]
        try:
            return type_(var.get())
        except tk.TclError:
            messagebox.showerror("Invalid input", f"{label} must be a valid number.")
            raise ValueError

    def _start(self) -> None:
        input_path = self.input_path_var.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "Please select an input EPUB file.")
            return
        if not os.path.isfile(input_path):
            messagebox.showerror("File not found", f"Input file not found:\n{input_path}")
            return

        output_path = self.output_path_var.get().strip()
        if not output_path:
            output_path = os.path.splitext(input_path)[0] + "_corrected.epub"
            self.output_path_var.set(output_path)

        try:
            temperature = self._get_option("Temperature", float)
            max_segments = self._get_option("Max segments / request", int)
            max_chars = self._get_option("Max chars / request", int)
            similarity = self._get_option("Similarity threshold", float)
            max_change = self._get_option("Max change ratio", float)
        except ValueError:
            return

        self.stop_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        translate_lang = self.translate_var.get().strip() or None

        self.worker_thread = threading.Thread(
            target=self._worker,
            kwargs={
                "input_path": input_path,
                "output_path": output_path,
                "temperature": temperature,
                "max_segments": max_segments,
                "max_chars": max_chars,
                "similarity": similarity,
                "max_change": max_change,
                "max_context": self._get_option("Max context segments", int),
                "max_context_chars": self._get_option("Max context chars", int),
                "conserve_context": self.conserve_context_var.get(),
                "rewrite": self.rewrite_var.get(),
                "aggressive": self.aggressive_var.get(),
                "translate": translate_lang,
                "max_workers": self._get_option("Max workers", int),
            },
            daemon=True,
        )
        self.worker_thread.start()

    def _stop(self) -> None:
        self.stop_event.set()

    def _worker(
        self,
        input_path: str,
        output_path: str,
        temperature: float,
        max_segments: int,
        max_chars: int,
        similarity: float,
        max_change: float,
        max_context: int,
        max_context_chars: int,
        conserve_context: bool,
        rewrite: bool = False,
        aggressive: bool = False,
        translate: str | None = None,
        max_workers: int = 1,
    ) -> None:
        try:
            level = logging.INFO if self.verbose_var.get() else logging.WARNING
            logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

            client = OpenAI(
                base_url=self.base_url_var.get().strip(),
                api_key=self.api_key_var.get().strip(),
            )
            book = epub.read_epub(input_path)
            stats = ProcessingStats()
            report_path = self.report_var.get().strip()
            records: list[ChangeRecord] | None = [] if report_path else None

            checkpoint_path = self.checkpoint_var.get().strip()
            checkpoint: dict[str, str] = {}
            if checkpoint_path:
                checkpoint = _load_checkpoint(checkpoint_path)
                if checkpoint:
                    print(f"Resuming from checkpoint: {len(checkpoint)} document(s) already processed.")

            conserved_context: list[str] = []

            is_translate = bool(translate)
            similarity_threshold = 0.0 if (is_translate or aggressive) else similarity
            max_change_ratio = 1.0 if (is_translate or aggressive) else max_change

            for item in self._iter_document_items(book):
                if self.stop_event.is_set():
                    print("Stopping as requested.")
                    break

                doc_name: str = item.file_name

                if doc_name in checkpoint:
                    print(f"Skipping already-processed document: {doc_name}")
                    item.set_content(base64.b64decode(checkpoint[doc_name]))
                    if conserve_context:
                        texts = _extract_segment_texts(item)
                        conserved_context.extend(texts)
                        if max_context > 0:
                            conserved_context = conserved_context[-max_context:]
                    continue

                conserved_context = _process_document(
                    item=item,
                    doc_name=doc_name,
                    client=client,
                    model=self.model_var.get().strip(),
                    temperature=temperature,
                    max_segments_per_request=max_segments,
                    max_chars_per_request=max_chars,
                    similarity_threshold=similarity_threshold,
                    max_change_ratio=max_change_ratio,
                    stats=stats,
                    records=records,
                    review=self.review_state,
                    review_callback=self.review,
                    no_thinking=self.no_thinking_var.get(),
                    debug=self.debug_var.get(),
                    use_schema=self.schema_var.get(),
                    max_context=max_context,
                    max_context_chars=max_context_chars,
                    previous_context=conserved_context if conserve_context else None,
                    rewrite=rewrite,
                    translate=is_translate,
                    target_language=translate,
                    max_workers=max_workers,
                    should_stop=self.stop_event.is_set,
                )

                if checkpoint_path:
                    checkpoint[doc_name] = base64.b64encode(item.get_content()).decode()
                    _save_checkpoint(checkpoint_path, checkpoint)

                epub.write_epub(output_path, book, {})

            _reorder_items_by_spine(book)
            epub.write_epub(output_path, book, {})

            if records is not None and report_path:
                _write_csv_report(records, report_path)
                print(f"Change report written to {report_path} ({len(records)} edits)")

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
            print("Done.")
        except StopProcessing:
            print("Stopping as requested.")
        except Exception as exc:
            print(f"ERROR: {exc}")
        finally:
            self.root.after(0, self._on_worker_done)

    @staticmethod
    def _iter_document_items(book) -> Any:
        from ebooklib import ITEM_DOCUMENT
        spine_ids = [idref for idref, _ in book.spine]
        id_to_item = {}
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            id_to_item[item.id] = item
        for idref in spine_ids:
            if idref in id_to_item:
                yield id_to_item[idref]

    def _on_worker_done(self) -> None:
        self._clear_review()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)


def main() -> None:
    root = tk.Tk()
    EpubCorrectorGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
