from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from openai import OpenAI

from epub_corrector.config import CorrectionConfig
from epub_corrector.engine import BookProcessor
from epub_corrector.glossary import format_glossary_injection, load_glossary
from epub_corrector.llm import LLMClient
from epub_corrector.types import ReviewState, StopProcessing

from .base_tab import BaseTab
from .widgets import CheckboxBar, FilePickerRow, OptionsGrid, ScrollableFrame, ServerConfigFrame


class SimpleCorrectionTab(BaseTab):
    """Tab for single EPUB correction with manual review support."""

    def title(self) -> str:
        return "Simple Correction"

    def build(self, parent: ttk.Frame) -> None:
        scrollable = ScrollableFrame(parent)
        scrollable.pack(fill=tk.BOTH, expand=True)
        scrollable_frame = scrollable.inner

        self.server_frame = ServerConfigFrame(scrollable_frame)
        self.server_frame.pack(fill=tk.X, padx=10, pady=5)

        files_frame = ttk.LabelFrame(scrollable_frame, text="Files", padding=10)
        files_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.checkpoint_var = tk.StringVar()
        self.report_var = tk.StringVar()

        FilePickerRow(
            files_frame,
            "Input EPUB",
            self.input_path_var,
            command=self._browse_input,
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")],
            row=0,
        )
        FilePickerRow(
            files_frame,
            "Output EPUB",
            self.output_path_var,
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")],
            save_mode=True,
            default_extension=".epub",
            row=1,
        )
        FilePickerRow(
            files_frame,
            "Checkpoint (optional)",
            self.checkpoint_var,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            row=2,
        )
        FilePickerRow(
            files_frame,
            "Report CSV (optional)",
            self.report_var,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            save_mode=True,
            default_extension=".csv",
            row=3,
        )
        self.input_glossary_var = tk.StringVar()
        FilePickerRow(
            files_frame,
            "Input glossary (optional)",
            self.input_glossary_var,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            row=4,
        )

        opts_frame = ttk.LabelFrame(scrollable_frame, text="Options", padding=10)
        opts_frame.pack(fill=tk.X, padx=10, pady=5)

        self.options = OptionsGrid(
            opts_frame,
            [
                ("Temperature", tk.DoubleVar(value=0.0)),
                ("Max segments / request", tk.IntVar(value=1)),
                ("Max chars / request", tk.IntVar(value=6000)),
                ("Similarity threshold", tk.DoubleVar(value=0.88)),
                ("Max change ratio", tk.DoubleVar(value=0.20)),
                ("Max context segments", tk.IntVar(value=0)),
                ("Max context chars", tk.IntVar(value=3000)),
                ("Max workers", tk.IntVar(value=1)),
                ("Max retries", tk.IntVar(value=3)),
            ],
        )

        translate_row = 5
        ttk.Label(opts_frame, text="Translate to:").grid(row=translate_row, column=0, sticky="w", padx=5, pady=2)
        self.translate_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.translate_var, width=30).grid(
            row=translate_row, column=1, columnspan=3, sticky="w", padx=5, pady=2
        )

        range_row = translate_row + 1
        ttk.Label(opts_frame, text="From doc #:").grid(row=range_row, column=0, sticky="w", padx=5, pady=2)
        self.from_doc_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.from_doc_var, width=8).grid(
            row=range_row, column=1, sticky="w", padx=5, pady=2
        )
        ttk.Label(opts_frame, text="To doc #:").grid(row=range_row, column=2, sticky="w", padx=5, pady=2)
        self.to_doc_var = tk.StringVar()
        ttk.Entry(opts_frame, textvariable=self.to_doc_var, width=8).grid(
            row=range_row, column=3, sticky="w", padx=5, pady=2
        )

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

        cb_bar = CheckboxBar(
            opts_frame,
            [
                ("No thinking", self.no_thinking_var),
                ("No schema", self.no_schema_var),
                ("Debug", self.debug_var),
                ("Verbose", self.verbose_var),
                ("Auto-accept all", self.auto_accept_var),
                ("Conserve context", self.conserve_context_var),
                ("Rewrite", self.rewrite_var),
                ("Aggressive", self.aggressive_var),
            ],
        )
        cb_bar.grid(row=range_row + 1, column=0, columnspan=4, sticky="w", pady=(5, 0))

        self.review_state = ReviewState()

    def _on_aggressive_toggle(self, *args: Any) -> None:
        if self.aggressive_var.get():
            self.rewrite_var.set(True)

    def _on_auto_accept_toggle(self, *args: Any) -> None:
        self.review_state.auto_accept = self.auto_accept_var.get()

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.input_path_var.set(path)
            if not self.output_path_var.get():
                self.output_path_var.set(os.path.splitext(path)[0] + "_corrected.epub")

    def on_start(self) -> None:
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
            temperature = self.options.get("Temperature", float)
            max_segments = self.options.get("Max segments / request", int)
            max_chars = self.options.get("Max chars / request", int)
            similarity = self.options.get("Similarity threshold", float)
            max_change = self.options.get("Max change ratio", float)
            max_context = self.options.get("Max context segments", int)
            max_context_chars = self.options.get("Max context chars", int)
            max_workers = self.options.get("Max workers", int)
            max_retries = self.options.get("Max retries", int)
        except ValueError:
            return

        translate_lang = self.translate_var.get().strip() or None
        from_doc = int(self.from_doc_var.get()) if self.from_doc_var.get().strip() else None
        to_doc = int(self.to_doc_var.get()) if self.to_doc_var.get().strip() else None
        checkpoint_path = self.checkpoint_var.get().strip() or None
        report_path = self.report_var.get().strip() or None

        server = self.server_frame.get_config()

        kwargs = {
            "input_path": input_path,
            "output_path": output_path,
            "checkpoint_path": checkpoint_path,
            "report_path": report_path,
            "temperature": temperature,
            "max_segments": max_segments,
            "max_chars": max_chars,
            "similarity": similarity,
            "max_change": max_change,
            "max_context": max_context,
            "max_context_chars": max_context_chars,
            "max_workers": max_workers,
            "max_retries": max_retries,
            "translate": translate_lang,
            "from_doc": from_doc,
            "to_doc": to_doc,
            "conserve_context": self.conserve_context_var.get(),
            "rewrite": self.rewrite_var.get(),
            "aggressive": self.aggressive_var.get(),
            "server": server,
            "no_thinking": self.no_thinking_var.get(),
            "debug": self.debug_var.get(),
            "no_schema": self.no_schema_var.get(),
            "verbose": self.verbose_var.get(),
            "input_glossary": self.input_glossary_var.get().strip(),
        }
        self.app.worker.start(
            lambda: self._run_worker(kwargs),
            on_done=self._on_worker_done,
        )

    def _run_worker(self, kwargs: dict[str, Any]) -> None:
        try:
            level = logging.INFO if kwargs["verbose"] else logging.WARNING
            logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

            client = OpenAI(base_url=kwargs["server"]["base_url"], api_key=kwargs["server"]["api_key"])

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
                no_thinking=kwargs["no_thinking"],
                debug=kwargs["debug"],
                use_schema=not kwargs["no_schema"],
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

            llm = LLMClient(client, kwargs["server"]["model"], config)
            processor = BookProcessor(llm, config)

            processor.process_book(
                input_path=kwargs["input_path"],
                output_path=kwargs["output_path"],
                checkpoint_path=kwargs["checkpoint_path"],
                from_doc=kwargs["from_doc"],
                to_doc=kwargs["to_doc"],
                report_path=kwargs["report_path"],
                review_callback=self.app.worker.review,
                auto_accept=self.review_state.auto_accept,
                conserve_context=kwargs["conserve_context"],
                should_stop=self.app.worker.get_stop_check(),
            )
            print("Done.")
        except StopProcessing:
            print("Stopping as requested.")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            print(f"ERROR: {exc}")

    def _on_worker_done(self) -> None:
        self.app.review_panel.clear_review()
        self.app.set_running(False)
