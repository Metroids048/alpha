"""Atomic, idempotent projection of candidates eligible for guarded submission."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Mapping


class ReadyAlphaCsvStore:
    def __init__(self, path: str | Path = "待提交Alpha列表.csv") -> None:
        self.path = Path(path)

    def read_ready(self) -> list[dict[str, str]]:
        if not self.path.is_file():
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {key: value for key, value in row.items() if value not in (None, "")}
                for row in csv.DictReader(handle)
                if str(row.get("quality_status") or "") == "READY_TO_SUBMIT"
                and str(row.get("alpha_id") or "").strip()
            ]

    def upsert(self, row: Mapping[str, object]) -> bool:
        normalized = {str(key): str(value) for key, value in row.items() if value is not None}
        if normalized.get("quality_status") != "READY_TO_SUBMIT" or not normalized.get("alpha_id"):
            return False
        if not normalized.get("exact_hash"):
            return False
        rows = self.read_ready()
        identity = (normalized["alpha_id"], normalized["exact_hash"])
        if any((item.get("alpha_id"), item.get("exact_hash")) == identity for item in rows):
            return False
        rows.append(normalized)
        fields = list(dict.fromkeys(key for item in rows for key in item))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, self.path)
        return True
