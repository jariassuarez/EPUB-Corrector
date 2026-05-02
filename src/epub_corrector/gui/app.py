from __future__ import annotations

import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from epub_corrector.i18n import _
from epub_corrector.gui.log_handler import GuiLogHandler, install_tee_stream
from epub_corrector.gui.review_panel import ReviewPanel
from epub_corrector.gui.worker import WorkerController
from epub_corrector.prefs import load_pref, save_pref

if TYPE_CHECKING:
    from epub_corrector.gui.base_tab import BaseTab

from epub_corrector.gui.batch_tab import BatchCorrectionTab
from epub_corrector.gui.debug_tab import DebugTab
from epub_corrector.gui.simple_tab import SimpleCorrectionTab
from epub_corrector.gui.summary_tab import SummaryTab
from epub_corrector.gui.translate_tab import TranslateTab


class EpubCorrectorApp:
    """Main application window managing tabs, worker, and shared UI panels."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        root.title(_("EPUB Corrector"))
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

        self.worker = WorkerController(root)
        self.log_handler = GuiLogHandler()
        self.log_handler.setLevel("DEBUG")
        logging = __import__("logging")
        logging.getLogger().addHandler(self.log_handler)

        self._tee = install_tee_stream(self._on_stdout_write)

        self._build_ui()
        self._poll_review_queue()
        self._poll_log_queue()

    def _build_ui(self) -> None:
        self.tabview = ctk.CTkTabview(self.root)
        self.tabview.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tabview.configure(command=self._on_tab_changed)

        self.tabs: list[BaseTab] = [
            SimpleCorrectionTab(self),
            BatchCorrectionTab(self),
            TranslateTab(self),
            SummaryTab(self),
            DebugTab(self),
        ]

        for tab in self.tabs:
            frame = self.tabview.add(tab.title())
            tab.build(frame)

        bottom_frame = ctk.CTkFrame(self.root)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False)

        controls = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        controls.pack(fill=tk.X, padx=10, pady=5)

        self.status_label = ctk.CTkLabel(controls, text=_("Ready"))
        self.status_label.pack(side=tk.LEFT, padx=5)

        self.start_btn = ctk.CTkButton(controls, text=_("Start"), command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ctk.CTkButton(controls, text=_("Stop"), command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.theme_btn = ctk.CTkButton(controls, text=_("Toggle Theme"), width=100, command=self._toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=5)

        ctk.CTkLabel(controls, text=_("Language") + ":").pack(side=tk.RIGHT, padx=(5, 0))
        self.lang_var = tk.StringVar(value=load_pref("language", "en"))
        lang_combo = ctk.CTkComboBox(
            controls,
            variable=self.lang_var,
            values=["en", "es"],
            width=70,
            state="readonly",
            command=self._on_language_change,
        )
        lang_combo.pack(side=tk.RIGHT, padx=5)

        self.review_panel = ReviewPanel(bottom_frame, on_action=self._on_review_action)
        self.review_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._bind_review_keys()

    def _current_tab(self) -> BaseTab:
        current_name = self.tabview.get()
        for tab in self.tabs:
            if tab.title() == current_name:
                return tab
        return self.tabs[0]

    def _on_tab_changed(self) -> None:
        for tab in self.tabs:
            tab.on_hide()
        self._current_tab().on_show()
        self._update_start_button()

    def _update_start_button(self) -> None:
        if self.worker.is_running():
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
        else:
            self.start_btn.configure(state=tk.NORMAL if self._current_tab().can_start() else tk.DISABLED)
            self.stop_btn.configure(state=tk.DISABLED)

    def set_running(self, running: bool) -> None:
        if running:
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
            self.status_label.configure(text=_("Running..."))
        else:
            self._update_start_button()
            self.status_label.configure(text=_("Ready"))

    def _on_start(self) -> None:
        if self.worker.is_running():
            return
        self._current_tab().on_start()
        if self.worker.is_running():
            self.set_running(True)

    def _on_stop(self) -> None:
        self.worker.stop()
        self._current_tab().on_stop()

    def _on_review_action(self, action: str) -> None:
        if action == "accept_all":
            current = self._current_tab()
            if hasattr(current, "auto_accept_var"):
                current.auto_accept_var.set(True)
                if hasattr(current, "review_state"):
                    current.review_state.auto_accept = True
        self.worker.review.response_queue.put(action)

    def _bind_review_keys(self) -> None:
        for key in ("<Return>", "<n>", "<r>", "<a>", "<Escape>"):
            self.root.bind(key, self._on_review_key)

    def _on_review_key(self, event: Any) -> str | None:
        if self.review_panel.handle_key(event):
            return "break"
        return None

    def _poll_review_queue(self) -> None:
        try:
            while True:
                req = self.worker.review.request_queue.get_nowait()
                self.review_panel.show_review(req["original"], req["proposed"], req["doc_name"])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_review_queue)

    def _on_language_change(self, choice: str) -> None:
        new_lang = self.lang_var.get()
        current = load_pref("language", "en")
        if new_lang == current:
            return
        save_pref("language", new_lang)
        if messagebox.askyesno(
            _("Language"),
            _("Restart required to apply language change.") + "\n" + _("Restart now?"),
        ):
            python = sys.executable
            os.execl(python, python, *sys.argv)

    def _toggle_theme(self) -> None:
        current = ctk.get_appearance_mode()
        new_mode = "Dark" if current == "Light" else "Light"
        ctk.set_appearance_mode(new_mode)
        self._apply_text_colors()

    def _apply_text_colors(self) -> None:
        """Update tk.Text widget colors to match the current theme."""
        bg, fg = __import__("epub_corrector.gui.utils", fromlist=["get_text_colors"]).get_text_colors()
        for tab in self.tabs:
            if hasattr(tab, "log_text"):
                tab.log_text.configure(bg=bg, fg=fg, insertbackground=fg)
            if hasattr(tab, "results_text"):
                tab.results_text.configure(bg=bg, fg=fg, insertbackground=fg)
        self.review_panel.review_orig_text.configure(bg=bg, fg=fg, insertbackground=fg)
        self.review_panel.review_prop_text.configure(bg=bg, fg=fg, insertbackground=fg)

    def _on_stdout_write(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        self.log_handler.emit(
            __import__("logging").LogRecord(
                name="stdout",
                level=__import__("logging").INFO,
                pathname="",
                lineno=0,
                msg=stripped,
                args=(),
                exc_info=None,
            )
        )

    def _poll_log_queue(self) -> None:
        records = self.log_handler.get_records()
        if records:
            debug_tab = self.tabs[4]
            if isinstance(debug_tab, DebugTab):
                for record in records:
                    debug_tab.append(self.log_handler.format(record) + "\n")
        self.root.after(200, self._poll_log_queue)
