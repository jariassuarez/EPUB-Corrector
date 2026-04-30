from __future__ import annotations

import difflib
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from epub_corrector.gui.utils import DEFAULT_FONT


class ReviewPanel:
    """Reusable side-by-side diff review widget."""

    def __init__(self, parent: tk.Widget, on_action: Callable[[str], None]) -> None:
        self.on_action = on_action
        self._review_pending = False

        self.frame = ttk.LabelFrame(parent, text="Review Change", padding=10)

        self.review_doc_label = ttk.Label(
            self.frame, text="No pending review.", font=("TkDefaultFont", 10, "bold")
        )
        self.review_doc_label.pack(pady=(5, 10), padx=10, anchor="w")

        paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_frame = ttk.LabelFrame(paned, text="Original", padding=5)
        right_frame = ttk.LabelFrame(paned, text="Proposed", padding=5)
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=1)

        self.review_orig_text = tk.Text(
            left_frame,
            wrap=tk.WORD,
            font=DEFAULT_FONT,
            state=tk.DISABLED,
            bg="#fdfdfd",
            relief=tk.SUNKEN,
            borderwidth=1,
            height=10,
        )
        self.review_prop_text = tk.Text(
            right_frame,
            wrap=tk.WORD,
            font=DEFAULT_FONT,
            state=tk.DISABLED,
            bg="#fdfdfd",
            relief=tk.SUNKEN,
            borderwidth=1,
            height=10,
        )
        self.review_orig_text.pack(fill=tk.BOTH, expand=True)
        self.review_prop_text.pack(fill=tk.BOTH, expand=True)

        for txt, tag_name, bg in (
            (self.review_orig_text, "del", "#ffcccc"),
            (self.review_prop_text, "ins", "#ccffcc"),
        ):
            txt.tag_config(tag_name, background=bg)

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(pady=(10, 5))

        self.review_accept_btn = ttk.Button(
            btn_frame,
            text="Accept (Enter)",
            command=lambda: self._do_action("accept"),
        )
        self.review_accept_btn.pack(side=tk.LEFT, padx=5)
        self.review_skip_btn = ttk.Button(
            btn_frame,
            text="Skip (n)",
            command=lambda: self._do_action("reject"),
        )
        self.review_skip_btn.pack(side=tk.LEFT, padx=5)
        self.review_retry_btn = ttk.Button(
            btn_frame,
            text="Retry (r)",
            command=lambda: self._do_action("retry"),
        )
        self.review_retry_btn.pack(side=tk.LEFT, padx=5)
        self.review_accept_all_btn = ttk.Button(
            btn_frame,
            text="Accept All (a)",
            command=lambda: self._do_action("accept_all"),
        )
        self.review_accept_all_btn.pack(side=tk.LEFT, padx=5)

        self._clear()

    def pack(self, **kwargs: Any) -> None:
        self.frame.pack(**kwargs)

    def _clear(self) -> None:
        self._review_pending = False
        self.review_doc_label.config(text="No pending review.")
        for widget in (self.review_orig_text, self.review_prop_text):
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, "Waiting for next change...")
            widget.config(state=tk.DISABLED)
        for btn in (self.review_accept_btn, self.review_skip_btn, self.review_retry_btn, self.review_accept_all_btn):
            btn.config(state=tk.DISABLED)

    def show_review(self, original: str, proposed: str, doc_name: str) -> None:
        self._review_pending = True
        self.review_doc_label.config(text=f"Document: {doc_name}")
        self._fill_diff(self.review_orig_text, original, proposed, is_original=True)
        self._fill_diff(self.review_prop_text, original, proposed, is_original=False)
        for btn in (self.review_accept_btn, self.review_skip_btn, self.review_retry_btn, self.review_accept_all_btn):
            btn.config(state=tk.NORMAL)
        self.frame.focus_set()

    def clear_review(self) -> None:
        self._clear()

    def is_pending(self) -> bool:
        return self._review_pending

    def handle_key(self, event: Any) -> bool:
        """Handle a keyboard event. Returns True if the event was consumed."""
        if not self._review_pending:
            return False
        focus = self.frame.winfo_toplevel().focus_get()
        if focus is not None:
            w: tk.Misc | None = focus
            while w is not None:
                if w == self.frame:
                    break
                try:
                    w = w.master
                except AttributeError:
                    w = None
            else:
                return False
        key = event.keysym
        if key in ("Return",):
            self._do_action("accept")
        elif key == "n":
            self._do_action("reject")
        elif key == "r":
            self._do_action("retry")
        elif key == "a":
            self._do_action("accept_all")
        elif key == "Escape":
            self._do_action("reject")
        else:
            return False
        return True

    def _do_action(self, action: str) -> None:
        if not self._review_pending:
            return
        self.on_action(action)
        self._clear()

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
