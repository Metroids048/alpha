"""Evidence-window metrics and conservative low-yield arm downweighting."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ArmDimensions:
    family: str
    dataset: str
    field_family: str
    mechanism: str
    operator_topology: str
    region: str
    universe: str
    delay: str

    @property
    def key(self) -> str:
        canonical = "|".join(
            str(value).strip().lower()
            for value in (
                self.family,
                self.dataset,
                self.field_family,
                self.mechanism,
                self.operator_topology,
                self.region,
                self.universe,
                self.delay,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArmStats:
    simulation_count: int
    base_pass_rate: float
    median_sharpe: float | None
    near_pass_rate: float
    self_corr_pass_rate: float
    prod_corr_pass_rate: float
    final_submit_rate: float
    consecutive_low_windows: int
    sampling_weight: float


class ResearchArmTracker:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def record_window(
        self,
        arm: ArmDimensions,
        *,
        sharpes: list[float],
        base_passes: list[bool],
        near_passes: list[bool],
        self_corr_passes: int,
        prod_corr_passes: int,
        final_submits: int,
    ) -> ArmStats:
        if not (len(sharpes) == len(base_passes) == len(near_passes)):
            raise ValueError("arm observation lengths do not match")
        window_count = len(sharpes)
        window_pass_rate = sum(base_passes) / window_count if window_count else 0.0
        low_window = window_count >= 20 and window_pass_rate < 0.02
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT simulation_count,base_pass_count,near_pass_count,sharpe_values_json,
                          self_corr_pass_count,prod_corr_pass_count,final_submit_count,
                          consecutive_low_windows,sampling_weight
                   FROM research_arm_metrics WHERE arm_key=?""",
                (arm.key,),
            ).fetchone()
            if row is None:
                totals = [0, 0, 0, [], 0, 0, 0, 0, 1.0]
            else:
                totals = [
                    int(row[0]), int(row[1]), int(row[2]), list(json.loads(row[3])),
                    int(row[4]), int(row[5]), int(row[6]), int(row[7]), float(row[8]),
                ]
            totals[0] += window_count
            totals[1] += sum(base_passes)
            totals[2] += sum(near_passes)
            totals[3].extend(float(value) for value in sharpes)
            totals[4] += max(0, int(self_corr_passes))
            totals[5] += max(0, int(prod_corr_passes))
            totals[6] += max(0, int(final_submits))
            totals[7] = totals[7] + 1 if low_window else 0
            totals[8] = 0.1 if totals[7] >= 3 else 1.0
            con.execute(
                """INSERT INTO research_arm_metrics
                (arm_key,family,dataset,field_family,mechanism,operator_topology,region,
                 universe_name,delay,simulation_count,base_pass_count,near_pass_count,
                 sharpe_values_json,self_corr_pass_count,prod_corr_pass_count,final_submit_count,
                 consecutive_low_windows,sampling_weight,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(arm_key) DO UPDATE SET
                 simulation_count=excluded.simulation_count,base_pass_count=excluded.base_pass_count,
                 near_pass_count=excluded.near_pass_count,sharpe_values_json=excluded.sharpe_values_json,
                 self_corr_pass_count=excluded.self_corr_pass_count,
                 prod_corr_pass_count=excluded.prod_corr_pass_count,
                 final_submit_count=excluded.final_submit_count,
                 consecutive_low_windows=excluded.consecutive_low_windows,
                 sampling_weight=excluded.sampling_weight,updated_at=excluded.updated_at""",
                (
                    arm.key, arm.family, arm.dataset, arm.field_family, arm.mechanism,
                    arm.operator_topology, arm.region, arm.universe, arm.delay,
                    totals[0], totals[1], totals[2], json.dumps(totals[3]), totals[4],
                    totals[5], totals[6], totals[7], totals[8], _utc_now(),
                ),
            )
        return self.stats(arm)

    def stats(self, arm: ArmDimensions) -> ArmStats:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT simulation_count,base_pass_count,near_pass_count,sharpe_values_json,
                          self_corr_pass_count,prod_corr_pass_count,final_submit_count,
                          consecutive_low_windows,sampling_weight
                   FROM research_arm_metrics WHERE arm_key=?""",
                (arm.key,),
            ).fetchone()
        if row is None:
            return ArmStats(0, 0.0, None, 0.0, 0.0, 0.0, 0.0, 0, 1.0)
        count = int(row[0])
        sharpes = [float(value) for value in json.loads(row[3])]
        denominator = count or 1
        return ArmStats(
            count,
            int(row[1]) / denominator,
            statistics.median(sharpes) if sharpes else None,
            int(row[2]) / denominator,
            int(row[4]) / denominator,
            int(row[5]) / denominator,
            int(row[6]) / denominator,
            int(row[7]),
            float(row[8]),
        )
