import importlib.util
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "auto_alpha_pipeline_rebuilt_v50.py"
SPEC = importlib.util.spec_from_file_location(
    "auto_alpha_pipeline_rebuilt_v50", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _catalog() -> object:
    return MODULE.FieldCatalog(
        df=None,
        ids={"fnd6_sales", "cap"},
        by_ds={},
        fund=["fnd6_sales", "cap"],
        analyst=[],
        model=[],
        sent=[],
        pv=[],
        other=[],
    )


def test_phase4_pipeline_hooks_persist_tree_mutations_and_later_repair_outcome(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research-memory.sqlite3"
    cfg = MODULE.PipelineConfig(
        username="u",
        password="p",
        sqlite_runs_path=str(database),
        phase4_mutation_enabled=True,
        phase4_repair_enabled=True,
        feedback_ledger_filename=str(tmp_path / "feedback.csv"),
        hopeful_queue_filename=str(tmp_path / "hopeful.jsonl"),
        submission_results_filename=str(tmp_path / "submissions.jsonl"),
    )
    pipeline = MODULE.WorldQuantAlphaPipeline(cfg)
    validator = MODULE.PreflightValidator(_catalog())
    seed = "ts_rank(fnd6_sales/cap,126)"

    candidates = pipeline.phase4_mutate_near_pass_records(
        [{"expression": seed, "sharpe": 1.1, "settings": {"region": "USA"}}],
        validator,
        existing_expressions={seed},
    )

    assert candidates
    assert all(
        candidate["meta"]["family"] == "near_pass_variant_tree_mutation"
        for candidate in candidates
    )
    assert all(
        candidate["meta"]["source"] == "phase4_tree_mutation"
        for candidate in candidates
    )
    assert all(candidate["expression"] != seed for candidate in candidates)
    assert all(candidate["settings"] == {"region": "USA"} for candidate in candidates)

    repaired = "group_neutralize(rank(ts_rank(fnd6_sales/cap,126)), market)"
    failed_payload = {
        "regular": seed,
        "settings": {"region": "USA"},
        "meta": {
            "family": "near_pass_variant_tree_mutation",
            "source": "phase4_tree_mutation",
        },
    }
    pipeline._append_feedback(
        failed_payload,
        "alpha-failed",
        {"id": "sim-failed"},
        "ok",
        False,
        "LOW_SHARPE",
        "not_queued:low_sharpe",
        merged_json={"is": {"sharpe": 0.4, "fitness": 1.1, "turnover": 0.2}},
    )

    with sqlite3.connect(database) as connection:
        repair_row = connection.execute(
            "SELECT resulting_expression_id, success FROM repairs"
        ).fetchone()
        assert repair_row is not None
        resulting_expression_id, success = repair_row
        assert success is None
        assert connection.execute(
            "SELECT expression_text FROM expressions WHERE expression_id=?",
            (resulting_expression_id,),
        ).fetchone() == (repaired,)
        assert connection.execute("SELECT COUNT(*) FROM mutations").fetchone() == (
            len(candidates),
        )

    pipeline._append_feedback(
        {
            "regular": repaired,
            "settings": {"region": "USA"},
            "meta": {"family": "repair", "source": "phase4_repair"},
        },
        "alpha-repaired",
        {"id": "sim-repaired"},
        "ok",
        True,
        "all checks passed",
        "ready",
        merged_json={"is": {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.2}},
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT success FROM repairs").fetchone() == (1,)
