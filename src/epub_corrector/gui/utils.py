from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen

import customtkinter as ctk
from dotenv import load_dotenv

from epub_corrector.i18n import _

load_dotenv()

_DEFAULT_BASE_URL = os.environ.get("EPUB_CORRECTOR_BASE_URL", "http://localhost:1234/v1")
_DEFAULT_API_KEY = os.environ.get("EPUB_CORRECTOR_API_KEY", "lm-studio")
_DEFAULT_MODEL = os.environ.get("EPUB_CORRECTOR_MODEL", "local-model")

DEFAULT_FONT = ("TkDefaultFont", 10)


def fetch_models(base_url: str) -> list[str]:
    """Return model IDs from the OpenAI-compatible /models endpoint."""
    url = base_url.rstrip("/") + "/models"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = []
        for item in data.get("data", []):
            mid = item.get("id")
            if mid:
                models.append(mid)
        return models
    except (URLError, json.JSONDecodeError, TimeoutError) as exc:
        raise RuntimeError(_("Failed to fetch models: {}").format(exc)) from exc


def get_text_colors() -> tuple[str, str]:
    """Return (background, foreground) for tk.Text widgets based on current theme."""
    if ctk.get_appearance_mode() == "Dark":
        return ("#2b2b2b", "#d4d4d4")
    return ("#fdfdfd", "#1a1a1a")
