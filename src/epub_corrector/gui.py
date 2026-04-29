from __future__ import annotations

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
from openai import OpenAI

from .config import CorrectionConfig
from .engine import BookProcessor
from .glossary import extract_glossary, format_glossary_injection, load_glossary, summarize_glossary
from .llm import LLMClient
from .types import ReviewCallback, ReviewState, StopProcessing

load_dotenv()

_DEFAULT_BASE_URL = os.environ.get("EPUB_CORRECTOR_BASE_URL", "http://localhost:1234/v1")
_DEFAULT_API_KEY = os.environ.get("EPUB_CORRECTOR_API_KEY", "lm-studio")
_DEFAULT_MODEL = os.environ.get("EPUB_CORRECTOR_MODEL", "local-model")

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
        while True:
            try:
                return self.response_queue.get(timeout=0.2)
            except queue.Empty:
                if self.stop_event.is_set():
                    raise StopProcessing()

    def poll(self) -> str | None:
        """No-op in GUI mode; stopping is handled via stop_event."""
        return None


class EpubCorrectorGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("EPUB Corrector")
        root.geometry("1200x800")
        try:
            root.state("zoomed")
        except tk.TclError:
            try:
                root.attributes("-zoomed", True)
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
        self.scrollable_frame = ttk.Frame(self.root)
        self.scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

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
            ("Max retries", self._int_var(3)),
        ]
        self.option_vars: dict[str, tk.Variable] = {}
        for i, (label, var) in enumerate(opts):
            row = i // 2
            col = (i % 2) * 2
            ttk.Label(opts_frame, text=label + ":").grid(row=row, column=col, sticky="w", padx=5, pady=2)
            ent = ttk.Entry(opts_frame, textvariable=var, width=12)
            ent.grid(row=row, column=col + 1, sticky="w", padx=5, pady=2)
            self.option_vars[label] = var

        translate_row = (len(opts) + 1) // 2
        ttk.Label(opts_frame, text="Translate to:").grid(row=translate_row, column=0, sticky="w", padx=5, pady=2)
        self.translate_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.translate_var, width=30).grid(row=translate_row, column=1, columnspan=3, sticky="w", padx=5, pady=2)

        range_row = translate_row + 1
        ttk.Label(opts_frame, text="From doc #:").grid(row=range_row, column=0, sticky="w", padx=5, pady=2)
        self.from_doc_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.from_doc_var, width=8).grid(row=range_row, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(opts_frame, text="To doc #:").grid(row=range_row, column=2, sticky="w", padx=5, pady=2)
        self.to_doc_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.to_doc_var, width=8).grid(row=range_row, column=3, sticky="w", padx=5, pady=2)

        cb_frame = ttk.Frame(opts_frame)
        cb_row = range_row + 1
        cb_frame.grid(row=cb_row, column=0, columnspan=4, sticky="w", pady=(5, 0))
        self.no_thinking_var = tk.BooleanVar(value=False)
        self.debug_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)
        self.auto_accept_var = tk.BooleanVar(value=False)
        self.no_schema_var = tk.BooleanVar(value=False)
        self.conserve_context_var = tk.BooleanVar(value=False)
        self.rewrite_var = tk.BooleanVar(value=False)
        self.aggressive_var = tk.BooleanVar(value=False)
        self.aggressive_var.trace_add("write", self._on_aggressive_toggle)
        self.auto_accept_var.trace_add("write", self._on_auto_accept_toggle)
        ttk.Checkbutton(cb_frame, text="No thinking", variable=self.no_thinking_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="No schema", variable=self.no_schema_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Debug", variable=self.debug_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Verbose", variable=self.verbose_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Auto-accept all", variable=self.auto_accept_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Conserve context", variable=self.conserve_context_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Rewrite", variable=self.rewrite_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(cb_frame, text="Aggressive", variable=self.aggressive_var).pack(side=tk.LEFT, padx=5)

        files_frame = ttk.LabelFrame(self.scrollable_frame, text="Files", padding=10)
        files_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.checkpoint_var = tk.StringVar()
        self.report_var = tk.StringVar()

        self.batch_mode_var = tk.BooleanVar(value=False)
        self.batch_mode_var.trace_add("write", self._on_batch_mode_toggle)
        ttk.Checkbutton(
            files_frame,
            text="Batch mode (input is a folder containing EPUBs)",
            variable=self.batch_mode_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=5, pady=2)

        self.file_widgets: list[tuple[Any, Any]] = []
        for i, (label, var, cmd) in enumerate([
            ("Input", self.input_path_var, self._browse_input),
            ("Output EPUB", self.output_path_var, self._browse_output),
            ("Checkpoint (optional)", self.checkpoint_var, self._browse_checkpoint),
            ("Report CSV (optional)", self.report_var, self._browse_report),
        ], start=1):
            ttk.Label(files_frame, text=label + ":").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            ent = ttk.Entry(files_frame, textvariable=var, width=60)
            ent.grid(row=i, column=1, sticky="ew", padx=5, pady=2)
            btn = ttk.Button(files_frame, text="Browse...", command=cmd)
            btn.grid(row=i, column=2, padx=5)
            if label != "Input":
                self.file_widgets.append((ent, btn))

        files_frame.columnconfigure(1, weight=1)

        glossary_frame = ttk.LabelFrame(self.scrollable_frame, text="Glossary", padding=10)
        glossary_frame.pack(fill=tk.X, padx=10, pady=5)

        self.glossary_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            glossary_frame,
            text="Extract glossary only (standalone mode — does not correct the book)",
            variable=self.glossary_mode_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=5, pady=2)

        ttk.Label(glossary_frame, text="Context length (chars):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.glossary_context_length_var = tk.IntVar(value=20000)
        ttk.Entry(glossary_frame, textvariable=self.glossary_context_length_var, width=12).grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(glossary_frame, text="Save glossary to:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.glossary_output_var = tk.StringVar()
        ttk.Entry(glossary_frame, textvariable=self.glossary_output_var, width=50).grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(glossary_frame, text="Browse...", command=self._browse_glossary_output).grid(row=2, column=2, padx=5)

        ttk.Label(glossary_frame, text="Input glossary (optional):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.input_glossary_var = tk.StringVar()
        ttk.Entry(glossary_frame, textvariable=self.input_glossary_var, width=50).grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(glossary_frame, text="Browse...", command=self._browse_input_glossary).grid(row=3, column=2, padx=5)

        ttk.Label(glossary_frame, text="Summarize glossary:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.summarize_glossary_var = tk.StringVar()
        ttk.Entry(glossary_frame, textvariable=self.summarize_glossary_var, width=50).grid(row=4, column=1, sticky="ew", padx=5, pady=2)
        sum_btn_frame = ttk.Frame(glossary_frame)
        sum_btn_frame.grid(row=4, column=2, padx=5, sticky="w")
        ttk.Button(sum_btn_frame, text="Browse...", command=self._browse_summarize_glossary).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sum_btn_frame, text="Summarize", command=self._start_summarize_glossary).pack(side=tk.LEFT)

        glossary_frame.columnconfigure(1, weight=1)

        action_frame = ttk.Frame(self.scrollable_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=10)
        self.start_btn = ttk.Button(action_frame, text="Start", command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(action_frame, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self._build_review_panel()

    def _float_var(self, value: float) -> tk.DoubleVar:
        return tk.DoubleVar(value=value)

    def _int_var(self, value: int) -> tk.IntVar:
        return tk.IntVar(value=value)

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.config(show="" if self.show_key_var.get() else "*")

    def _on_batch_mode_toggle(self, *args: Any) -> None:
        batch = self.batch_mode_var.get()
        for ent, btn in self.file_widgets:
            ent.config(state=tk.DISABLED if batch else tk.NORMAL)
            btn.config(state=tk.DISABLED if batch else tk.NORMAL)

    def _browse_input(self) -> None:
        if self.batch_mode_var.get():
            path = filedialog.askdirectory()
            if path:
                self.input_path_var.set(path)
        else:
            path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
            if path:
                self.input_path_var.set(path)
                if not self.output_path_var.get():
                    self.output_path_var.set(
                        os.path.splitext(path)[0] + "_corrected.epub"
                    )
                if not self.glossary_output_var.get():
                    stem = os.path.splitext(os.path.basename(path))[0]
                    os.makedirs("glossaries", exist_ok=True)
                    self.glossary_output_var.set(
                        os.path.join("glossaries", f"{stem}_glossary.json")
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

    def _browse_glossary_output(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.glossary_output_var.set(path)

    def _browse_input_glossary(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.input_glossary_var.set(path)

    def _browse_summarize_glossary(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.summarize_glossary_var.set(path)

    def _start_summarize_glossary(self) -> None:
        gpath = self.summarize_glossary_var.get().strip()
        if not gpath:
            messagebox.showerror("Missing path", "Please select a glossary file to summarize.")
            return
        if not os.path.isfile(gpath):
            messagebox.showerror("File not found", f"Glossary file not found:\n{gpath}")
            return
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_event.clear()
        t = threading.Thread(target=self._worker_summarize_glossary, args=(gpath,), daemon=True)
        t.start()

    def _worker_summarize_glossary(self, gpath: str) -> None:
        try:
            glossary_data = load_glossary(gpath)
            before_total = sum(len(v) for v in glossary_data.values())
            print(f"Summarizing glossary: {gpath} ({before_total} terms)")
            client = OpenAI(
                base_url=self.base_url_var.get().strip(),
                api_key=self.api_key_var.get().strip(),
            )
            cleaned = summarize_glossary(
                glossary=glossary_data,
                client=client,
                model=self.model_var.get().strip(),
                temperature=0.0,
                no_thinking=self.no_thinking_var.get(),
                debug=self.debug_var.get(),
                max_retries=self.option_vars.get("Max retries", tk.IntVar(value=3)).get(),
            )
            after_total = sum(len(v) for v in cleaned.values())
            with open(gpath, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            print(f"Done. {before_total} → {after_total} terms ({before_total - after_total} removed). Saved to {gpath}")
        except Exception as exc:
            print(f"ERROR: {exc}")
        finally:
            self.root.after(0, self._on_worker_done)

    def _refresh_models(self) -> None:
        try:
            models = fetch_models(self.base_url_var.get())
            self.model_combo["values"] = models
            if models and self.model_var.get() not in models:
                self.model_var.set(models[0])
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
            messagebox.showerror("Missing input", "Please select an input EPUB file or folder.")
            return

        batch_mode = self.batch_mode_var.get()
        if batch_mode:
            if not os.path.isdir(input_path):
                messagebox.showerror("Not a folder", f"Batch mode requires a folder:\n{input_path}")
                return
        else:
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

        kwargs = {
            "input_path": input_path,
            "output_path": self.output_path_var.get().strip() if not batch_mode else "",
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
            "max_retries": self._get_option("Max retries", int),
            "glossary_mode": self.glossary_mode_var.get(),
            "glossary_context_length": self.glossary_context_length_var.get(),
            "glossary_output": self.glossary_output_var.get().strip(),
            "input_glossary": self.input_glossary_var.get().strip(),
            "from_doc": int(self.from_doc_var.get()) if self.from_doc_var.get().strip() else None,
            "to_doc": int(self.to_doc_var.get()) if self.to_doc_var.get().strip() else None,
            "batch_mode": batch_mode,
        }
        self.worker_thread = threading.Thread(
            target=self._worker,
            kwargs=kwargs,
            daemon=True,
        )
        self.worker_thread.start()

    def _stop(self) -> None:
        self.stop_event.set()

    def _worker(self, **kwargs: Any) -> None:
        try:
            level = logging.INFO if self.verbose_var.get() else logging.WARNING
            logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

            client = OpenAI(
                base_url=self.base_url_var.get().strip(),
                api_key=self.api_key_var.get().strip(),
            )

            config = CorrectionConfig(
                temperature=kwargs["temperature"],
                max_segments_per_request=kwargs["max_segments"],
                max_chars_per_request=kwargs["max_chars"],
                similarity_threshold=kwargs["similarity"],
                max_change_ratio=kwargs["max_change"],
                max_context=kwargs["max_context"],
                max_context_chars=kwargs["max_context_chars"],
                max_workers=kwargs["max_workers"],
                max_retries=kwargs["max_retries"],
                no_thinking=self.no_thinking_var.get(),
                debug=self.debug_var.get(),
                use_schema=not self.no_schema_var.get(),
                rewrite=kwargs["rewrite"],
                translate=bool(kwargs["translate"]),
                target_language=kwargs["translate"],
                aggressive=kwargs["aggressive"],
            )

            if kwargs.get("input_glossary"):
                if os.path.isfile(kwargs["input_glossary"]):
                    glossary_data = load_glossary(kwargs["input_glossary"])
                    config.glossary_injection = format_glossary_injection(glossary_data) or None
                    if config.glossary_injection:
                        total = sum(len(v) for v in glossary_data.values())
                        print(f"Loaded glossary: {total} terms from {kwargs['input_glossary']}")
                else:
                    print(f"WARNING: Glossary file not found: {kwargs['input_glossary']}")

            llm = LLMClient(client, self.model_var.get().strip(), config)
            processor = BookProcessor(llm, config)

            if kwargs.get("glossary_mode"):
                stem = os.path.splitext(os.path.basename(kwargs["input_path"]))[0]
                out_path = kwargs.get("glossary_output") or os.path.join("glossaries", f"{stem}_glossary.json")
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                print(f"Extracting glossary from: {kwargs['input_path']}")
                print(f"Output: {out_path}")
                glossary = extract_glossary(
                    input_path=kwargs["input_path"],
                    client=client,
                    model=self.model_var.get().strip(),
                    temperature=config.temperature,
                    context_length=kwargs["glossary_context_length"],
                    no_thinking=config.no_thinking,
                    debug=config.debug,
                    should_stop=self.stop_event.is_set,
                    max_retries=config.max_retries,
                )
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(glossary, f, ensure_ascii=False, indent=2)
                total = sum(len(v) for v in glossary.values())
                print(f"Glossary saved to {out_path} ({total} terms)")
                print("Done.")
                return

            if kwargs.get("batch_mode"):
                processor.process_batch(
                    kwargs["input_path"],
                    review_callback=self.review,
                    auto_accept=self.review_state.auto_accept,
                    conserve_context=kwargs["conserve_context"],
                    should_stop=self.stop_event.is_set,
                    from_doc=kwargs.get("from_doc"),
                    to_doc=kwargs.get("to_doc"),
                )
                return

            checkpoint_path = self.checkpoint_var.get().strip() or None
            processor.process_book(
                input_path=kwargs["input_path"],
                output_path=kwargs["output_path"],
                checkpoint_path=checkpoint_path,
                from_doc=kwargs.get("from_doc"),
                to_doc=kwargs.get("to_doc"),
                report_path=self.report_var.get().strip() or None,
                review_callback=self.review,
                auto_accept=self.review_state.auto_accept,
                conserve_context=kwargs["conserve_context"],
                should_stop=self.stop_event.is_set,
            )
            print("Done.")
        except StopProcessing:
            print("Stopping as requested.")
        except Exception as exc:
            print(f"ERROR: {exc}")
        finally:
            self.root.after(0, self._on_worker_done)

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
