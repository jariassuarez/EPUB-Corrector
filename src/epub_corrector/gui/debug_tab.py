from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

import customtkinter as ctk

from epub_corrector.i18n import _

from .base_tab import BaseTab
from .utils import DEFAULT_FONT, get_text_colors


class DebugTab(BaseTab):
    """Tab for viewing application logs."""

    def title(self) -> str:
        return _("Debug")

    def build(self, parent: tk.Widget) -> None:
        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill=tk.X, padx=10, pady=5)

        ctk.CTkLabel(controls, text=_("Log level:"), font=DEFAULT_FONT).pack(side=tk.LEFT, padx=(0, 5))
        self.level_var = tk.StringVar(value="INFO")
        self.level_combo = ctk.CTkComboBox(
            controls,
            variable=self.level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            width=100,
            state="readonly",
            command=self._on_level_change,
        )
        self.level_combo.pack(side=tk.LEFT, padx=5)

        ctk.CTkButton(controls, text=_("Clear"), command=self._clear).pack(side=tk.LEFT, padx=5)

        bg, fg = get_text_colors()
        self.log_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Courier New", 9),
            state=tk.DISABLED,
            bg=bg,
            fg=fg,
            insertbackground=fg,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ctk.CTkScrollbar(self.log_text, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _on_level_change(self, choice: str) -> None:
        level = getattr(logging, self.level_var.get(), logging.INFO)
        logging.getLogger().setLevel(level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)

    def _clear(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        if self.app.log_handler:
            self.app.log_handler.clear()

    def append(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
