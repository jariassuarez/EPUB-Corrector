from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, cast

from epub_corrector.gui.log_handler import GuiLogHandler, install_tee_stream
from epub_corrector.gui.review_panel import ReviewPanel
from epub_corrector.gui.worker import WorkerController

if TYPE_CHECKING:
    from epub_corrector.gui.base_tab import BaseTab

from epub_corrector.gui.batch_tab import BatchCorrectionTab
from epub_corrector.gui.debug_tab import DebugTab
from epub_corrector.gui.glossary_tab import GlossaryTab
from epub_corrector.gui.simple_tab import SimpleCorrectionTab


class EpubCorrectorApp:
    """Main application window managing tabs, worker, and shared UI panels."""

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
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.tabs: list[BaseTab] = [
            SimpleCorrectionTab(self),
            BatchCorrectionTab(self),
            GlossaryTab(self),
            DebugTab(self),
        ]

        self._tab_frames: dict[str, tk.Widget] = {}
        for tab in self.tabs:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=tab.title())
            tab.build(frame)
            self._tab_frames[tab.title()] = frame

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False)

        controls = ttk.Frame(bottom_frame)
        controls.pack(fill=tk.X, padx=10, pady=5)

        self.status_label = ttk.Label(controls, text="Ready")
        self.status_label.pack(side=tk.LEFT, padx=5)

        self.start_btn = ttk.Button(controls, text="Start", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.review_panel = ReviewPanel(bottom_frame, on_action=self._on_review_action)
        self.review_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._bind_review_keys()

    def _current_tab(self) -> BaseTab:
        idx = cast("int", self.notebook.index(self.notebook.select()))
        return self.tabs[idx]

    def _on_tab_changed(self, event: Any) -> None:
        for tab in self.tabs:
            tab.on_hide()
        self._current_tab().on_show()
        self._update_start_button()

    def _update_start_button(self) -> None:
        if self.worker.is_running():
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.NORMAL if self._current_tab().can_start() else tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)

    def set_running(self, running: bool) -> None:
        if running:
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
            self.status_label.config(text="Running...")
        else:
            self._update_start_button()
            self.status_label.config(text="Ready")

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
            debug_tab = self.tabs[3]
            if isinstance(debug_tab, DebugTab):
                for record in records:
                    debug_tab.append(self.log_handler.format(record) + "\n")
        self.root.after(200, self._poll_log_queue)
