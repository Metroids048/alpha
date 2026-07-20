from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from alpha_mining.generator.expression import ExpressionGenerator
from alpha_mining.storage.sqlite_store import SqliteRunLog


class _LLM:
    def __init__(self, payload=None):
        self.payload = payload or {
            "expressions": [
                {
                    "expression": "group_rank(foo, market) + ts_rank(foo, 21)",
                    "rationale": "test",
                }
            ]
        }
        self.user_prompt = ""
        self.schema = None

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        self.user_prompt = user_prompt
        self.schema = json_schema
        return self.payload


class _Validator:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def validate(self, expression):
        self.calls.append(expression)
        return self.result, "validator"


class _Factory:
    def __init__(self, gate=(True, "ok"), candidates=()):
        self.gate = gate
        self.candidates = list(candidates)
        self.calls = []

    def _quality_gate(self, expression):
        self.calls.append(expression)
        return self.gate

    def generate(self, *args, **kwargs):
        self.generate_args = (args, kwargs)
        return self.candidates


@dataclass
class _Candidate:
    expression: str
    family: str


def test_expression_generator_persists_one_llm_candidate(tmp_path):
    database = tmp_path / "research.sqlite"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO research_topics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "topic",
                "主题",
                "topic",
                "test",
                "fundamental",
                "desc",
                "test",
                "2026-01-01T00:00:00Z",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "hyp",
                "topic",
                "statement",
                "statement",
                "mechanism",
                "medium",
                None,
                "2026-01-01T00:00:00Z",
                "test",
                "active",
            ),
        )
        connection.execute(
            "INSERT INTO data_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "map",
                "hyp",
                "foo",
                "ds",
                "rationale",
                1.0,
                "test",
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.commit()

    generated = ExpressionGenerator(
        database,
        llm=_LLM(),
        validator=_Validator(),
        factory=_Factory(),
    ).generate_llm_grammar("hyp")

    assert len(generated) == 1
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT expression_text, generation_strategy, generation_layer FROM expressions"
        ).fetchone()
    assert row == (generated[0].expression_text, "llm_grammar", "L4")


def _database(tmp_path, *, mapping=True):
    database = tmp_path / "research.sqlite"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO research_topics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "topic",
                "主题",
                "topic",
                "test",
                "fundamental",
                "desc",
                "test",
                "2026-01-01T00:00:00Z",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "hyp",
                "topic",
                "statement",
                "statement",
                "mechanism",
                "medium",
                None,
                "2026-01-01T00:00:00Z",
                "test",
                "active",
            ),
        )
        if mapping:
            connection.execute(
                "INSERT INTO data_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "map",
                    "hyp",
                    "foo",
                    "ds",
                    "rationale",
                    1.0,
                    "test",
                    "2026-01-01T00:00:00Z",
                ),
            )
        connection.commit()
    return database


@pytest.mark.parametrize(
    "payload",
    [
        {"expressions": [{"expression": "x", "rationale": "r", "extra": "no"}]},
        {"expressions": [{"expression": "x"}]},
        {
            "expressions": [
                {"expression": "x", "rationale": "r"},
                {"expression": "x", "rationale": "r2"},
            ]
        },
        {"other": []},
    ],
)
def test_llm_structured_output_rejects_extra_missing_duplicate_and_unknown(
    tmp_path, payload
):
    generator = ExpressionGenerator(
        _database(tmp_path),
        llm=_LLM(payload),
        validator=_Validator(),
        factory=_Factory(),
    )
    with pytest.raises(Exception, match="expression|expressions|duplicate"):
        generator.generate_llm_grammar("hyp")


def test_prompt_contains_positive_and_negative_history(tmp_path):
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO expressions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "expr_old",
                "group_rank(foo, market) + ts_rank(foo, 42)",
                "old",
                "sig",
                "hyp",
                None,
                "legacy",
                "L4",
                None,
                "2026-01-01T00:00:00Z",
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO simulation_runs (expression_id, expression, fitness, sharpe) VALUES (?, ?, ?, ?)",
            ("expr_old", "group_rank(foo, market) + ts_rank(foo, 42)", 2.0, 1.5),
        )
        connection.execute(
            "INSERT INTO repairs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "repair",
                "expr_old",
                "low_sharpe",
                "detail",
                "shorten_window",
                None,
                0,
                "2026-01-02T00:00:00Z",
            ),
        )
        connection.commit()
    llm = _LLM()
    ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    assert "Positive history" in llm.user_prompt and "group_rank(foo" in llm.user_prompt
    assert (
        "Negative repair history" in llm.user_prompt
        and "shorten_window" in llm.user_prompt
    )


