"""Bridge: convert platform Prod Corr observations into bandit reward components.

Usage::

    components = get_prod_corr_reward_components(con, alpha_id="abc123")
    # Returns one of:
    #   {"prod_corr_pass": 1.0}
    #   {"prod_corr_fail": 1.0}
    #   {"prod_corr_unknown": 1.0}   ← also used for PENDING/MISSING/ERROR

The caller merges this dict with other reward components and passes the combined
dict to ConsultantBandit.reward().
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from alpha_mining.platform.check_parser import (
    PROD_CORR_FAIL,
    PROD_CORR_PASS,
)

if TYPE_CHECKING:
    pass


def get_prod_corr_reward_components(
    con: sqlite3.Connection,
    alpha_id: str,
) -> dict[str, float]:
    """Return a single-key reward component dict for the latest Prod Corr observation.

    Looks up the most recent row in prod_correlation_observations for alpha_id.
    If no row exists, returns prod_corr_unknown to signal we lack evidence —
    never returns an empty dict or a default-pass assumption.

    Returns one of:
        {"prod_corr_pass":    1.0}
        {"prod_corr_fail":    1.0}
        {"prod_corr_unknown": 1.0}
    """
    if not alpha_id:
        return {"prod_corr_unknown": 1.0}

    row = con.execute(
        "SELECT status FROM prod_correlation_observations "
        "WHERE alpha_id = ? ORDER BY observed_at DESC LIMIT 1",
        (alpha_id,),
    ).fetchone()

    if row is None:
        return {"prod_corr_unknown": 1.0}

    status = str(row[0] or "").upper()
    if status == PROD_CORR_PASS:
        return {"prod_corr_pass": 1.0}
    if status == PROD_CORR_FAIL:
        return {"prod_corr_fail": 1.0}
    # PENDING / MISSING / UNKNOWN / ERROR all map to unknown penalty.
    return {"prod_corr_unknown": 1.0}


def upsert_prod_corr_observation(
    con: sqlite3.Connection,
    *,
    alpha_id: str,
    expression_id: str = "",
    behavior_cluster_id: str | None = None,
    prod_correlation: float | None,
    prod_cutoff: float | None,
    required_sharpe_improvement: float | None,
    status: str,
    failure_message: str = "",
    raw_payload_hash: str = "",
    observed_at: str,
    source: str = "platform_payload",
) -> None:
    """Insert or ignore a Prod Corr observation row.

    Uses INSERT OR IGNORE with the (alpha_id, raw_payload_hash) UNIQUE constraint
    so re-ingesting the same platform response is idempotent.
    """
    con.execute(
        "INSERT OR IGNORE INTO prod_correlation_observations "
        "(alpha_id, expression_id, behavior_cluster_id, prod_correlation, "
        " prod_cutoff, required_sharpe_improvement, status, failure_message, "
        " raw_payload_hash, observed_at, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            alpha_id,
            expression_id,
            behavior_cluster_id,
            prod_correlation,
            prod_cutoff,
            required_sharpe_improvement,
            status,
            failure_message,
            raw_payload_hash,
            observed_at,
            source,
        ),
    )
