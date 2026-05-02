from __future__ import annotations

import gettext
import locale
import os

from epub_corrector.prefs import load_pref

_localedir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "locales")
)
_translation: gettext.GNUTranslations | None = None


def setup_i18n(language: str | None = None) -> None:
    """Install gettext translation for the GUI.

    If *language* is not provided, the saved preference is used, then the
    system default locale (falling back to English).
    """
    global _translation
    if language is None:
        language = load_pref("language")
        if language is None:
            try:
                lang = locale.getdefaultlocale()[0]
                if lang:
                    language = lang.split("_")[0]
                else:
                    language = "en"
            except AttributeError:
                language = "en"

    _translation = gettext.translation(
        "messages",
        localedir=_localedir,
        languages=[language],
        fallback=True,
    )
    _translation.install(names=["gettext", "ngettext"])


def _(message: str) -> str:
    """Translate a string."""
    return _translation.gettext(message) if _translation else message


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a singular/plural string pair."""
    if _translation is None:
        return singular if n == 1 else plural
    return _translation.ngettext(singular, plural, n)
