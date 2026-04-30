from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

from .base_tab import BaseTab


class DebugTab(BaseTab):
    """Tab for viewing application logs."""

    def title(self) -> str:
        return "Debug"

    def build(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(controls, text="Log level:").pack(side=tk.LEFT, padx=(0, 5))
        self.level_var = tk.StringVar(value="INFO")
        self.level_combo = ttk.Combobox(
            controls, textvariable=self.level_var, values=["DEBUG", "INFO", "WARNING", "ERROR"], width=12, state="readonly"
        )
        self.level_combo.pack(side=tk.LEFT, padx=5)
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_change)

        ttk.Button(controls, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=5)

        self.log_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Courier New", 9),
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            relief=tk.SUNKEN,
            borderwidth=1,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _on_level_change(self, *args: Any) -> None:
        level = getattr(logging, self.level_var.get(), logging.INFO)
        logging.getLogger().setLevel(level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)

    def _clear(self) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)
        if self.app.log_handler:
            self.app.log_handler.clear()

    def append(self, text: str) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
