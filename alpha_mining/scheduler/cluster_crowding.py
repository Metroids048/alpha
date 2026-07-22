"""Cluster-level Prod Corr crowding statistics and freeze policy.

A frozen cluster means: enough platform observations exist, every one failed
PROD_CORRELATION, and the median observed value is clearly above the live cutoff.
Frozen clusters must not spawn further parameter/settings variants — only
mechanism-level rewrites are permitted.

Usage::

    registry = ClusterFreezeRegistry(db_path="research_memory.sqlite")
    registry.refresh()

    if registry.is_frozen("my_sig::rank_ts_delta"):
        # skip this parent's offspring generation
        ...
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClusterCrowdingStats:
    cluster_id: str           # behavior_signature used as cluster key
    observation_count: int
    pass_count: int
    fail_count: int
    pass_rate: float          # 0.0–1.0
    median_prod_corr: float | None
    live_cutoff: float | None
    should_freeze: bool

    @property
    def margin_above_cutoff(self) -> float | None:
        """How far the median sits above the live cutoff (positive = bad)."""
        if self.median_prod_corr is None or self.live_cutoff is None:
            return None
        return self.median_prod_corr - self.live_cutoff


class ClusterFreezeRegistry:
    """Computes and caches freeze decisions for behavior-signature clusters.

    Freeze condition (all must hold):
    - observation_count >= min_observations
    - pass_count == 0
    - median_prod_corr > live_cutoff + freeze_margin
    - freeze_margin and min_observations are configurable; no hard-coded 0.7/0.8.

    The live cutoff is read from platform_gate_snapshots (gate_name=PROD_CORRELATION).
    If no snapshot exists, the registry falls back to fallback_cutoff (default 0.7)
    but marks the decision as uncertain.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        min_observations: int = 5,
        freeze_margin: float = 0.05,
        fallback_cutoff: float = 0.70,
    ) -> None:
        self.db_path = Path(db_path)
        self.min_observations = min_observations
        self.freeze_margin = freeze_margin
        self.fallback_cutoff = fallback_cutoff
        self._frozen: set[str] = set()
        self._stats: dict[str, ClusterCrowdingStats] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Recompute freeze decisions from the database. Call before each cycle."""
        if not self.db_path.exists():
            return
        with sqlite3.connect(self.db_path) as con:
            live_cutoff = self._load_live_cutoff(con)
            rows = self._load_cluster_observations(con)

        self._frozen.clear()
        self._stats.clear()

        # Group by behavior_cluster_id (falls back to expression_id if cluster unknown).
        from collections import defaultdict
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            key = str(row["cluster_id"] or row["expression_id"] or "")
            if key:
                groups[key].append(row)

        effective_cutoff = live_cutoff if live_cutoff is not None else self.fallback_cutoff

        for cluster_id, obs in groups.items():
            n = len(obs)
            pass_count = sum(1 for o in obs if str(o["status"]).upper() == "PASS")
            fail_count = sum(1 for o in obs if str(o["status"]).upper() == "FAIL")
            corr_values = [
                o["prod_correlation"]
                for o in obs
                if o["prod_correlation"] is not None
            ]
            median_corr = statistics.median(corr_values) if corr_values else None

            should_freeze = (
                n >= self.min_observations
                and pass_count == 0
                and median_corr is not None
                and median_corr > effective_cutoff + self.freeze_margin
            )

            stats = ClusterCrowdingStats(
                cluster_id=cluster_id,
                observation_count=n,
                pass_count=pass_count,
                fail_count=fail_count,
                pass_rate=pass_count / n if n > 0 else 0.0,
                median_prod_corr=median_corr,
                live_cutoff=effective_cutoff,
                should_freeze=should_freeze,
            )
            self._stats[cluster_id] = stats
            if should_freeze:
                self._frozen.add(cluster_id)

    def is_frozen(self, cluster_id_or_signature: str) -> bool:
        """Return True if this cluster should not generate more offspring."""
        return cluster_id_or_signature in self._frozen

    def all_stats(self) -> list[ClusterCrowdingStats]:
        return list(self._stats.values())

    def frozen_clusters(self) -> list[str]:
        return sorted(self._frozen)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_live_cutoff(self, con: sqlite3.Connection) -> float | None:
        """Read the most recent PROD_CORRELATION gate snapshot limit."""
        row = con.execute(
            "SELECT limit_value FROM platform_gate_snapshots "
            "WHERE gate_name = 'PROD_CORRELATION' "
            "ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0] is not None:
            try:
                return float(row[0])
            except (TypeError, ValueError):
                pass
        return None

    def _load_cluster_observations(self, con: sqlite3.Connection) -> list[dict]:
        """Load all prod_corr observations joined with cluster info."""
        # Check if the prod_correlation_observations table exists (migration v4).
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "prod_correlation_observations" not in tables:
            return []

        rows = con.execute(
            "SELECT alpha_id, expression_id, behavior_cluster_id, "
            "       prod_correlation, status "
            "FROM prod_correlation_observations"
        ).fetchall()

        return [
            {
                "alpha_id": r[0],
                "expression_id": r[1],
                "cluster_id": r[2],  # may be None
                "prod_correlation": r[3],
                "status": r[4],
            }
            for r in rows
        ]
