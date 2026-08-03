"""Evidence windows and frozen feedback budgets for research arms."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from alpha_mining.storage.migrations import migrate


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
                self.family, self.dataset, self.field_family, self.mechanism,
                self.operator_topology, self.region, self.universe, self.delay,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArmState(str, Enum):
    YELLOW = "YELLOW"
    RED = "RED"
    DEAD = "DEAD"
    GREEN = "GREEN"


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
    state: ArmState = ArmState.YELLOW


class ResearchArmTracker:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        migrate(self.database)

    def record_window(
        self,
        arm: ArmDimensions,
        *,
        sharpes: list[float | None],
        base_passes: list[bool],
        near_passes: list[bool],
        self_corr_passes: int,
        prod_corr_passes: int,
        final_submits: int,
        fitnesses: list[float | None] | None = None,
    ) -> ArmStats:
        if not (len(sharpes) == len(base_passes) == len(near_passes)):
            raise ValueError("arm observation lengths do not match")
        fitnesses = fitnesses if fitnesses is not None else [None] * len(sharpes)
        if len(fitnesses) != len(sharpes):
            raise ValueError("fitness observation lengths do not match")
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            self._record_window_in_transaction(
                con, arm, sharpes=sharpes, fitnesses=fitnesses,
                base_passes=base_passes, near_passes=near_passes,
                self_corr_passes=self_corr_passes, prod_corr_passes=prod_corr_passes,
                final_submits=final_submits,
            )
        return self.stats(arm)

    @staticmethod
    def _record_window_in_transaction(
        con: sqlite3.Connection,
        arm: ArmDimensions,
        *,
        sharpes: list[float | None],
        fitnesses: list[float | None],
        base_passes: list[bool],
        near_passes: list[bool],
        self_corr_passes: int,
        prod_corr_passes: int,
        final_submits: int,
    ) -> None:
        row = con.execute(
            """SELECT simulation_count,base_pass_count,near_pass_count,sharpe_values_json,
                      self_corr_pass_count,prod_corr_pass_count,final_submit_count,
                      consecutive_low_windows
               FROM research_arm_metrics WHERE arm_key=?""",
            (arm.key,),
        ).fetchone()
        totals = [0, 0, 0, [], 0, 0, 0, 0] if row is None else [
            int(row[0]), int(row[1]), int(row[2]), list(json.loads(row[3])),
            int(row[4]), int(row[5]), int(row[6]), int(row[7]),
        ]
        totals[0] += len(sharpes)
        totals[1] += sum(bool(item) for item in base_passes)
        totals[2] += sum(bool(item) for item in near_passes)
        totals[3].extend(float(item) for item in sharpes if item is not None)
        totals[4] += max(0, int(self_corr_passes))
        totals[5] += max(0, int(prod_corr_passes))
        totals[6] += max(0, int(final_submits))
        window_low = len(sharpes) >= 20 and sum(bool(item) for item in base_passes) / len(sharpes) < 0.02
        totals[7] = totals[7] + 1 if window_low else 0
        state = _state(totals[0], totals[2], totals[6], totals[3], fitnesses)
        weight = _weight(state, consecutive_low_windows=totals[7])
        con.execute(
            """INSERT INTO research_arm_metrics
            (arm_key,family,dataset,field_family,mechanism,operator_topology,region,universe_name,delay,
             simulation_count,base_pass_count,near_pass_count,sharpe_values_json,self_corr_pass_count,
             prod_corr_pass_count,final_submit_count,consecutive_low_windows,sampling_weight,updated_at,arm_state)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(arm_key) DO UPDATE SET
             simulation_count=excluded.simulation_count,base_pass_count=excluded.base_pass_count,
             near_pass_count=excluded.near_pass_count,sharpe_values_json=excluded.sharpe_values_json,
             self_corr_pass_count=excluded.self_corr_pass_count,prod_corr_pass_count=excluded.prod_corr_pass_count,
             final_submit_count=excluded.final_submit_count,consecutive_low_windows=excluded.consecutive_low_windows,
             sampling_weight=excluded.sampling_weight,updated_at=excluded.updated_at,arm_state=excluded.arm_state""",
            (arm.key, arm.family, arm.dataset, arm.field_family, arm.mechanism, arm.operator_topology,
             arm.region, arm.universe, arm.delay, totals[0], totals[1], totals[2], json.dumps(totals[3]),
             totals[4], totals[5], totals[6], totals[7], weight, _utc_now(), state.value),
        )

    def record_observation(
        self,
        arm: ArmDimensions,
        *,
        base_pass: bool,
        sharpe: float | None = None,
        fitness: float | None = None,
        near_pass: bool = False,
        self_corr_pass: bool = False,
        prod_corr_pass: bool = False,
        final_submit: bool = False,
    ) -> ArmStats:
        """Persist each real observation and flush exactly its collected 20 samples."""
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT sharpes_json,fitnesses_json,base_passes_json,near_passes_json,
                          self_corr_passes_json,prod_corr_passes_json,final_submits_json
                   FROM research_arm_observation_windows WHERE arm_key=?""",
                (arm.key,),
            ).fetchone()
            values = [list(json.loads(value)) if row else [] for value in (row or ("[]",) * 7)]
            values[0].append(sharpe)
            values[1].append(fitness)
            values[2].append(bool(base_pass))
            values[3].append(bool(near_pass))
            values[4].append(bool(self_corr_pass))
            values[5].append(bool(prod_corr_pass))
            values[6].append(bool(final_submit))
            if len(values[0]) >= 20:
                window = [items[:20] for items in values]
                values = [items[20:] for items in values]
                self._record_window_in_transaction(
                    con, arm, sharpes=window[0], fitnesses=window[1], base_passes=window[2],
                    near_passes=window[3], self_corr_passes=sum(window[4]),
                    prod_corr_passes=sum(window[5]), final_submits=sum(window[6]),
                )
            con.execute(
                """INSERT INTO research_arm_observation_windows
                (arm_key,current_window_count,current_window_base_pass_count,updated_at,sharpes_json,fitnesses_json,
                 base_passes_json,near_passes_json,self_corr_passes_json,prod_corr_passes_json,final_submits_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(arm_key) DO UPDATE SET current_window_count=excluded.current_window_count,
                 current_window_base_pass_count=excluded.current_window_base_pass_count,updated_at=excluded.updated_at,
                 sharpes_json=excluded.sharpes_json,fitnesses_json=excluded.fitnesses_json,
                 base_passes_json=excluded.base_passes_json,near_passes_json=excluded.near_passes_json,
                 self_corr_passes_json=excluded.self_corr_passes_json,prod_corr_passes_json=excluded.prod_corr_passes_json,
                 final_submits_json=excluded.final_submits_json""",
                (arm.key, len(values[0]), sum(bool(item) for item in values[2]), _utc_now(), *(json.dumps(item) for item in values)),
            )
        return self.stats(arm)

    def stats(self, arm: ArmDimensions) -> ArmStats:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                """SELECT simulation_count,base_pass_count,near_pass_count,sharpe_values_json,self_corr_pass_count,
                          prod_corr_pass_count,final_submit_count,consecutive_low_windows,sampling_weight,arm_state
                   FROM research_arm_metrics WHERE arm_key=?""",
                (arm.key,),
            ).fetchone()
            pending = con.execute(
                """SELECT sharpes_json,fitnesses_json,base_passes_json,near_passes_json,
                          self_corr_passes_json,prod_corr_passes_json,final_submits_json
                   FROM research_arm_observation_windows WHERE arm_key=?""",
                (arm.key,),
            ).fetchone()
        if row is None:
            completed = [0, 0, 0, [], 0, 0, 0, 0, ArmState.YELLOW]
        else:
            completed = [int(row[0]), int(row[1]), int(row[2]), list(json.loads(row[3])), int(row[4]), int(row[5]), int(row[6]), int(row[7]), ArmState(str(row[9] or "YELLOW"))]
        pending_values = [list(json.loads(value)) if pending else [] for value in (pending or ("[]",) * 7)]
        count = completed[0] + len(pending_values[0])
        sharpes = completed[3] + [float(item) for item in pending_values[0] if item is not None]
        denominator = count or 1
        weight = _weight(completed[8], consecutive_low_windows=completed[7])
        return ArmStats(
            count,
            (completed[1] + sum(bool(item) for item in pending_values[2])) / denominator,
            statistics.median(sharpes) if sharpes else None,
            (completed[2] + sum(bool(item) for item in pending_values[3])) / denominator,
            (completed[4] + sum(bool(item) for item in pending_values[4])) / denominator,
            (completed[5] + sum(bool(item) for item in pending_values[5])) / denominator,
            (completed[6] + sum(bool(item) for item in pending_values[6])) / denominator,
            completed[7], weight, completed[8],
        )

    def next_cycle_quota(self, arm: ArmDimensions, *, normal_quota: int) -> int:
        stats = self.stats(arm)
        if stats.sampling_weight <= 0 or stats.state is ArmState.DEAD:
            return 0
        if stats.sampling_weight < 1.0:
            return min(1, max(0, int(normal_quota)))
        return max(0, int(normal_quota))


def _state(count: int, near_count: int, ready_count: int, sharpes: list[float], fitnesses: list[float | None]) -> ArmState:
    if ready_count > 0 or near_count > 0:
        return ArmState.GREEN
    if count < 4:
        return ArmState.YELLOW
    median = statistics.median(sharpes) if sharpes else float("-inf")
    if count >= 8 and sharpes and max(sharpes) < 0.8 and all(item is not None and item < 0.5 for item in fitnesses):
        return ArmState.DEAD
    if median < 0.8:
        return ArmState.RED
    return ArmState.GREEN


def _weight(state: ArmState, *, consecutive_low_windows: int = 0) -> float:
    if state is ArmState.DEAD:
        return 0.0
    if consecutive_low_windows >= 3:
        return 0.1
    return {ArmState.GREEN: 1.0, ArmState.YELLOW: 0.5, ArmState.RED: 0.25}[state]
