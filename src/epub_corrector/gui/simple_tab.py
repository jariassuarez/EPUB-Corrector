from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk

from openai import OpenAI

from epub_corrector.config import CorrectionConfig
from epub_corrector.engine import BookProcessor
from epub_corrector.i18n import _, ngettext
from epub_corrector.llm import LLMClient
from epub_corrector.types import ReviewState, StopProcessing

from .base_tab import BaseTab
from .utils import DEFAULT_FONT
from .widgets import CheckboxBar, FilePickerRow, OptionsGrid, ScrollableFrame, ServerConfigFrame


class SimpleCorrectionTab(BaseTab):
    """Tab for single EPUB correction with manual review support."""

    def title(self) -> str:
        return _("Simple Correction")

    def build(self, parent: tk.Widget) -> None:
        scrollable = ScrollableFrame(parent)
        scrollable.pack(fill=tk.BOTH, expand=True)
        scrollable_frame = scrollable.inner

        self.server_frame = ServerConfigFrame(scrollable_frame)
        self.server_frame.pack(fill=tk.X, padx=10, pady=5)

        ctk.CTkLabel(scrollable_frame, text=_("Files"), font=(*DEFAULT_FONT, "bold")).pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        files_frame = ctk.CTkFrame(scrollable_frame)
        files_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.checkpoint_var = tk.StringVar()
        self.report_var = tk.StringVar()

        FilePickerRow(
            files_frame,
            _("Input EPUB"),
            self.input_path_var,
            command=self._browse_input,
            filetypes=[(_("EPUB files"), "*.epub"), (_("All files"), "*.*")],
            row=0,
        )
        FilePickerRow(
            files_frame,
            _("Output EPUB"),
            self.output_path_var,
            filetypes=[(_("EPUB files"), "*.epub"), (_("All files"), "*.*")],
            save_mode=True,
            default_extension=".epub",
            row=1,
        )
        FilePickerRow(
            files_frame,
            _("Checkpoint (optional)"),
            self.checkpoint_var,
            filetypes=[(_("JSON files"), "*.json"), (_("All files"), "*.*")],
            row=2,
        )
        FilePickerRow(
            files_frame,
            _("Report CSV (optional)"),
            self.report_var,
            filetypes=[(_("CSV files"), "*.csv"), (_("All files"), "*.*")],
            save_mode=True,
            default_extension=".csv",
            row=3,
        )

        ctk.CTkLabel(scrollable_frame, text=_("Options"), font=(*DEFAULT_FONT, "bold")).pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        opts_frame = ctk.CTkFrame(scrollable_frame)
        opts_frame.pack(fill=tk.X, padx=10, pady=5)

        self.options = OptionsGrid(
            opts_frame,
            [
                ("Temperature", _("Temperature"), tk.DoubleVar(value=0.0)),
                ("Max segments / request", _("Max segments / request"), tk.IntVar(value=1)),
                ("Max chars / request", _("Max chars / request"), tk.IntVar(value=6000)),
                ("Similarity threshold", _("Similarity threshold"), tk.DoubleVar(value=0.88)),
                ("Max change ratio", _("Max change ratio"), tk.DoubleVar(value=0.20)),
                ("Max context segments", _("Max context segments"), tk.IntVar(value=0)),
                ("Max context chars", _("Max context chars"), tk.IntVar(value=3000)),
                ("Max workers", _("Max workers"), tk.IntVar(value=1)),
                ("Max retries", _("Max retries"), tk.IntVar(value=3)),
            ],
        )

        range_row = 5
        ctk.CTkLabel(opts_frame, text=_("From doc #:"), font=DEFAULT_FONT).grid(
            row=range_row, column=0, sticky="w", padx=5, pady=2
        )
        self.from_doc_var = tk.StringVar()
        ctk.CTkEntry(opts_frame, textvariable=self.from_doc_var, width=60).grid(
            row=range_row, column=1, sticky="w", padx=5, pady=2
        )
        ctk.CTkLabel(opts_frame, text=_("To doc #:"), font=DEFAULT_FONT).grid(
            row=range_row, column=2, sticky="w", padx=5, pady=2
        )
        self.to_doc_var = tk.StringVar()
        ctk.CTkEntry(opts_frame, textvariable=self.to_doc_var, width=60).grid(
            row=range_row, column=3, sticky="w", padx=5, pady=2
        )

        self.auto_accept_var = tk.BooleanVar(value=False)
        self.rewrite_var = tk.BooleanVar(value=False)

        self.auto_accept_var.trace_add("write", self._on_auto_accept_toggle)

        cb_bar = CheckboxBar(
            opts_frame,
            [
                (_("Auto-accept all"), self.auto_accept_var),
                (_("Rewrite"), self.rewrite_var),
            ],
        )
        cb_bar.grid(row=range_row + 1, column=0, columnspan=4, sticky="w", pady=(5, 0))

        self.review_state = ReviewState()

    def _on_auto_accept_toggle(self, *args: Any) -> None:
        self.review_state.auto_accept = self.auto_accept_var.get()

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[(_("EPUB files"), "*.epub"), (_("All files"), "*.*")])
        if path:
            self.input_path_var.set(path)
            if not self.output_path_var.get():
                self.output_path_var.set(os.path.splitext(path)[0] + "_corrected.epub")

    def on_start(self) -> None:
        input_path = self.input_path_var.get().strip()
        if not input_path:
            messagebox.showerror(_("Missing input"), _("Please select an input EPUB file."))
            return
        if not os.path.isfile(input_path):
            messagebox.showerror(_("File not found"), _("Input file not found:\n{}").format(input_path))
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
            "from_doc": from_doc,
            "to_doc": to_doc,
            "rewrite": self.rewrite_var.get(),
            "aggressive": self.rewrite_var.get(),
            "server": server,
        }
        self.app.worker.start(
            lambda: self._run_worker(kwargs),
            on_done=self._on_worker_done,
        )

    def _run_worker(self, kwargs: dict[str, Any]) -> None:
        try:
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

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
                rewrite=kwargs["rewrite"],
                aggressive=kwargs["aggressive"],
            )

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
                should_stop=self.app.worker.get_stop_check(),
            )
            print(_("Done."))
        except StopProcessing:
            print(_("Stopping as requested."))
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            print(_("ERROR: {}").format(exc))

    def _on_worker_done(self) -> None:
        self.app.review_panel.clear_review()
        self.app.set_running(False)
