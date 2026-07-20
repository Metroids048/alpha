"""Persistent dynamic platform thresholds with scope-aware lookup."""

from __future__ import annotations

import json
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .check_parser import GateObservation
from .check_parser import parse_gate_observations


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class GateScope:
    region: str = "*"
    universe: str = "*"
    delay: int | str = "*"
    alpha_type: str = "*"
    theme_id: str = "*"
    pyramid_id: str = "*"

    def values(self) -> tuple[str, ...]:
        return tuple(
            str(value).upper() if index < 4 else str(value)
            for index, value in enumerate(
                (
                    self.region,
                    self.universe,
                    self.delay,
                    self.alpha_type,
                    self.theme_id,
                    self.pyramid_id,
                )
            )
        )


@dataclass(frozen=True)
class GateSnapshot:
    snapshot_key: str
    gate_name: str
    limit: float
    direction: str
    scope: GateScope
    first_seen_at: str
    last_seen_at: str
    observation_count: int
    source: str
    raw_payload_hash: str
    version: int


class MissingGateSnapshot(LookupError):
    pass


class StaleGateSnapshot(RuntimeError):
    pass


class GateRegistry:
    def __init__(self, database: str | Path, *, freshness_hours: float = 24.0) -> None:
        self.database = Path(database)
        self.freshness_hours = float(freshness_hours)

    @staticmethod
    def _snapshot_key(observation: GateObservation) -> str:
        values = (
            observation.gate_name,
            observation.region,
            observation.universe,
            observation.delay,
            observation.alpha_type,
            observation.theme_id,
            observation.pyramid_id,
        )
        return "|".join(values)

    def record(self, observation: GateObservation) -> None:
        self.record_many([observation])

    def record_many(self, observations: Iterable[GateObservation]) -> int:
        rows = list(observations)
        if not rows:
            return 0
        inserted = 0
        with sqlite3.connect(self.database) as con:
            for item in rows:
                cursor = con.execute(
                    """INSERT OR IGNORE INTO platform_gate_observations
                    (observation_id,gate_name,result,limit_value,observed_value,message,direction,region,universe_name,delay,alpha_type,theme_id,pyramid_id,source_alpha_id,observed_at,ingested_at,raw_payload_hash,source,timestamp_source,freshness_eligible)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.observation_id,
                        item.gate_name,
                        item.result,
                        item.limit,
                        item.value,
                        item.message,
                        item.direction,
                        item.region,
                        item.universe,
                        item.delay,
                        item.alpha_type,
                        item.theme_id,
                        item.pyramid_id,
                        item.source_alpha_id,
                        item.observed_at,
                        item.ingested_at,
                        item.raw_payload_hash,
                        item.source,
                        item.timestamp_source,
                        int(item.freshness_eligible),
                    ),
                )
                inserted += cursor.rowcount
                if cursor.rowcount == 0:
                    continue
                if (
                    item.limit is None
                    or not item.observed_at
                    or not item.freshness_eligible
                ):
                    continue
                key = self._snapshot_key(item)
                current = con.execute(
                    "SELECT first_seen_at,last_seen_at,observation_count,version FROM platform_gate_snapshots WHERE snapshot_key=?",
                    (key,),
                ).fetchone()
                if current and _parse_time(str(current[1])) > _parse_time(
                    item.observed_at
                ):
                    continue
                first_seen = (
                    min(str(current[0]), item.observed_at)
                    if current
                    else item.observed_at
                )
                count, version = (
                    (int(current[2]) + 1, int(current[3]) + 1) if current else (1, 1)
                )
                con.execute(
                    """INSERT INTO platform_gate_snapshots
                    (snapshot_key,gate_name,limit_value,direction,region,universe_name,delay,alpha_type,theme_id,pyramid_id,first_seen_at,last_seen_at,observation_count,source,raw_payload_hash,version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(snapshot_key) DO UPDATE SET limit_value=excluded.limit_value,direction=excluded.direction,
                    first_seen_at=excluded.first_seen_at,last_seen_at=excluded.last_seen_at,observation_count=excluded.observation_count,
                    source=excluded.source,raw_payload_hash=excluded.raw_payload_hash,version=excluded.version""",
                    (
                        key,
                        item.gate_name,
                        item.limit,
                        item.direction,
                        item.region,
                        item.universe,
                        item.delay,
                        item.alpha_type,
                        item.theme_id,
                        item.pyramid_id,
                        first_seen,
                        item.observed_at,
                        count,
                        item.source,
                        item.raw_payload_hash,
                        version,
                    ),
                )
        return inserted

    def resolve(self, scope: GateScope, gate_name: str) -> GateSnapshot | None:
        requested = scope.values()
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                """SELECT snapshot_key,gate_name,limit_value,direction,region,universe_name,delay,alpha_type,theme_id,pyramid_id,first_seen_at,last_seen_at,observation_count,source,raw_payload_hash,version
                FROM platform_gate_snapshots WHERE gate_name=?""",
                (gate_name.upper(),),
            ).fetchall()
        matches = []
        for row in rows:
            stored = tuple(str(value) for value in row[4:10])
            if all(
                saved == "*" or saved.upper() == wanted.upper()
                for saved, wanted in zip(stored, requested)
            ):
                specificity = sum(value != "*" for value in stored)
                matches.append((specificity, _parse_time(str(row[11])), row))
        if not matches:
            return None
        row = max(matches, key=lambda item: (item[0], item[1]))[2]
        return GateSnapshot(
            row[0],
            row[1],
            float(row[2]),
            row[3],
            GateScope(*row[4:10]),
            row[10],
            row[11],
            int(row[12]),
            row[13],
            row[14],
            int(row[15]),
        )

    def require_fresh(
        self, scope: GateScope, gate_name: str, *, now: datetime | None = None
    ) -> GateSnapshot:
        snapshot = self.resolve(scope, gate_name)
        if snapshot is None:
            raise MissingGateSnapshot(gate_name)
        current = now or datetime.now(timezone.utc)
        if current - _parse_time(snapshot.last_seen_at) > timedelta(
            hours=self.freshness_hours
        ):
            raise StaleGateSnapshot(gate_name)
        return snapshot

    def export_snapshot(self, path: str | Path) -> int:
        with sqlite3.connect(self.database) as con:
            rows = con.execute(
                "SELECT snapshot_key,gate_name,limit_value,direction,region,universe_name,delay,alpha_type,theme_id,pyramid_id,first_seen_at,last_seen_at,observation_count,source,raw_payload_hash,version FROM platform_gate_snapshots ORDER BY gate_name,snapshot_key"
            ).fetchall()
        payload = [
            {
                "snapshot_key": r[0],
                "gate_name": r[1],
                "limit": r[2],
                "direction": r[3],
                "scope": {
                    "region": r[4],
                    "universe": r[5],
                    "delay": r[6],
                    "alpha_type": r[7],
                    "theme_id": r[8],
                    "pyramid_id": r[9],
                },
                "first_seen_at": r[10],
                "last_seen_at": r[11],
                "observation_count": r[12],
                "source": r[13],
                "raw_payload_hash": r[14],
                "version": r[15],
            }
            for r in rows
        ]
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return len(payload)


def sync_gate_sources(
    database: str | Path, sources: Iterable[str | Path]
) -> dict[str, int]:
    """Stream saved API response columns into the registry without touching source files."""
    registry = GateRegistry(database)
    scanned = observations = 0
    for source in sources:
        path = Path(source)
        if not path.is_file() or path.suffix.lower() != ".csv":
            continue
        pending: list[GateObservation] = []
        with path.open(
            "r", encoding="utf-8-sig", errors="ignore", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                scanned += 1
                raw = row.get("platform_check_json") or row.get("check_json") or ""
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    continue
                settings = {
                    key.lower(): row.get(key)
                    for key in ("Region", "Universe", "Delay")
                    if row.get(key)
                }
                payload.setdefault("settings", settings)
                payload.setdefault("id", row.get("alpha_id") or "")
                pending.extend(
                    parse_gate_observations(
                        payload,
                        observed_at=row.get("utc_iso") or None,
                        source=path.name,
                    )
                )
                if len(pending) >= 2000:
                    observations += registry.record_many(pending)
                    pending = []
        if pending:
            observations += registry.record_many(pending)
    return {"rows_scanned": scanned, "observations_recorded": observations}
