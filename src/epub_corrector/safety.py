from __future__ import annotations

import difflib

BOUNDARY_PUNCT = frozenset("\"\"''-–—…")


def change_is_safe(original: str, proposed: str, similarity_threshold: float, max_change_ratio: float) -> bool:
    if original == proposed:
        return True
    if original.strip() and not proposed.strip():
        return False
    similarity = difflib.SequenceMatcher(a=original, b=proposed).ratio()
    change_ratio = 1.0 - similarity
    if similarity < similarity_threshold:
        return False
    return change_ratio <= max_change_ratio


def restore_boundary_punctuation(original: str, corrected: str) -> str:
    """Re-instate leading/trailing quotes, dashes, or ellipsis the model may have stripped."""
    out = corrected
    if original and out and original[0] in BOUNDARY_PUNCT and out[0] != original[0]:
        out = original[0] + out
    if original and out and original[-1] in BOUNDARY_PUNCT and out[-1] != original[-1]:
        out = out + original[-1]
    return out
