"""Atomic, idempotent projection of candidates eligible for guarded submission."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Mapping


class ReadyAlphaCsvStore:
    FIELDS = (
        "alpha_id", "candidate_id", "exact_hash", "expression", "research_family",
        "strategy_family", "source", "dataset", "generator_source", "settings_json",
        "sharpe", "fitness", "turnover", "self_correlation", "prod_correlation",
        "checks_json", "quality_status", "quality_reasons_json", "request_hash", "simulated_at",
    )
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
        normalized = self._normalise(row)
        if normalized is None:
            return False
        rows = self.read_ready()
        identity = (normalized["alpha_id"], normalized["exact_hash"])
        if any((item.get("alpha_id"), item.get("exact_hash")) == identity for item in rows):
            return False
        rows.append(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows([{field: item.get(field, "") for field in self.FIELDS} for item in rows])
        os.replace(temporary, self.path)
        return True

    def _normalise(self, row: Mapping[str, object]) -> dict[str, str] | None:
        values = {str(key): value for key, value in row.items() if value is not None}
        required = ("alpha_id", "exact_hash", "expression", "request_hash")
        if str(values.get("quality_status") or "") != "READY_TO_SUBMIT":
            return None
        if any(not str(values.get(key) or "").strip() for key in required):
            return None
        try:
            sharpe = float(values["sharpe"])
            fitness = float(values["fitness"])
            turnover = float(values["turnover"])
            checks = values["checks_json"]
            json.loads(checks if isinstance(checks, str) else json.dumps(checks))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if sharpe < 1.57 or fitness <= 1.0 or not 0.01 <= turnover <= 0.70:
            return None
        normalized = {field: "" for field in self.FIELDS}
        for key, value in values.items():
            if key in normalized:
                if key in {"settings_json", "checks_json", "quality_reasons_json"} and not isinstance(value, str):
                    normalized[key] = json.dumps(value, sort_keys=True)
                else:
                    normalized[key] = str(value)
        return normalized