def test_quality_gate_and_validator_both_apply_and_mapping_is_required(tmp_path):
    database = _database(tmp_path)
    llm = _LLM(
        {
            "expressions": [
                {
                    "expression": "group_rank(bar, market) + ts_rank(bar, 21)",
                    "rationale": "r",
                }
            ]
        }
    )
    validator = _Validator()
    factory = _Factory()
    with pytest.raises(Exception, match="all expression candidates rejected"):
        ExpressionGenerator(
            database, llm=llm, validator=validator, factory=factory
        ).generate_llm_grammar("hyp")
    assert factory.calls and validator.calls == [
        "group_rank(bar, market) + ts_rank(bar, 21)"
    ]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expressions").fetchone()[0] == 0


def test_all_candidates_rejected_leave_expression_rows_unchanged(tmp_path):
    database = _database(tmp_path)
    generator = ExpressionGenerator(
        database,
        llm=_LLM(),
        validator=_Validator(False),
        factory=_Factory(),
    )
    with pytest.raises(Exception, match="all expression candidates rejected"):
        generator.generate_llm_grammar("hyp")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expressions").fetchone()[0] == 0


def test_template_delegate_strategy_null_hypothesis_and_idempotent(tmp_path):
    database = _database(tmp_path, mapping=False)
    candidates = [
        _Candidate("group_rank(foo, market) + ts_rank(foo, 21)", "arch_ts_rank")
    ]
    factory = _Factory(candidates=candidates)
    generator = ExpressionGenerator(
        database, llm=_LLM(), validator=_Validator(), factory=factory
    )
    first = generator.generate_templates(set(), set(), {}, set())
    second = generator.generate_templates(set(), set(), {}, set())
    assert first[0].generation_strategy == "template_arch_ts_rank"
    assert first[0].hypothesis_id is None
    assert first[0].expression_id == second[0].expression_id
    assert first[0].created_at == second[0].created_at
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT hypothesis_id, generation_strategy, structure_sig FROM expressions"
        ).fetchone()
        assert row[0] is None and row[1] == "template_arch_ts_rank" and row[2]
    assert factory.generate_args[0][0] == set()


def test_missing_active_hypothesis_has_clear_error(tmp_path):
    generator = ExpressionGenerator(
        _database(tmp_path), llm=_LLM(), validator=_Validator(), factory=_Factory()
    )
    with pytest.raises(Exception, match="active hypothesis"):
        generator.generate_llm_grammar("missing")


def test_expression_module_does_not_modify_legacy_production_file():
    source = Path("alpha_mining/generator/expression.py").read_text(encoding="utf-8")
    assert "import auto_alpha_pipeline_rebuilt_v50" not in source


def test_mapped_field_gate_is_token_bounded_and_case_insensitive(tmp_path):
    database = _database(tmp_path)
    llm = _LLM(
        {
            "expressions": [
                {
                    "expression": "group_rank(foobar, market) + ts_rank(foobar, 21)",
                    "rationale": "bad substring",
                },
                {
                    "expression": "group_rank(FOO, market) + ts_rank(FOO, 21)",
                    "rationale": "valid token",
                },
            ]
        }
    )
    generated = ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    assert [item.expression_text for item in generated] == [
        "group_rank(FOO, market) + ts_rank(FOO, 21)"
    ]


def test_history_prompt_filters_mapped_field_tokens_not_substrings(tmp_path):
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        for expression_id, expression_text in (
            ("expr_bad", "group_rank(foobar, market) + ts_rank(foobar, 21)"),
            ("expr_good", "group_rank(FOO, market) + ts_rank(FOO, 21)"),
        ):
            connection.execute(
                "INSERT INTO expressions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    expression_id,
                    expression_text,
                    expression_text,
                    "sig",
                    None,
                    None,
                    "legacy",
                    "L4",
                    None,
                    "2026",
                    None,
                    None,
                ),
            )
        connection.commit()
    llm = _LLM()
    ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    assert "group_rank(FOO" in llm.user_prompt
    assert "group_rank(foobar" not in llm.user_prompt


