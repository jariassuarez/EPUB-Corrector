from __future__ import annotations

import tkinter as tk

from .app import EpubCorrectorApp


def main() -> None:
    root = tk.Tk()
    EpubCorrectorApp(root)
    root.mainloop()
