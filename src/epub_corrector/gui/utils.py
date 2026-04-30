from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

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
        raise RuntimeError(f"Failed to fetch models: {exc}") from exc
