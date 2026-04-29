from __future__ import annotations

import csv
import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import ChangeRecord


def write_csv_report(records: list[ChangeRecord], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["document", "status", "original", "proposed"])
        for r in records:
            writer.writerow(
                [
                    r.doc_name,
                    "accepted" if r.accepted else "rejected",
                    r.original,
                    r.proposed,
                ]
            )


def load_checkpoint(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        processed = data.get("processed", {})
        if not isinstance(processed, dict):
            return {}
        return {str(k): str(v) for k, v in processed.items()}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def save_checkpoint(path: str, processed: dict[str, str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"processed": processed}, f, ensure_ascii=False)
    os.replace(tmp, path)
