"""Read-only evidence collector for CONSULTANT_ALPHA_VNEXT_REVIEW.md."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> None:
    database = Path("research_memory.sqlite")
    output: dict[str, object] = {}
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as con:
        output["counts"] = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "legacy_alphas",
                "alpha_lineage",
                "alpha_expression_features",
                "alpha_behavior_clusters",
                "alpha_cluster_members",
                "legacy_triage_results",
                "platform_gate_observations",
                "platform_gate_snapshots",
                "settings_trials",
                "consultant_submit_queue",
                "simulation_requests",
            )
        }
        output["duplicate_canonical_hashes"] = con.execute(
            """SELECT COUNT(*) FROM (
            SELECT exact_hash FROM legacy_alphas WHERE is_canonical=1
            GROUP BY exact_hash HAVING COUNT(*)>1)"""
        ).fetchone()[0]
        output["lineage_alpha_ids"] = con.execute(
            "SELECT COUNT(DISTINCT alpha_id) FROM alpha_lineage WHERE trim(alpha_id)<>''"
        ).fetchone()[0]
        output["triage_classifications"] = dict(
            con.execute(
                "SELECT classification,COUNT(*) FROM legacy_triage_results GROUP BY classification"
            )
        )
        output["triage_reasons"] = dict(
            con.execute(
                "SELECT reason,COUNT(*) FROM legacy_triage_results GROUP BY reason ORDER BY COUNT(*) DESC"
            )
        )
        output["clusters_with_multiple_rechecks"] = con.execute(
            """SELECT COUNT(*) FROM (
            SELECT cluster_id FROM legacy_triage_results WHERE classification='RECHECK'
            GROUP BY cluster_id HAVING COUNT(*)>1)"""
        ).fetchone()[0]
        output["archived_settings_trials"] = con.execute(
            """SELECT COUNT(*) FROM settings_trials s
            JOIN legacy_triage_results t ON t.legacy_id=s.expression_id
            WHERE t.classification='ARCHIVE'"""
        ).fetchone()[0]
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=24))
            .isoformat()
            .replace("+00:00", "Z")
        )
        output["gate_snapshots_by_name"] = dict(
            con.execute(
                "SELECT gate_name,COUNT(*) FROM platform_gate_snapshots GROUP BY gate_name ORDER BY gate_name"
            )
        )
        output["fresh_gate_snapshots_24h"] = con.execute(
            "SELECT COUNT(*) FROM platform_gate_snapshots WHERE last_seen_at>=?",
            (cutoff,),
        ).fetchone()[0]
        output["queue_statuses"] = dict(
            con.execute(
                "SELECT status,COUNT(*) FROM consultant_submit_queue GROUP BY status"
            )
        )
    output["report_sizes"] = {
        path.name: path.stat().st_size
        for path in sorted(Path("consultant_reports").glob("legacy_*.csv"))
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
