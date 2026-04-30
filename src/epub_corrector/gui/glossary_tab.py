from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from openai import OpenAI

from epub_corrector.glossary import extract_glossary, load_glossary, summarize_glossary

from .base_tab import BaseTab
from .widgets import FilePickerRow, ScrollableFrame, ServerConfigFrame


class GlossaryTab(BaseTab):
    """Tab for glossary extraction and summarization."""

    def title(self) -> str:
        return "Glossary"

    def build(self, parent: ttk.Frame) -> None:
        scrollable = ScrollableFrame(parent)
        scrollable.pack(fill=tk.BOTH, expand=True)
        scrollable_frame = scrollable.inner

        self.server_frame = ServerConfigFrame(scrollable_frame)
        self.server_frame.pack(fill=tk.X, padx=10, pady=5)

        extract_frame = ttk.LabelFrame(scrollable_frame, text="Extract Glossary", padding=10)
        extract_frame.pack(fill=tk.X, padx=10, pady=5)

        self.input_path_var = tk.StringVar()
        FilePickerRow(
            extract_frame,
            "Input EPUB",
            self.input_path_var,
            command=self._browse_input,
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")],
            row=0,
        )

        ttk.Label(extract_frame, text="Context length (chars):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.context_length_var = tk.IntVar(value=20000)
        ttk.Entry(extract_frame, textvariable=self.context_length_var, width=12).grid(
            row=1, column=1, sticky="w", padx=5, pady=2
        )

        self.glossary_output_var = tk.StringVar()
        FilePickerRow(
            extract_frame,
            "Save glossary to",
            self.glossary_output_var,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            save_mode=True,
            default_extension=".json",
            row=2,
        )

        self.input_glossary_var = tk.StringVar()
        FilePickerRow(
            extract_frame,
            "Input glossary (optional)",
            self.input_glossary_var,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            row=3,
        )

        summarize_frame = ttk.LabelFrame(scrollable_frame, text="Summarize Glossary", padding=10)
        summarize_frame.pack(fill=tk.X, padx=10, pady=5)

        self.summarize_glossary_var = tk.StringVar()
        FilePickerRow(
            summarize_frame,
            "Glossary file",
            self.summarize_glossary_var,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            row=0,
        )
        ttk.Button(summarize_frame, text="Summarize", command=self._start_summarize).grid(row=0, column=3, padx=5)

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.input_path_var.set(path)
            if not self.glossary_output_var.get():
                stem = os.path.splitext(os.path.basename(path))[0]
                os.makedirs("glossaries", exist_ok=True)
                self.glossary_output_var.set(os.path.join("glossaries", f"{stem}_glossary.json"))

    def on_start(self) -> None:
        input_path = self.input_path_var.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "Please select an input EPUB file.")
            return
        if not os.path.isfile(input_path):
            messagebox.showerror("File not found", f"Input file not found:\n{input_path}")
            return

        out_path = self.glossary_output_var.get().strip()
        if not out_path:
            stem = os.path.splitext(os.path.basename(input_path))[0]
            os.makedirs("glossaries", exist_ok=True)
            out_path = os.path.join("glossaries", f"{stem}_glossary.json")
            self.glossary_output_var.set(out_path)

        server = self.server_frame.get_config()
        kwargs = {
            "input_path": input_path,
            "output_path": out_path,
            "context_length": self.context_length_var.get(),
            "server": server,
            "temperature": 0.0,
            "no_thinking": False,
            "debug": False,
            "max_retries": 3,
        }
        self.app.worker.start(
            lambda: self._run_extract(kwargs),
            on_done=self._on_worker_done,
        )

    def _run_extract(self, kwargs: dict[str, Any]) -> None:
        try:
            client = OpenAI(base_url=kwargs["server"]["base_url"], api_key=kwargs["server"]["api_key"])
            print(f"Extracting glossary from: {kwargs['input_path']}")
            print(f"Output: {kwargs['output_path']}")
            glossary = extract_glossary(
                input_path=kwargs["input_path"],
                client=client,
                model=kwargs["server"]["model"],
                temperature=kwargs["temperature"],
                context_length=kwargs["context_length"],
                no_thinking=kwargs["no_thinking"],
                debug=kwargs["debug"],
                should_stop=self.app.worker.get_stop_check(),
                max_retries=kwargs["max_retries"],
            )
            os.makedirs(os.path.dirname(kwargs["output_path"]) or ".", exist_ok=True)
            with open(kwargs["output_path"], "w", encoding="utf-8") as f:
                json.dump(glossary, f, ensure_ascii=False, indent=2)
            total = sum(len(v) for v in glossary.values())
            print(f"Glossary saved to {kwargs['output_path']} ({total} terms)")
            print("Done.")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            print(f"ERROR: {exc}")

    def _start_summarize(self) -> None:
        gpath = self.summarize_glossary_var.get().strip()
        if not gpath:
            messagebox.showerror("Missing path", "Please select a glossary file to summarize.")
            return
        if not os.path.isfile(gpath):
            messagebox.showerror("File not found", f"Glossary file not found:\n{gpath}")
            return
        server = self.server_frame.get_config()
        self.app.set_running(True)
        self.app.worker.start(
            lambda: self._run_summarize(gpath, server),
            on_done=self._on_worker_done,
        )

    def _run_summarize(self, gpath: str, server: dict[str, str]) -> None:
        try:
            glossary_data = load_glossary(gpath)
            before_total = sum(len(v) for v in glossary_data.values())
            print(f"Summarizing glossary: {gpath} ({before_total} terms)")
            client = OpenAI(base_url=server["base_url"], api_key=server["api_key"])
            cleaned = summarize_glossary(
                glossary=glossary_data,
                client=client,
                model=server["model"],
                temperature=0.0,
                no_thinking=False,
                debug=False,
                max_retries=3,
            )
            after_total = sum(len(v) for v in cleaned.values())
            with open(gpath, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            print(
                f"Done. {before_total} → {after_total} terms ({before_total - after_total} removed). Saved to {gpath}"
            )
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            print(f"ERROR: {exc}")

    def _on_worker_done(self) -> None:
        self.app.set_running(False)
