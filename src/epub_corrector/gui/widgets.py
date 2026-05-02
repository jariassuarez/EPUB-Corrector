from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

if TYPE_CHECKING:
    from collections.abc import Callable

from epub_corrector.i18n import _
from .utils import _DEFAULT_API_KEY, _DEFAULT_BASE_URL, _DEFAULT_MODEL, DEFAULT_FONT, fetch_models


class FilePickerRow:
    """Reusable label + entry + browse button row."""

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None] | None = None,
        filetypes: list[tuple[str, str]] | None = None,
        dir_mode: bool = False,
        save_mode: bool = False,
        default_extension: str = "",
        row: int = 0,
    ) -> None:
        self.variable = variable
        self.filetypes = filetypes or [(_("All files"), "*.*")]
        self.dir_mode = dir_mode
        self.save_mode = save_mode
        self.default_extension = default_extension

        ctk.CTkLabel(parent, text=label + ":", font=DEFAULT_FONT).grid(
            row=row, column=0, sticky="w", padx=5, pady=2
        )
        self.entry = ctk.CTkEntry(parent, textvariable=variable, width=400)
        self.entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
        self.button = ctk.CTkButton(
            parent, text=_("Browse..."), width=80, command=command or self._browse
        )
        self.button.grid(row=row, column=2, padx=5)

        parent.grid_columnconfigure(1, weight=1)

    def _browse(self) -> None:
        if self.dir_mode:
            path = filedialog.askdirectory()
        elif self.save_mode:
            path = filedialog.asksaveasfilename(
                defaultextension=self.default_extension,
                filetypes=self.filetypes,
            )
        else:
            path = filedialog.askopenfilename(filetypes=self.filetypes)
        if path:
            self.variable.set(path)

    def set_state(self, state: str) -> None:
        self.entry.configure(state=state)
        self.button.configure(state=state)


class ServerConfigFrame(ctk.CTkFrame):
    """Reusable server configuration widget."""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)

        self.base_url_var = tk.StringVar(value=_DEFAULT_BASE_URL)
        self.api_key_var = tk.StringVar(value=_DEFAULT_API_KEY)
        self.model_var = tk.StringVar(value=_DEFAULT_MODEL)
        self.show_key_var = tk.BooleanVar(value=False)

        title = ctk.CTkLabel(self, text=_("Server"), font=(*DEFAULT_FONT, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 5))

        ctk.CTkLabel(self, text=_("Base URL:"), font=DEFAULT_FONT).grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        ctk.CTkEntry(self, textvariable=self.base_url_var, width=300).grid(
            row=1, column=1, sticky="ew", padx=5, pady=2
        )

        ctk.CTkLabel(self, text=_("API Key:"), font=DEFAULT_FONT).grid(
            row=2, column=0, sticky="w", padx=5, pady=2
        )
        self.api_key_entry = ctk.CTkEntry(self, textvariable=self.api_key_var, width=300, show="*")
        self.api_key_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkCheckBox(
            self,
            text=_("Show"),
            variable=self.show_key_var,
            command=self._toggle_key_visibility,
            font=DEFAULT_FONT,
        ).grid(row=2, column=2, sticky="w")

        ctk.CTkLabel(self, text=_("Model:"), font=DEFAULT_FONT).grid(
            row=3, column=0, sticky="w", padx=5, pady=2
        )
        self.model_combo = ctk.CTkComboBox(self, variable=self.model_var, width=280, values=[])
        self.model_combo.grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkButton(self, text=_("Refresh Models"), command=self._refresh_models).grid(row=3, column=2, padx=5)

        self.columnconfigure(1, weight=1)

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.configure(show="" if self.show_key_var.get() else "*")

    def _refresh_models(self) -> None:
        try:
            models = fetch_models(self.base_url_var.get())
            self.model_combo.configure(values=models)
            if models and self.model_var.get() not in models:
                self.model_var.set(models[0])
        except RuntimeError as exc:
            messagebox.showerror(_("Error"), str(exc))

    def get_config(self) -> dict[str, str]:
        return {
            "base_url": self.base_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "model": self.model_var.get().strip(),
        }


class OptionsGrid:
    """Grid of labeled numeric entries."""

    def __init__(
        self, parent: tk.Widget, options: list[tuple[str, str, tk.Variable]], columns: int = 2
    ) -> None:
        self.parent = parent
        self.option_vars: dict[str, tk.Variable] = {}
        self.option_labels: dict[str, str] = {}
        for i, (key, label, var) in enumerate(options):
            row = i // columns
            col = (i % columns) * 2
            ctk.CTkLabel(parent, text=label + ":", font=DEFAULT_FONT).grid(
                row=row, column=col, sticky="w", padx=5, pady=2
            )
            ctk.CTkEntry(parent, textvariable=var, width=80).grid(
                row=row, column=col + 1, sticky="w", padx=5, pady=2
            )
            self.option_vars[key] = var
            self.option_labels[key] = label

    def get(self, key: str, type_: type) -> Any:
        var = self.option_vars[key]
        try:
            return type_(var.get())
        except tk.TclError:
            messagebox.showerror(
                _("Invalid input"), _("{} must be a valid number.").format(self.option_labels[key])
            )
            raise ValueError from None


class CheckboxBar:
    """Horizontal row of checkboxes."""

    def __init__(self, parent: tk.Widget, items: list[tuple[str, tk.BooleanVar]]) -> None:
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.vars: dict[str, tk.BooleanVar] = {}
        for text, var in items:
            ctk.CTkCheckBox(
                self.frame, text=text, variable=var, font=DEFAULT_FONT
            ).pack(side=tk.LEFT, padx=5)
            self.vars[text] = var

    def grid(self, **kwargs: Any) -> None:
        self.frame.grid(**kwargs)

    def pack(self, **kwargs: Any) -> None:
        self.frame.pack(**kwargs)


class ScrollableFrame(ctk.CTkScrollableFrame):
    """A scrollable frame using customtkinter's built-in scrollable frame."""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)

    @property
    def inner(self) -> ctk.CTkScrollableFrame:
        """Return the scrollable content frame (self for CTkScrollableFrame)."""
        return self
