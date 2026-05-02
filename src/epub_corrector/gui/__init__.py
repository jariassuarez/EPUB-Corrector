from __future__ import annotations

import customtkinter as ctk

from epub_corrector.i18n import setup_i18n
from .app import EpubCorrectorApp


def main() -> None:
    setup_i18n()
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    EpubCorrectorApp(root)
    root.mainloop()