def test_prompt_contains_topic_and_mapping_metadata(tmp_path):
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE research_topics SET topic_name_cn=?, topic_name_en=?, category=?, description=?, source=? WHERE topic_id=?",
            (
                "主题元数据",
                "Metadata Topic",
                "family-x",
                "topic description",
                "journal-source",
                "topic",
            ),
        )
        connection.execute(
            "UPDATE data_mappings SET dataset_id=?, rationale=?, field_quality_score=? WHERE mapping_id=?",
            ("dataset-42", "mapping rationale", 4.25, "map"),
        )
        connection.commit()
    llm = _LLM()
    ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    for value in (
        "主题元数据",
        "Metadata Topic",
        "family-x",
        "topic description",
        "journal-source",
        "dataset-42",
        "mapping rationale",
        "4.25",
    ):
        assert value in llm.user_prompt


def test_normalized_duplicates_are_rejected_before_idempotent_insert(tmp_path):
    database = _database(tmp_path)
    payload = {
        "expressions": [
            {
                "expression": "group_rank(foo, market) + ts_rank(foo, 21)",
                "rationale": "one",
            },
            {
                "expression": "group_rank(foo,  market) + ts_rank(foo, 21)",
                "rationale": "same normalized",
            },
        ]
    }
    generator = ExpressionGenerator(
        database, llm=_LLM(payload), validator=_Validator(), factory=_Factory()
    )
    with pytest.raises(Exception, match="normalized|duplicate"):
        generator.generate_llm_grammar("hyp")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expressions").fetchone()[0] == 0


def test_template_family_strategy_follows_candidate_order(tmp_path):
    database = _database(tmp_path, mapping=False)
    candidates = [
        _Candidate("group_rank(foo, market) + ts_rank(foo, 21)", "family_a"),
        _Candidate("group_rank(bar, market) + ts_rank(bar, 21)", "family_b"),
    ]
    factory = _Factory(candidates=candidates)
    generated = ExpressionGenerator(
        database, llm=_LLM(), validator=_Validator(), factory=factory
    ).generate_templates(set(), set(), {}, set())
    assert [item.generation_strategy for item in generated] == [
        "template_family_a",
        "template_family_b",
    ]


def test_history_and_repairs_require_actual_mapped_token_even_when_linked(tmp_path):
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO expressions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "expr_linked_bad",
                "group_rank(foobar, market) + ts_rank(foobar, 21)",
                "foobar",
                "sig",
                "hyp",
                None,
                "legacy",
                "L4",
                None,
                "2026",
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO repairs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "repair_linked_bad",
                "expr_linked_bad",
                "failure",
                "detail",
                "repair foobar",
                None,
                0,
                "2026-01-03",
            ),
        )
        connection.commit()
    llm = _LLM()
    ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    assert "group_rank(foobar" not in llm.user_prompt
    assert "repair foobar" not in llm.user_prompt


def test_persist_returns_db_lineage_and_rejects_metadata_conflicts(tmp_path):
    database = _database(tmp_path, mapping=False)
    expression = "group_rank(foo, market) + ts_rank(foo, 21)"
    factory = _Factory(candidates=[_Candidate(expression, "arch_ts_rank")])
    generator = ExpressionGenerator(
        database, llm=_LLM(), validator=_Validator(), factory=factory
    )
    template = generator.generate_templates(set(), set(), {}, set())[0]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE expressions SET structure_sig=?, generation_layer=?, created_at=? WHERE expression_id=?",
            (
                template.structure_sig,
                template.generation_layer,
                "2000-01-01T00:00:00Z",
                template.expression_id,
            ),
        )
        connection.commit()
    result = generator.generate_templates(set(), set(), {}, set())[0]
    assert result.structure_sig == template.structure_sig
    assert result.generation_layer == template.generation_layer
    assert result.created_at == "2000-01-01T00:00:00Z"


