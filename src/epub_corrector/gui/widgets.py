from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from .utils import _DEFAULT_API_KEY, _DEFAULT_BASE_URL, _DEFAULT_MODEL, fetch_models


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
        self.filetypes = filetypes or [("All files", "*.*")]
        self.dir_mode = dir_mode
        self.save_mode = save_mode
        self.default_extension = default_extension

        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="w", padx=5, pady=2)
        self.entry = ttk.Entry(parent, textvariable=variable, width=60)
        self.entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
        self.button = ttk.Button(parent, text="Browse...", command=command or self._browse)
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
        self.entry.config(state=state)
        self.button.config(state=state)


class ServerConfigFrame(ttk.LabelFrame):
    """Reusable server configuration widget."""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, text="Server", padding=10, **kwargs)

        self.base_url_var = tk.StringVar(value=_DEFAULT_BASE_URL)
        self.api_key_var = tk.StringVar(value=_DEFAULT_API_KEY)
        self.model_var = tk.StringVar(value=_DEFAULT_MODEL)
        self.show_key_var = tk.BooleanVar(value=False)

        ttk.Label(self, text="Base URL:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(self, textvariable=self.base_url_var, width=50).grid(
            row=0, column=1, sticky="ew", padx=5, pady=2
        )

        ttk.Label(self, text="API Key:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.api_key_entry = ttk.Entry(self, textvariable=self.api_key_var, width=50, show="*")
        self.api_key_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ttk.Checkbutton(
            self, text="Show", variable=self.show_key_var, command=self._toggle_key_visibility
        ).grid(row=1, column=2, sticky="w")

        ttk.Label(self, text="Model:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.model_combo = ttk.Combobox(self, textvariable=self.model_var, width=48)
        self.model_combo.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(self, text="Refresh Models", command=self._refresh_models).grid(row=2, column=2, padx=5)

        self.columnconfigure(1, weight=1)

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.config(show="" if self.show_key_var.get() else "*")

    def _refresh_models(self) -> None:
        try:
            models = fetch_models(self.base_url_var.get())
            self.model_combo["values"] = models
            if models and self.model_var.get() not in models:
                self.model_var.set(models[0])
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))

    def get_config(self) -> dict[str, str]:
        return {
            "base_url": self.base_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "model": self.model_var.get().strip(),
        }


class OptionsGrid:
    """Grid of labeled numeric entries."""

    def __init__(self, parent: tk.Widget, options: list[tuple[str, tk.Variable]], columns: int = 2) -> None:
        self.parent = parent
        self.option_vars: dict[str, tk.Variable] = {}
        for i, (label, var) in enumerate(options):
            row = i // columns
            col = (i % columns) * 2
            ttk.Label(parent, text=label + ":").grid(row=row, column=col, sticky="w", padx=5, pady=2)
            ttk.Entry(parent, textvariable=var, width=12).grid(row=row, column=col + 1, sticky="w", padx=5, pady=2)
            self.option_vars[label] = var

    def get(self, label: str, type_: type) -> Any:
        var = self.option_vars[label]
        try:
            return type_(var.get())
        except tk.TclError:
            messagebox.showerror("Invalid input", f"{label} must be a valid number.")
            raise ValueError from None


class CheckboxBar:
    """Horizontal row of checkboxes."""

    def __init__(self, parent: tk.Widget, items: list[tuple[str, tk.BooleanVar]]) -> None:
        self.frame = ttk.Frame(parent)
        self.vars: dict[str, tk.BooleanVar] = {}
        for text, var in items:
            ttk.Checkbutton(self.frame, text=text, variable=var).pack(side=tk.LEFT, padx=5)
            self.vars[text] = var

    def grid(self, **kwargs: Any) -> None:
        self.frame.grid(**kwargs)

    def pack(self, **kwargs: Any) -> None:
        self.frame.pack(**kwargs)


class ScrollableFrame(ttk.Frame):
    """A scrollable frame using a Canvas and scrollbar."""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._window_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfig(self._window_id, width=event.width)

    @property
    def inner(self) -> ttk.Frame:
        return self.scrollable_frame
