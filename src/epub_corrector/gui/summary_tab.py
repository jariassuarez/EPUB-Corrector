from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk

from epub_corrector.i18n import _
from epub_corrector.summary import summarize_epub

from .base_tab import BaseTab
from .utils import DEFAULT_FONT
from .widgets import FilePickerRow, ScrollableFrame


class SummaryTab(BaseTab):
    """Tab for displaying EPUB summary statistics."""

    def title(self) -> str:
        return _("Summary")

    def build(self, parent: tk.Widget) -> None:
        scrollable = ScrollableFrame(parent)
        scrollable.pack(fill=tk.BOTH, expand=True)
        scrollable_frame = scrollable.inner

        ctk.CTkLabel(scrollable_frame, text=_("File"), font=(*DEFAULT_FONT, "bold")).pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        files_frame = ctk.CTkFrame(scrollable_frame)
        files_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        FilePickerRow(
            files_frame,
            _("Input EPUB"),
            self.input_path_var,
            command=self._browse_input,
            filetypes=[(_("EPUB files"), "*.epub"), (_("All files"), "*.*")],
            row=0,
        )

        ctk.CTkButton(files_frame, text=_("Analyze"), command=self._on_analyze).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(10, 0)
        )

        ctk.CTkLabel(scrollable_frame, text=_("Results"), font=(*DEFAULT_FONT, "bold")).pack(
            anchor="w", padx=20, pady=(10, 0)
        )
        results_frame = ctk.CTkFrame(scrollable_frame)
        results_frame.pack(fill=tk.X, padx=10, pady=5)

        self._result_vars: dict[str, tk.StringVar] = {}
        stats = [
            _("Chapters"),
            _("Total words"),
            _("Total characters"),
            _("Average words per chapter"),
            _("Estimated pages"),
            _("Estimated reading time"),
        ]
        for idx, name in enumerate(stats):
            ctk.CTkLabel(results_frame, text=f"{name}:", font=(*DEFAULT_FONT, "bold")).grid(
                row=idx, column=0, sticky="w", padx=5, pady=3
            )
            var = tk.StringVar(value="-")
            self._result_vars[name] = var
            ctk.CTkLabel(results_frame, textvariable=var, font=DEFAULT_FONT).grid(
                row=idx, column=1, sticky="w", padx=5, pady=3
            )

        results_frame.columnconfigure(1, weight=1)

    def can_start(self) -> bool:
        return False

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.input_path_var.set(path)

    def _on_analyze(self) -> None:
        input_path = self.input_path_var.get().strip()
        if not input_path:
            messagebox.showerror(_("Missing input"), _("Please select an input EPUB file."))
            return
        if not os.path.isfile(input_path):
            messagebox.showerror(_("File not found"), _("Input file not found:\n{}").format(input_path))
            return

        self.app.worker.start(
            lambda: self._run_analysis(input_path),
            on_done=self._on_analysis_done,
        )

    def _run_analysis(self, input_path: str) -> None:
        try:
            print(_("Analyzing: {}").format(input_path))
            summary = summarize_epub(input_path)
            print(summary.format())
            self._last_result = {
                _("Chapters"): str(summary.chapter_count),
                _("Total words"): f"{summary.total_words:,}",
                _("Total characters"): f"{summary.total_chars:,}",
                _("Average words per chapter"): f"{summary.avg_words_per_chapter:,.1f}",
                _("Estimated pages"): f"{summary.estimated_pages:,}",
                _("Estimated reading time"): self._format_time(summary.estimated_reading_time_minutes),
            }
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            self._last_result = {}
            print(_("ERROR: {}").format(exc))

    def _on_analysis_done(self) -> None:
        self.app.set_running(False)
        for name, var in self._result_vars.items():
            var.set(self._last_result.get(name, "-"))

    @staticmethod
    def _format_time(minutes: float) -> str:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        return _("{}h {}m").format(hours, mins) if hours else _("{}m").format(mins)
