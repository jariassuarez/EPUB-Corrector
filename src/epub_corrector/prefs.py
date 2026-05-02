from __future__ import annotations

import json
import os
import platform


def _get_config_dir() -> str:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = os.path.join(base, "epub-corrector")
    os.makedirs(path, exist_ok=True)
    return path


_PREFS_PATH = os.path.join(_get_config_dir(), "gui_prefs.json")


def load_pref(key: str, default: str | None = None) -> str | None:
    if os.path.exists(_PREFS_PATH):
        with open(_PREFS_PATH, encoding="utf-8") as f:
            return json.load(f).get(key, default)
    return default


def save_pref(key: str, value: str) -> None:
    prefs: dict[str, str] = {}
    if os.path.exists(_PREFS_PATH):
        with open(_PREFS_PATH, encoding="utf-8") as f:
            prefs = json.load(f)
    prefs[key] = value
    with open(_PREFS_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)
