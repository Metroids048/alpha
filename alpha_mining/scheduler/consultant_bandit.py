"""Persistent bounded-arm reward accounting for consultant exploration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.policy.consultant_policy import ConsultantPolicy


@dataclass(frozen=True)
class BanditArm:
    family: str
    dataset: str
    mutation_type: str
    settings_profile: str

    @property
    def key(self) -> str:
        return "|".join(
            (self.family, self.dataset, self.mutation_type, self.settings_profile)
        )


class ConsultantBandit:
    def __init__(
        self, database: str | Path, *, policy: ConsultantPolicy | None = None
    ) -> None:
        self.database = Path(database)
        self.policy = policy or ConsultantPolicy()

    def reward(self, arm: BanditArm, components: dict[str, float | bool]) -> float:
        value = sum(
            self.policy.reward_weights.get(name, 0.0) * float(component)
            for name, component in components.items()
        )
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        event_id = hashlib.sha256(
            f"{arm.key}\0{now}\0{json.dumps(components, sort_keys=True)}".encode()
        ).hexdigest()
        with sqlite3.connect(self.database) as con:
            con.execute(
                "INSERT INTO consultant_bandit_events(event_id,arm_key,reward,components_json,created_at) VALUES (?,?,?,?,?)",
                (event_id, arm.key, value, json.dumps(components, sort_keys=True), now),
            )
        return value

    def scores(self) -> dict[str, float]:
        with sqlite3.connect(self.database) as con:
            return {
                key: float(value)
                for key, value in con.execute(
                    "SELECT arm_key,AVG(reward) FROM consultant_bandit_events GROUP BY arm_key"
                )
            }