def test_template_to_llm_same_expression_conflict_is_rejected(tmp_path):
    database = _database(tmp_path)
    expression = "group_rank(foo, market) + ts_rank(foo, 21)"
    generator = ExpressionGenerator(
        database,
        llm=_LLM(),
        validator=_Validator(),
        factory=_Factory(candidates=[_Candidate(expression, "arch_ts_rank")]),
    )
    generator.generate_templates(set(), set(), {}, set())
    with pytest.raises(Exception, match="conflict|metadata|hypothesis|strategy"):
        generator.generate_llm_grammar("hyp")


def test_default_callbacks_use_independent_domain_functions(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "alpha_mining.domain.expression_normalization.normalized_expression",
        lambda expression: (
            calls.append(("normalize", expression)) or "canonical-normalized"
        ),
    )
    monkeypatch.setattr(
        "alpha_mining.domain.expression_normalization.structure_signature",
        lambda expression: (
            calls.append(("structure", expression)) or "canonical-structure"
        ),
    )
    generator = ExpressionGenerator(
        _database(tmp_path), llm=_LLM(), validator=_Validator(), factory=_Factory()
    )
    generated = generator.generate_llm_grammar("hyp")[0]
    assert generated.normalized_text == "canonical-normalized"
    assert generated.structure_sig == "canonical-structure"
    assert {kind for kind, _ in calls} == {"normalize", "structure"}


def test_history_uses_one_simulation_run_without_cross_run_max_combination(tmp_path):
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        expression = "group_rank(foo, market) + ts_rank(foo, 42)"
        connection.execute(
            "INSERT INTO expressions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "expr_runs",
                expression,
                expression,
                "sig",
                None,
                None,
                "legacy",
                "L4",
                None,
                "2026",
                None,
                None,
            ),
        )
        connection.executemany(
            "INSERT INTO simulation_runs (expression_id, expression, fitness, sharpe) VALUES (?, ?, ?, ?)",
            [("expr_runs", expression, 9.0, 1.0), ("expr_runs", expression, 1.0, 9.0)],
        )
        connection.commit()
    llm = _LLM()
    ExpressionGenerator(
        database, llm=llm, validator=_Validator(), factory=_Factory()
    ).generate_llm_grammar("hyp")
    assert '"fitness": 9.0, "sharpe": 9.0' not in llm.user_prompt


def test_template_empty_factory_output_is_an_error(tmp_path):
    generator = ExpressionGenerator(
        _database(tmp_path, mapping=False),
        llm=_LLM(),
        validator=_Validator(),
        factory=_Factory(candidates=[]),
    )
    with pytest.raises(Exception, match="empty|no template|candidate"):
        generator.generate_templates(set(), set(), {}, set())


@pytest.mark.parametrize("attribute", ["expression", "family"])
def test_template_candidate_fields_must_be_non_empty_strings(tmp_path, attribute):
    candidate = _Candidate("valid", "family")
    setattr(candidate, attribute, None)
    generator = ExpressionGenerator(
        _database(tmp_path, mapping=False),
        llm=_LLM(),
        validator=_Validator(),
        factory=_Factory(candidates=[candidate]),
    )
    with pytest.raises(Exception, match=attribute):
        generator.generate_templates(set(), set(), {}, set())


def test_existing_different_expression_id_with_same_normalized_text_is_conflict(
    tmp_path,
):
    database = _database(tmp_path)
    expression = "group_rank(foo, market) + ts_rank(foo, 21)"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO expressions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "expr_bogus",
                expression,
                "same-normalized",
                "same-structure",
                "hyp",
                None,
                "llm_grammar",
                "L4",
                None,
                "2026",
                None,
                None,
            ),
        )
        connection.commit()
    generator = ExpressionGenerator(
        database,
        llm=_LLM(),
        validator=_Validator(),
        factory=_Factory(),
        normalizer=lambda _expression: "same-normalized",
        structure_signature=lambda _expression: "same-structure",
    )
    with pytest.raises(Exception, match="normalized.*conflict|conflict.*normalized"):
        generator.generate_llm_grammar("hyp")
