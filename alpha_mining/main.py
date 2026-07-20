"""alpha_mining CLI — Research Memory management and pipeline delegation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]


def _ensure_database(path: str) -> Path:
    from alpha_mining.storage.migrations import migrate

    database = Path(path)
    migrate(database)
    return database


# ─── subcommand handlers ──────────────────────────────────────────────────────


def _cmd_install_topics(args: argparse.Namespace) -> int:
    from alpha_mining.knowledge.ontology import install_seed_topics

    n = install_seed_topics(args.database)
    print(f"[install-topics] installed {n} seed topics -> {args.database}")
    return 0


def _cmd_run_evolution(args: argparse.Namespace) -> int:
    from alpha_mining.scheduler.evolution import EvolutionEngine
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db = SqliteRunLog(args.database)
    engine = EvolutionEngine(db, exploration_bonus=args.exploration_bonus)
    result = engine.run()
    print(
        f"[run-evolution] stats_updated={result['stats_updated']} "
        f"weights_updated={result['weights_updated']}"
    )
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    from alpha_mining.storage.backfill_from_csv import backfill_csvs, format_summary

    sources = tuple(Path(s) for s in args.sources) if args.sources else ()
    summary = backfill_csvs(Path(args.database), sources)
    print(format_summary(summary))
    return 0


def _cmd_observe_feedback(args: argparse.Namespace) -> int:
    from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
    from alpha_mining.storage.sqlite_store import SqliteRunLog
    from alpha_mining.submitter.observation import observe_feedback_csv

    summary = observe_feedback_csv(
        SqliteRunLog(args.database),
        args.source,
        llm_factory=None if args.template_only else lambda: DeepSeekStructuredLLM(),
        description_limit=args.description_limit,
    )
    print(
        f"[observe-feedback] scanned={summary.rows_scanned} "
        f"observed={summary.rows_observed} descriptions={summary.descriptions_generated} "
        f"failures={summary.failure_category_counts}"
    )
    return 0


def _cmd_pipeline(args: argparse.Namespace) -> int:
    """Delegate to the top-level compatibility entry without package imports."""
    from alpha_mining.common import subprocess_no_window_kwargs

    script = Path(__file__).resolve().parents[1] / "run_pipeline_cycle.py"
    if not script.is_file():
        print(f"ERROR: missing compatibility entry: {script}", file=sys.stderr)
        return 1
    completed = subprocess.run(
        [sys.executable, str(script), *(args.pipeline_args or [])],
        cwd=str(script.parent),
        **subprocess_no_window_kwargs(),
    )
    return int(completed.returncode)


def _cmd_gates_sync(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone
    from alpha_mining.platform.check_parser import parse_gate_observations
    from alpha_mining.platform.gates import GateRegistry, sync_gate_sources

    database = _ensure_database(args.database)
    if args.live:
        from alpha_mining.platform.client import ReadOnlyPlatformClient

        if not args.alpha_ids:
            print(
                "[gates/sync] --live requires at least one --alpha-id", file=sys.stderr
            )
            return 2
        payloads = ReadOnlyPlatformClient(state_path=args.auth_state_file).fetch_many(
            args.alpha_ids
        )
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        registry = GateRegistry(database)
        recorded = sum(
            registry.record_many(
                parse_gate_observations(
                    payload, observed_at=observed_at, source="live_read_only"
                )
            )
            for payload in payloads
        )
        print(f"[gates/sync] live_read_only={len(payloads)} observations={recorded}")
        return 0
    sources = [Path(item) for item in (args.sources or [ROOT / "总alpha.csv"])]
    summary = sync_gate_sources(database, sources)
    print(
        f"[gates/sync] rows={summary['rows_scanned']} observations={summary['observations_recorded']} database={database}"
    )
    return 0


def _cmd_gates_show(args: argparse.Namespace) -> int:
    from alpha_mining.platform.gates import GateRegistry

    database = _ensure_database(args.database)
    output = Path(args.output)
    count = GateRegistry(database).export_snapshot(output)
    print(f"[gates/show] snapshots={count} output={output}")
    return 0


def _default_legacy_sources() -> list[Path]:
    names = (
        "总alpha.csv",
        "legacy_triage_clusters.csv",
        "legacy_triage_resubmit.csv",
        "alpha_feedback_check_distribution.csv",
        "alpha_feedback_learning_summary.csv",
    )
    return [ROOT / name for name in names if (ROOT / name).is_file()]


def _cmd_legacy_import(args: argparse.Namespace) -> int:
    from alpha_mining.legacy.importer import LegacyImporter

    database = _ensure_database(args.database)
    sources = [Path(item) for item in (args.sources or _default_legacy_sources())]
    summary = LegacyImporter(database, chunk_size=args.chunk_size).import_sources(
        sources
    )
    print(
        f"[legacy/import] scanned={summary.rows_scanned} canonical={summary.canonical_records} lineage={summary.lineage_records} chunks={summary.chunks_committed} checks={summary.checks_imported} gates={summary.gates_observed}"
    )
    return 0


def _cmd_legacy_triage(args: argparse.Namespace) -> int:
    from alpha_mining.legacy.service import triage_database
    from alpha_mining.policy.consultant_policy import ConsultantPolicy

    database = _ensure_database(args.database)
    policy = ConsultantPolicy.from_file(args.config)
    summary = triage_database(
        database,
        near_pass_ratio=policy.near_pass_ratio,
        gate_freshness_hours=policy.gate_freshness_hours,
    )
    print(
        f"[legacy/triage] clusters={summary.clusters} medoids={summary.medoids} classifications={json.dumps(summary.classifications, sort_keys=True)}"
    )
    return 0


def _cmd_legacy_report(args: argparse.Namespace) -> int:
    from alpha_mining.legacy.service import report_database

    database = _ensure_database(args.database)
    counts = report_database(database, args.output_dir)
    print(f"[legacy/report] {json.dumps(counts, sort_keys=True)}")
    return 0


def _cmd_correlation_refresh(args: argparse.Namespace) -> int:
    database = _ensure_database(args.database)
    import sqlite3

    with sqlite3.connect(database) as con:
        count = con.execute(
            "SELECT COUNT(DISTINCT expression_id) FROM alpha_daily_returns"
        ).fetchone()[0]
    print(
        f"[correlation/refresh] returns_series={count}; comparisons are computed on demand"
    )
    return 0


def _cmd_correlation_inspect(args: argparse.Namespace) -> int:
    from alpha_mining.correlation.returns_store import ReturnsStore
    from alpha_mining.correlation.service import CorrelationService
    from alpha_mining.policy.consultant_policy import ConsultantPolicy
    import sqlite3

    database = _ensure_database(args.database)
    store = ReturnsStore(database)
    candidate = store.get(args.expression_id)
    policy = ConsultantPolicy.from_file(args.config)
    service = CorrelationService(
        min_overlap=policy.min_correlation_overlap,
        internal_limit=policy.correlation_ceiling,
    )
    results = []
    with sqlite3.connect(database) as con:
        ids = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT expression_id FROM alpha_daily_returns WHERE expression_id<>? ORDER BY expression_id",
                (args.expression_id,),
            )
        ]
    for reference_id in ids:
        result = service.compare(candidate, store.get(reference_id))
        results.append({"reference_id": reference_id, **result.__dict__})
    results.sort(key=lambda item: -(item.get("absolute_correlation") or -1))
    print(
        json.dumps(
            {"expression_id": args.expression_id, "comparisons": results[: args.limit]},
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


def _cmd_consultant_generate(args: argparse.Namespace) -> int:
    from alpha_mining.generator.consultant_generator import ConsultantGenerator
    from alpha_mining.policy.consultant_policy import ConsultantPolicy

    policy = ConsultantPolicy.from_file(args.config)
    generator = ConsultantGenerator(
        max_per_hypothesis=policy.max_candidates_per_hypothesis,
        max_same_behavior=policy.max_behavior_per_round,
    )
    candidates = generator.generate(
        hypothesis_id=args.hypothesis_id,
        family=args.family,
        fields=args.fields or ["close", "volume"],
        parent_expression=args.parent_expression,
    )
    print(json.dumps([item.__dict__ for item in candidates], ensure_ascii=False))
    return 0


def _cmd_consultant_simulate(args: argparse.Namespace) -> int:
    from alpha_mining.policy.consultant_policy import ConsultantPolicy
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

    policy = ConsultantPolicy.from_file(args.config)
    optimizer = SettingsOptimizer(
        max_local_trials=policy.max_settings_trials,
        total_budget=policy.simulation_budget,
        per_candidate_budget=policy.per_candidate_settings_budget,
    )
    base = optimizer.stage1_default(args.family)
    trials = optimizer.local_trials(
        base,
        candidate_id=args.candidate_id,
        quality_score=args.quality_score,
        metric_ratio=args.metric_ratio,
        delay_allowed=args.delay_allowed,
    )
    print(
        json.dumps(
            {
                "mode": "shadow",
                "base": base,
                "trials": [trial.__dict__ for trial in trials],
                "budget": policy.simulation_budget,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_consultant_shadow(args: argparse.Namespace) -> int:
    rc = _cmd_consultant_generate(args)
    print(
        "[consultant/shadow-run] generated only; no simulation or submission endpoint called"
    )
    return rc


def _cmd_submit_dry_run(args: argparse.Namespace) -> int:
    import sqlite3
    from alpha_mining.policy.consultant_policy import ConsultantPolicy
    from alpha_mining.submitter.dry_run import evaluate_triaged_candidates

    database = _ensure_database(args.database)
    with sqlite3.connect(database) as con:
        rows = con.execute(
            "SELECT status,COUNT(*) FROM consultant_submit_queue GROUP BY status"
        ).fetchall()
    summary = evaluate_triaged_candidates(
        database, policy=ConsultantPolicy.from_file(args.config)
    )
    print(
        f"[submit/dry-run] endpoint_calls=0 queue={json.dumps(dict(rows), sort_keys=True)} candidates={summary['candidates']} allowed={summary['allowed']} blocked={summary['blocked']} reasons={json.dumps(summary['blocked_reasons'], sort_keys=True)}"
    )
    return 0


def _cmd_submit_execute(args: argparse.Namespace) -> int:
    from alpha_mining.policy.consultant_policy import ConsultantPolicy

    _ensure_database(args.database)
    policy = ConsultantPolicy.from_file(args.config)
    if not policy.execute_submit or args.confirm != policy.confirmation_phrase:
        print(
            "[submit/execute] BLOCKED: execute_submit is disabled or confirmation is invalid",
            file=sys.stderr,
        )
        return 2
    from alpha_mining.platform.submission_client import LiveSubmissionClient
    from alpha_mining.submitter.queue import ConsultantSubmitQueue

    client = LiveSubmissionClient(state_path=args.auth_state_file)
    counts = ConsultantSubmitQueue(
        args.database,
        gate_freshness_hours=policy.gate_freshness_hours,
    ).execute_ready(client, max_items=args.max_submit, execute=True)
    print(f"[submit/execute] {json.dumps(counts, sort_keys=True)}")
    return 0 if counts["failed"] == 0 else 1


# ─── parser ───────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m alpha_mining",
        description="Research Memory management and pipeline delegation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_topics = sub.add_parser(
        "install-topics",
        help="Install seed topics from YAML into Research Memory.",
    )
    p_topics.add_argument("--database", default="research_memory.sqlite")
    p_topics.set_defaults(func=_cmd_install_topics)

    p_evo = sub.add_parser("run-evolution", help="Run one Evolution Engine cycle.")
    p_evo.add_argument("--database", default="research_memory.sqlite")
    p_evo.add_argument(
        "--exploration-bonus",
        type=float,
        default=1.0,
        dest="exploration_bonus",
    )
    p_evo.set_defaults(func=_cmd_run_evolution)

    p_backfill = sub.add_parser(
        "backfill",
        help="Backfill historical CSV data into Research Memory.",
    )
    p_backfill.add_argument("--database", default="research_memory.sqlite")
    p_backfill.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="CSV",
        help="Path to a legacy alpha CSV file; repeat for multiple files.",
    )
    p_backfill.set_defaults(func=_cmd_backfill)

    p_observe = sub.add_parser(
        "observe-feedback",
        help="Replay an existing feedback CSV into local submission observations only.",
    )
    p_observe.add_argument("--database", default="research_memory.sqlite")
    p_observe.add_argument("--source", default="alpha_submission_feedback.csv")
    p_observe.add_argument("--description-limit", type=int, default=20)
    p_observe.add_argument(
        "--template-only",
        action="store_true",
        help="Do not call DeepSeek while drafting ready-candidate descriptions.",
    )
    p_observe.set_defaults(func=_cmd_observe_feedback)

    p_pipeline = sub.add_parser(
        "pipeline",
        help="Delegate to the preserved top-level legacy pipeline entry.",
    )
    p_pipeline.add_argument("pipeline_args", nargs=argparse.REMAINDER)
    p_pipeline.set_defaults(func=_cmd_pipeline)

    p_gates = sub.add_parser("gates", help="Dynamic platform gate registry.")
    gates_sub = p_gates.add_subparsers(dest="gates_command", required=True)
    p_gate_sync = gates_sub.add_parser("sync")
    p_gate_sync.add_argument("--database", default="research_memory.sqlite")
    p_gate_sync.add_argument("--source", action="append", dest="sources")
    p_gate_sync.add_argument("--live", action="store_true")
    p_gate_sync.add_argument("--alpha-id", action="append", dest="alpha_ids")
    p_gate_sync.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_gate_sync.set_defaults(func=_cmd_gates_sync)
    p_gate_show = gates_sub.add_parser("show")
    p_gate_show.add_argument("--database", default="research_memory.sqlite")
    p_gate_show.add_argument("--output", default="gate_snapshot.json")
    p_gate_show.set_defaults(func=_cmd_gates_show)

    p_legacy = sub.add_parser("legacy", help="Legacy Knowledge Lake.")
    legacy_sub = p_legacy.add_subparsers(dest="legacy_command", required=True)
    p_legacy_import = legacy_sub.add_parser("import")
    p_legacy_import.add_argument("--database", default="research_memory.sqlite")
    p_legacy_import.add_argument("--source", action="append", dest="sources")
    p_legacy_import.add_argument("--chunk-size", type=int, default=2000)
    p_legacy_import.set_defaults(func=_cmd_legacy_import)
    p_legacy_triage = legacy_sub.add_parser("triage")
    p_legacy_triage.add_argument("--database", default="research_memory.sqlite")
    p_legacy_triage.add_argument(
        "--config", default=str(ROOT / "alpha_mining" / "config.yaml")
    )
    p_legacy_triage.set_defaults(func=_cmd_legacy_triage)
    p_legacy_report = legacy_sub.add_parser("report")
    p_legacy_report.add_argument("--database", default="research_memory.sqlite")
    p_legacy_report.add_argument("--output-dir", default="consultant_reports")
    p_legacy_report.set_defaults(func=_cmd_legacy_report)

    p_corr = sub.add_parser("correlation", help="Two-level correlation engine.")
    corr_sub = p_corr.add_subparsers(dest="correlation_command", required=True)
    p_corr_refresh = corr_sub.add_parser("refresh")
    p_corr_refresh.add_argument("--database", default="research_memory.sqlite")
    p_corr_refresh.set_defaults(func=_cmd_correlation_refresh)
    p_corr_inspect = corr_sub.add_parser("inspect")
    p_corr_inspect.add_argument("--database", default="research_memory.sqlite")
    p_corr_inspect.add_argument(
        "--config", default=str(ROOT / "alpha_mining" / "config.yaml")
    )
    p_corr_inspect.add_argument("--expression-id", required=True)
    p_corr_inspect.add_argument("--limit", type=int, default=20)
    p_corr_inspect.set_defaults(func=_cmd_correlation_inspect)

    p_consult = sub.add_parser("consultant", help="Consultant-grade bounded research.")
    consult_sub = p_consult.add_subparsers(dest="consultant_command", required=True)
    for name, handler in (
        ("generate", _cmd_consultant_generate),
        ("shadow-run", _cmd_consultant_shadow),
    ):
        command_parser = consult_sub.add_parser(name)
        command_parser.add_argument(
            "--config", default=str(ROOT / "alpha_mining" / "config.yaml")
        )
        command_parser.add_argument("--hypothesis-id", default="manual")
        command_parser.add_argument("--family", default="exploration")
        command_parser.add_argument("--field", action="append", dest="fields")
        command_parser.add_argument("--parent-expression", default="")
        command_parser.set_defaults(func=handler)
    p_consult_sim = consult_sub.add_parser("simulate")
    p_consult_sim.add_argument(
        "--config", default=str(ROOT / "alpha_mining" / "config.yaml")
    )
    p_consult_sim.add_argument("--family", default="exploration")
    p_consult_sim.add_argument("--candidate-id", default="manual")
    p_consult_sim.add_argument("--quality-score", type=float, default=0.0)
    p_consult_sim.add_argument("--metric-ratio", type=float, default=0.0)
    p_consult_sim.add_argument("--delay-allowed", action="store_true")
    p_consult_sim.set_defaults(func=_cmd_consultant_simulate)

    p_submit = sub.add_parser("submit", help="Fail-closed Consultant submit queue.")
    submit_sub = p_submit.add_subparsers(dest="submit_command", required=True)
    p_dry = submit_sub.add_parser("dry-run")
    p_dry.add_argument("--database", default="research_memory.sqlite")
    p_dry.add_argument("--config", default=str(ROOT / "alpha_mining" / "config.yaml"))
    p_dry.set_defaults(func=_cmd_submit_dry_run)
    p_exec = submit_sub.add_parser("execute")
    p_exec.add_argument("--database", default="research_memory.sqlite")
    p_exec.add_argument("--config", default=str(ROOT / "alpha_mining" / "config.yaml"))
    p_exec.add_argument("--confirm", default="")
    p_exec.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_exec.add_argument("--max-submit", type=int, default=20)
    p_exec.set_defaults(func=_cmd_submit_execute)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
