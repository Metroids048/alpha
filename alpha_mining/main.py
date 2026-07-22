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
    from alpha_mining.factory.control import FactoryControl
    from alpha_mining.generator.consultant_generator import ConsultantGenerator
    from alpha_mining.policy.consultant_policy import ConsultantPolicy

    state = FactoryControl(getattr(args, "database", "research_memory.sqlite")).status()
    if state.hard_stop:
        print(f"[consultant/generate] BLOCKED: {state.reason}", file=sys.stderr)
        return 2

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
    from alpha_mining.factory.control import FactoryControl
    from alpha_mining.policy.consultant_policy import ConsultantPolicy
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

    state = FactoryControl(getattr(args, "database", "research_memory.sqlite")).status()
    if state.hard_stop:
        print(f"[consultant/simulate] BLOCKED: {state.reason}", file=sys.stderr)
        return 2

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
    from alpha_mining.factory.control import FactoryControl
    from alpha_mining.policy.consultant_policy import ConsultantPolicy
    from alpha_mining.platform.gateway import PlatformGateway
    from alpha_mining.submitter.delivery import SubmissionDelivery
    from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard

    database = _ensure_database(args.database)
    policy = ConsultantPolicy.from_file(args.config)
    state = FactoryControl(database).status()
    if state.hard_stop or not state.execute_submit:
        print(f"[submit/execute] BLOCKED: factory hard stop ({state.reason})", file=sys.stderr)
        return 2
    if not args.execute_submit or not policy.execute_submit or args.confirm != policy.confirmation_phrase:
        print(
            "[submit/execute] BLOCKED: execute_submit is disabled or confirmation is invalid",
            file=sys.stderr,
        )
        return 2

    import sqlite3
    from datetime import datetime, timedelta, timezone

    # Verify fresh COMPLETE ledger with matching sync
    with sqlite3.connect(database) as con:
        sync_row = con.execute(
            "SELECT sync_id,status,completed_at FROM platform_sync_runs ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        if not sync_row or str(sync_row[1]) != "COMPLETE":
            print("[submit/execute] BLOCKED: no COMPLETE platform sync", file=sys.stderr)
            return 2
        try:
            completed = datetime.fromisoformat(str(sync_row[2]).replace("Z", "+00:00"))
            if completed < datetime.now(timezone.utc) - timedelta(hours=24):
                print("[submit/execute] BLOCKED: platform sync is stale", file=sys.stderr)
                return 2
        except (TypeError, ValueError):
            print("[submit/execute] BLOCKED: invalid sync timestamp", file=sys.stderr)
            return 2

        current_sync_id = str(sync_row[0])

        # Get ready candidates with all guards
        candidates = con.execute(
            """SELECT q.alpha_id,q.context_json,l.latest_checks_json,l.platform_status,
                      l.sync_id,d.description_status
               FROM consultant_submit_queue q
               JOIN platform_alpha_ledger l ON q.alpha_id=l.alpha_id
               LEFT JOIN description_backfill_jobs d ON q.alpha_id=d.alpha_id AND d.sync_id=l.sync_id
               WHERE q.status='READY' AND l.sync_id=?
               ORDER BY q.created_at,q.queue_id
               LIMIT ?""",
            (current_sync_id, max(0, int(args.max_submit))),
        ).fetchall()

    if not candidates:
        print(f"[submit/execute] endpoint_calls=0 candidates=0 allowed=0 blocked=0 submitted=0 failed=0")
        return 0

    gateway = PlatformGateway(
        state_path=args.auth_state_file,
        database=database,
    )
    delivery = SubmissionDelivery(database, gateway)
    guard = SubmissionGuard()

    submitted = 0
    failed = 0
    blocked = 0

    for alpha_id, context_json, checks_json, platform_status, ledger_sync_id, description_status in candidates:
        import json
        try:
            context_data = json.loads(context_json or "{}")
            checks = json.loads(checks_json or "[]")
        except (TypeError, ValueError):
            blocked += 1
            continue

        # Build context with fresh ledger and description status
        context_data["checks"] = checks
        context_data["platform_status"] = platform_status
        context_data["ledger_sync_id"] = current_sync_id
        context_data["candidate_sync_id"] = ledger_sync_id
        context_data["description_status"] = description_status or ""
        context_data["execute_submit_enabled"] = True

        # Query write intents for this alpha
        with sqlite3.connect(database) as con:
            intent_statuses = [
                str(row[0]) for row in con.execute(
                    "SELECT status FROM platform_write_intents WHERE alpha_id=? AND status IN ('PENDING','PROCESSING','UNCERTAIN')",
                    (alpha_id,),
                )
            ]
        context_data["write_intent_statuses"] = tuple(intent_statuses)
        context_data["unit_warnings"] = tuple(context_data.get("unit_warnings") or ())
        context_data["mandatory_checks"] = tuple(context_data.get("mandatory_checks") or ())

        try:
            context = CandidateContext(**context_data)
        except TypeError:
            blocked += 1
            continue

        decision = guard.evaluate(context)
        if not decision.allowed:
            blocked += 1
            continue

        result = delivery.submit_once(sync_id=current_sync_id, alpha_id=alpha_id, execute=True)
        if result.status.value == "VERIFIED":
            submitted += 1
        elif result.status.value == "FAILED":
            failed += 1
        else:
            blocked += 1

    counts = {
        "candidates": len(candidates),
        "submitted": submitted,
        "failed": failed,
        "blocked": blocked,
    }
    print(f"[submit/execute] {json.dumps(counts, sort_keys=True)}")
    return 0 if failed == 0 else 1


def _cmd_platform_ledger_sync(args: argparse.Namespace) -> int:
    from alpha_mining.platform.client import ReadOnlyPlatformClient
    from alpha_mining.platform.ledger import AlphaQueryFilters, PlatformLedgerSynchronizer

    from alpha_mining.platform.reporting import export_request_events, write_ledger_sync_report

    client = ReadOnlyPlatformClient(
        state_path=args.auth_state_file,
        min_interval=args.min_interval,
        database=args.database,
        lock_path=args.lock_path,
    )
    try:
        result = PlatformLedgerSynchronizer(args.database).sync(
            client,
            AlphaQueryFilters(
                status=args.status,
                region=args.region,
                universe=args.universe,
                delay=args.delay,
                hidden=args.hidden,
                alpha_type=args.alpha_type,
                date_created_gte=args.date_created_gte,
                date_created_lt=args.date_created_lt,
            ),
        )
        code = 0 if result.status == "COMPLETE" else 1
        output = result.__dict__
    except Exception as exc:
        code = 2
        output = {"status": "BLOCKED", "error_class": type(exc).__name__}
    write_ledger_sync_report(args.database, args.report)
    export_request_events(args.database, args.events_csv)
    print(json.dumps(output, default=str, sort_keys=True))
    return code


def _cmd_platform_probe(args: argparse.Namespace) -> int:
    from alpha_mining.platform.client import ReadOnlyPlatformClient
    from alpha_mining.platform.readiness import run_connectivity_probe
    from alpha_mining.platform.reporting import export_request_events

    client = ReadOnlyPlatformClient(
        state_path=args.auth_state_file,
        min_interval=args.min_interval,
        max_attempts=1,
        database=args.database,
        lock_path=args.lock_path,
    )
    result = run_connectivity_probe(
        client,
        database=args.database,
        output_path=args.output,
    )
    export_request_events(args.database, args.events_csv)
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0 if result.ready_for_ledger_sync else 2


def _cmd_platform_access_status(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from alpha_mining.auth.session_manager import auth_state_metadata
    from alpha_mining.platform.access import PlatformAccessController

    payload = asdict(PlatformAccessController(args.database, args.lock_path).status())
    payload.update(auth_state_metadata(args.auth_state_file))
    print(json.dumps(payload, sort_keys=True))
    return 0


def _cmd_platform_clear_auth(args: argparse.Namespace) -> int:
    from alpha_mining.auth.session_manager import clear_local_auth_artifacts

    if args.confirm != "CLEAR_STALE_AUTH":
        print("[platform/auth] BLOCKED: --confirm CLEAR_STALE_AUTH required", file=sys.stderr)
        return 2
    state = Path(args.auth_state_file)
    removed = clear_local_auth_artifacts(
        [state, Path(str(state) + ".lock"), ".wq_browser_cookie.json", ".wq_browser_cookie.next.json"]
    )
    print(json.dumps({"removed_count": len(removed)}, sort_keys=True))
    return 0


def _cmd_platform_recovery_report(args: argparse.Namespace) -> int:
    from alpha_mining.audit.access_recovery import write_access_recovery_reports

    result = write_access_recovery_reports(
        args.database,
        args.output_dir,
        auth_state_file=args.auth_state_file,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


def _cmd_factory_status(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from alpha_mining.factory.control import FactoryControl

    print(json.dumps(asdict(FactoryControl(args.database).status()), sort_keys=True))
    return 0


def _cmd_factory_stop(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from alpha_mining.factory.control import FactoryControl

    print(json.dumps(asdict(FactoryControl(args.database).stop(args.reason)), sort_keys=True))
    return 0


def _cmd_factory_audit(args: argparse.Namespace) -> int:
    from alpha_mining.audit.acceptance import run_acceptance_audit

    result = run_acceptance_audit(
        args.database,
        args.output_dir,
        external_blockers=args.external_blocker,
        verification_summary=args.verification_summary,
    )
    print(json.dumps({"status": result.status, "blockers": result.blockers}, sort_keys=True))
    return 0 if result.status == "PASS" else 1


def _cmd_description(args: argparse.Namespace) -> int:
    from alpha_mining.description.cli import DescriptionCliService

    service = DescriptionCliService(args.database)
    command = args.description_command
    if command == "inspect":
        return service.inspect(args.alpha_id)
    if command == "generate":
        return service.generate(args.alpha_id)
    if command == "validate":
        return service.validate(args.alpha_id)
    if command == "dry-run":
        return service.dry_run(args.alpha_id)
    if command == "patch":
        return service.patch(args.alpha_id, args.confirm)
    if command == "verify":
        return service.verify(args.alpha_id)
    if command == "backfill":
        return service.backfill(
            dry_run=args.dry_run, execute=args.execute, confirmation=args.confirm
        )
    if command == "resume":
        return service.resume(args.job_id)
    raise ValueError(f"unsupported description command: {command}")


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

    p_platform = sub.add_parser("platform", help="Authoritative platform Alpha ledger.")
    platform_sub = p_platform.add_subparsers(dest="platform_command", required=True)
    p_access_status = platform_sub.add_parser("access-status")
    p_access_status.add_argument("--database", default="research_memory.sqlite")
    p_access_status.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_access_status.add_argument("--lock-path", default="worldquant_api.lock")
    p_access_status.set_defaults(func=_cmd_platform_access_status)
    p_clear_auth = platform_sub.add_parser("clear-stale-auth")
    p_clear_auth.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_clear_auth.add_argument("--confirm", default="")
    p_clear_auth.set_defaults(func=_cmd_platform_clear_auth)
    p_probe = platform_sub.add_parser("probe")
    p_probe.add_argument("--database", default="research_memory.sqlite")
    p_probe.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_probe.add_argument("--lock-path", default="worldquant_api.lock")
    p_probe.add_argument("--min-interval", type=float, default=2.0)
    p_probe.add_argument("--output", default="platform_readiness.json")
    p_probe.add_argument("--events-csv", default="platform_request_events.csv")
    p_probe.set_defaults(func=_cmd_platform_probe)
    p_recovery_report = platform_sub.add_parser("recovery-report")
    p_recovery_report.add_argument("--database", default="research_memory.sqlite")
    p_recovery_report.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_recovery_report.add_argument("--output-dir", default=".")
    p_recovery_report.set_defaults(func=_cmd_platform_recovery_report)
    p_ledger_sync = platform_sub.add_parser("sync-ledger")
    p_ledger_sync.add_argument("--database", default="research_memory.sqlite")
    p_ledger_sync.add_argument("--auth-state-file", default=".wq_auth_state.json")
    p_ledger_sync.add_argument("--status", default="UNSUBMITTED")
    p_ledger_sync.add_argument("--region")
    p_ledger_sync.add_argument("--universe")
    p_ledger_sync.add_argument("--delay", type=int)
    p_ledger_sync.add_argument("--hidden", action=argparse.BooleanOptionalAction, default=None)
    p_ledger_sync.add_argument("--alpha-type")
    p_ledger_sync.add_argument("--date-created-gte")
    p_ledger_sync.add_argument("--date-created-lt")
    p_ledger_sync.add_argument("--lock-path", default="worldquant_api.lock")
    p_ledger_sync.add_argument("--min-interval", type=float, default=2.0)
    p_ledger_sync.add_argument("--report", default="platform_ledger_sync_report.json")
    p_ledger_sync.add_argument("--events-csv", default="platform_request_events.csv")
    p_ledger_sync.set_defaults(func=_cmd_platform_ledger_sync)

    p_factory = sub.add_parser("factory", help="vNext hard-stop and acceptance controls.")
    factory_sub = p_factory.add_subparsers(dest="factory_command", required=True)
    p_factory_status = factory_sub.add_parser("status")
    p_factory_status.add_argument("--database", default="research_memory.sqlite")
    p_factory_status.set_defaults(func=_cmd_factory_status)
    p_factory_stop = factory_sub.add_parser("stop")
    p_factory_stop.add_argument("--database", default="research_memory.sqlite")
    p_factory_stop.add_argument("--reason", default="manual_stop")
    p_factory_stop.set_defaults(func=_cmd_factory_stop)
    p_factory_audit = factory_sub.add_parser("audit")
    p_factory_audit.add_argument("--database", default="research_memory.sqlite")
    p_factory_audit.add_argument("--output-dir", default=".")
    p_factory_audit.add_argument("--external-blocker", action="append", default=[])
    p_factory_audit.add_argument("--verification-summary", default="not supplied")
    p_factory_audit.set_defaults(func=_cmd_factory_audit)

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
        command_parser.add_argument("--database", default="research_memory.sqlite")
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
    p_consult_sim.add_argument("--database", default="research_memory.sqlite")
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
    p_exec.add_argument("--execute-submit", action="store_true")
    p_exec.set_defaults(func=_cmd_submit_execute)

    p_description = sub.add_parser(
        "description", help="Fail-closed local-first alpha description operations."
    )
    description_sub = p_description.add_subparsers(
        dest="description_command", required=True
    )
    for name in ("inspect", "generate", "validate", "dry-run", "patch", "verify"):
        command_parser = description_sub.add_parser(name)
        command_parser.add_argument("--database", default="research_memory.sqlite")
        command_parser.add_argument("--alpha-id", required=True)
        if name == "patch":
            command_parser.add_argument("--confirm", default="")
        command_parser.set_defaults(func=_cmd_description)
    p_description_backfill = description_sub.add_parser("backfill")
    p_description_backfill.add_argument("--database", default="research_memory.sqlite")
    mode = p_description_backfill.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    p_description_backfill.add_argument("--confirm", default="")
    p_description_backfill.set_defaults(func=_cmd_description)
    p_description_resume = description_sub.add_parser("resume")
    p_description_resume.add_argument("--database", default="research_memory.sqlite")
    p_description_resume.add_argument("--job-id", required=True)
    p_description_resume.set_defaults(func=_cmd_description)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
