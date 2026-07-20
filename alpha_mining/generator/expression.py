"""L4 grammar-constrained expression generation and persistence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from alpha_mining.generator.hypothesis import StructuredLLM
from alpha_mining.storage.sqlite_store import SqliteRunLog


class ExpressionGenerationError(RuntimeError):
    """Structured output was invalid or every candidate failed validation."""


class HypothesisContextNotFound(LookupError):
    """The requested hypothesis is not active or has no active topic."""


class ExpressionValidator(Protocol):
    def validate(self, expression: str) -> tuple[bool, str] | bool: ...


class ExpressionFactoryLike(Protocol):
    def generate(
        self,
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: Any,
        library_skeletons: set[str],
        *,
        tried_exact: set[str] | None = None,
    ) -> Sequence[Any]: ...


@dataclass(frozen=True)
class GeneratedExpression:
    expression_id: str
    expression_text: str
    normalized_text: str
    structure_sig: str
    hypothesis_id: str | None
    generation_strategy: str
    generation_layer: str
    rationale: str
    created_at: str


EXPRESSION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "expressions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "expression": {"type": "string", "minLength": 1},
                    "rationale": {"type": "string", "minLength": 1},
                },
                "required": ["expression", "rationale"],
            },
        }
    },
    "required": ["expressions"],
}


def _fallback_normalize(expression: str) -> str:
    return re.sub(r"\s+", " ", expression.strip())


def _fallback_structure(expression: str) -> str:
    normalized = _fallback_normalize(expression).lower()
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "N", normalized)
    normalized = re.sub(r"[a-z_][a-z0-9_]*", "F", normalized)
    return normalized


def _default_normalize(expression: str) -> str:
    from alpha_mining.domain.expression_normalization import normalized_expression

    return normalized_expression(expression)


def _default_structure_signature(expression: str) -> str:
    from alpha_mining.domain.expression_normalization import structure_signature

    return structure_signature(expression)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _expression_id(normalized_text: str) -> str:
    return "expr_" + hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:24]


def _contains_mapped_field(expression: str, field: str) -> bool:
    field = str(field or "").strip()
    if not field:
        return False
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])",
            expression,
            flags=re.IGNORECASE,
        )
        is not None
    )


class ExpressionGenerator:
    """Vendor-neutral L4 facade with injected LLM, validator and factory."""

    def __init__(
        self,
        database: str | Path,
        *,
        llm: StructuredLLM,
        validator: ExpressionValidator,
        factory: ExpressionFactoryLike,
        normalizer: Callable[[str], str] | None = None,
        structure_signature: Callable[[str], str] | None = None,
    ) -> None:
        self.database = Path(database).expanduser().resolve()
        self.llm = llm
        self.validator = validator
        self.factory = factory
        self.normalizer = normalizer or _default_normalize
        self.structure_signature = structure_signature or _default_structure_signature
        SqliteRunLog(self.database).initialize_schema()

    def _quality_gate(self, expression: str) -> tuple[bool, str]:
        gate = getattr(self.factory, "_quality_gate", None)
        if not callable(gate):
            raise ValueError("factory must provide callable _quality_gate")
        result = gate(expression)
        if isinstance(result, tuple):
            return bool(result[0]), str(result[1] if len(result) > 1 else "rejected")
        return bool(result), "ok" if result else "rejected"

    def _require_quality_gate(self) -> None:
        if not callable(getattr(self.factory, "_quality_gate", None)):
            raise ValueError("factory must provide callable _quality_gate")

    def _validator(self, expression: str) -> tuple[bool, str]:
        result = self.validator.validate(expression)
        if isinstance(result, tuple):
            return bool(result[0]), str(result[1] if len(result) > 1 else "rejected")
        return bool(result), "ok" if result else "rejected"

    @staticmethod
    def _validate_structured_output(
        raw: Mapping[str, Any], limit: int
    ) -> list[tuple[str, str]]:
        if not isinstance(raw, Mapping) or set(raw) != {"expressions"}:
            raise ExpressionGenerationError(
                "LLM output must contain only an expressions array"
            )
        rows = raw.get("expressions")
        if not isinstance(rows, list) or not 1 <= len(rows) <= limit:
            raise ExpressionGenerationError(
                f"expressions must contain 1 to {limit} items"
            )
        validated: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"expression", "rationale"}:
                raise ExpressionGenerationError(
                    "each expression must contain only expression and rationale"
                )
            expression = row.get("expression")
            rationale = row.get("rationale")
            if not isinstance(expression, str) or not expression.strip():
                raise ExpressionGenerationError("expression must be a non-empty string")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ExpressionGenerationError("rationale must be a non-empty string")
            expression = expression.strip()
            if expression in seen:
                raise ExpressionGenerationError(f"duplicate expression: {expression}")
            seen.add(expression)
            validated.append((expression, rationale.strip()))
        return validated

    def _context(
        self, hypothesis_id: str
    ) -> tuple[
        str,
        str,
        str,
        str,
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT h.statement_cn, h.mechanism, h.horizon,
                       t.topic_id, t.topic_name_cn, t.topic_name_en, t.category,
                       t.data_category, t.description, t.source
                FROM hypotheses h JOIN research_topics t ON t.topic_id = h.topic_id
                WHERE h.hypothesis_id=? AND h.status='active' AND t.active=1
                """,
                (hypothesis_id,),
            ).fetchone()
            if row is None:
                raise HypothesisContextNotFound(
                    f"active hypothesis context not found: {hypothesis_id}"
                )
            mappings = [
                dict(r)
                for r in connection.execute(
                    """
                SELECT data_field, dataset_id, rationale, field_quality_score
                FROM data_mappings WHERE hypothesis_id=? ORDER BY created_at, mapping_id
                """,
                    (hypothesis_id,),
                )
            ]
            fields = [str(mapping["data_field"] or "") for mapping in mappings]
            historical_rows = [
                dict(r)
                for r in connection.execute(
                    """
                SELECT e.expression_id, e.expression_text, e.hypothesis_id,
                       sr.fitness, sr.sharpe
                FROM expressions e
                LEFT JOIN simulation_runs sr ON sr.expression_id=e.expression_id OR sr.expression=e.expression_text
                """
                )
            ]

            def relevant(expression_text: str, expression_hypothesis_id: Any) -> bool:
                del expression_hypothesis_id
                return any(
                    _contains_mapped_field(expression_text, field) for field in fields
                )

            positive_candidates = [
                {
                    key: value
                    for key, value in row_data.items()
                    if key in {"expression_text", "fitness", "sharpe"}
                }
                for row_data in historical_rows
                if relevant(
                    str(row_data.get("expression_text") or ""),
                    row_data.get("hypothesis_id"),
                )
            ]
            positive_candidates.sort(
                key=lambda item: (
                    item.get("fitness") if item.get("fitness") is not None else -1e99,
                    item.get("sharpe") if item.get("sharpe") is not None else -1e99,
                ),
                reverse=True,
            )
            positive: list[dict[str, Any]] = []
            seen_positive: set[str] = set()
            for item in positive_candidates:
                expression_text = str(item.get("expression_text") or "")
                if expression_text in seen_positive:
                    continue
                seen_positive.add(expression_text)
                positive.append(item)
                if len(positive) == 8:
                    break
            repair_rows = [
                dict(r)
                for r in connection.execute(
                    """
                SELECT r.failure_category, r.failure_detail, r.repair_strategy, r.success,
                       r.created_at, e.expression_text, e.hypothesis_id
                FROM repairs r JOIN expressions e ON e.expression_id=r.expression_id
                ORDER BY r.created_at DESC
                """
                )
            ]
            repairs = [
                {
                    key: value
                    for key, value in row_data.items()
                    if key
                    in {
                        "failure_category",
                        "failure_detail",
                        "repair_strategy",
                        "success",
                        "created_at",
                        "expression_text",
                    }
                }
                for row_data in repair_rows
                if relevant(
                    str(row_data.get("expression_text") or ""),
                    row_data.get("hypothesis_id"),
                )
            ][:12]
            topic = {
                "topic_id": str(row[3] or ""),
                "topic_name_cn": str(row[4] or ""),
                "topic_name_en": str(row[5] or ""),
                "category": str(row[6] or ""),
                "data_category": str(row[7] or ""),
                "description": str(row[8] or ""),
                "source": str(row[9] or ""),
            }
        return (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or ""),
            topic["data_category"],
            topic,
            mappings,
            positive,
            repairs,
        )

    @staticmethod
    def _prompt(
        hypothesis_id: str,
        statement: str,
        mechanism: str,
        horizon: str,
        data_category: str,
        topic: Mapping[str, Any],
        mappings: Sequence[Mapping[str, Any]],
        positive: Sequence[Mapping[str, Any]],
        repairs: Sequence[Mapping[str, Any]],
    ) -> str:
        positive_text = (
            json.dumps(list(positive), ensure_ascii=False)
            if positive
            else "empty history"
        )
        repair_text = (
            json.dumps(list(repairs), ensure_ascii=False)
            if repairs
            else "empty history"
        )
        fields = [str(mapping.get("data_field") or "") for mapping in mappings]
        field_text = (
            json.dumps(fields, ensure_ascii=False) if fields else "empty history"
        )
        mapping_text = (
            json.dumps(list(mappings), ensure_ascii=False)
            if mappings
            else "empty history"
        )
        topic_text = json.dumps(dict(topic), ensure_ascii=False)
        return (
            f"Hypothesis id: {hypothesis_id}\nStatement: {statement}\nMechanism: {mechanism}\n"
            f"Horizon: {horizon}\nData category: {data_category}\nMapped fields: {field_text}\n"
            f"Topic metadata: {topic_text}\nData mapping metadata: {mapping_text}\n"
            f"Positive history (fitness/sharpe descending): {positive_text}\n"
            f"Negative repair history (recent first): {repair_text}\n"
            "Only output expressions in the restricted grammar. Do not generate arbitrary calls, URLs, "
            "or condition strings. Return only data matching the supplied JSON schema."
        )

    def _persist(
        self,
        candidates: Sequence[tuple[str, str]],
        *,
        hypothesis_id: str | None,
        strategy_for: Callable[[str], str],
    ) -> tuple[GeneratedExpression, ...]:
        timestamp = _utc_now()
        rows: list[GeneratedExpression] = []
        normalized_seen: dict[str, str] = {}
        for expression, rationale in candidates:
            normalized = self.normalizer(expression)
            if not isinstance(normalized, str) or not normalized.strip():
                raise ExpressionGenerationError("normalizer returned an empty value")
            normalized = normalized.strip()
            previous = normalized_seen.get(normalized)
            if previous is not None:
                raise ExpressionGenerationError(
                    f"duplicate normalized expression in batch: {previous!r} and {expression!r}"
                )
            normalized_seen[normalized] = expression
            structure = self.structure_signature(expression)
            if not isinstance(structure, str) or not structure.strip():
                raise ExpressionGenerationError(
                    "structure_signature returned an empty value"
                )
            rows.append(
                GeneratedExpression(
                    expression_id=_expression_id(normalized),
                    expression_text=expression,
                    normalized_text=normalized,
                    structure_sig=structure,
                    hypothesis_id=hypothesis_id,
                    generation_strategy=strategy_for(expression),
                    generation_layer="L4",
                    rationale=rationale,
                    created_at=timestamp,
                )
            )
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            existing_by_id: dict[str, dict[str, Any]] = {}
            existing_by_normalized: dict[str, dict[str, Any]] = {}
            if rows:
                existing_rows = [
                    dict(row)
                    for row in connection.execute(
                        """
                    SELECT expression_id, expression_text, normalized_text, structure_sig,
                           hypothesis_id, generation_strategy, generation_layer, created_at
                    FROM expressions WHERE expression_id IN (%s) OR normalized_text IN (%s)
                    """
                        % (",".join("?" for _ in rows), ",".join("?" for _ in rows)),
                        [item.expression_id for item in rows]
                        + [item.normalized_text for item in rows],
                    )
                ]
                existing_by_id = {
                    str(row["expression_id"]): row for row in existing_rows
                }
                existing_by_normalized = {
                    str(row["normalized_text"]): row for row in existing_rows
                }
                for item in rows:
                    prior_by_normalized = existing_by_normalized.get(
                        item.normalized_text
                    )
                    if (
                        prior_by_normalized is not None
                        and str(prior_by_normalized["expression_id"])
                        != item.expression_id
                    ):
                        raise ExpressionGenerationError(
                            f"normalized expression conflict: {item.normalized_text!r} belongs to "
                            f"{prior_by_normalized['expression_id']}, expected {item.expression_id}"
                        )
                    prior = (
                        existing_by_id.get(item.expression_id) or prior_by_normalized
                    )
                    if prior is None:
                        continue
                    mismatches = []
                    for field, expected in (
                        ("expression_text", item.expression_text),
                        ("normalized_text", item.normalized_text),
                        ("structure_sig", item.structure_sig),
                        ("hypothesis_id", item.hypothesis_id),
                        ("generation_strategy", item.generation_strategy),
                        ("generation_layer", item.generation_layer),
                    ):
                        if prior.get(field) != expected:
                            mismatches.append(field)
                    if mismatches:
                        raise ExpressionGenerationError(
                            f"existing expression lineage conflict for {item.expression_id}: {', '.join(mismatches)}"
                        )
            for item in rows:
                connection.execute(
                    """
                    INSERT INTO expressions (
                        expression_id, expression_text, normalized_text, structure_sig,
                        hypothesis_id, parent_expression_id, generation_strategy, generation_layer,
                        embedding, created_at, submission_priority_score, novelty_score
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, NULL, NULL)
                    ON CONFLICT(expression_id) DO NOTHING
                    """,
                    (
                        item.expression_id,
                        item.expression_text,
                        item.normalized_text,
                        item.structure_sig,
                        item.hypothesis_id,
                        item.generation_strategy,
                        item.generation_layer,
                        item.created_at,
                    ),
                )
            persisted_rows = (
                {
                    str(row["expression_id"]): dict(row)
                    for row in connection.execute(
                        """
                    SELECT expression_id, expression_text, normalized_text, structure_sig,
                           hypothesis_id, generation_strategy, generation_layer, created_at
                    FROM expressions WHERE expression_id IN (%s)
                    """
                        % ",".join("?" for _ in rows),
                        [item.expression_id for item in rows],
                    )
                }
                if rows
                else {}
            )
        return tuple(
            GeneratedExpression(
                expression_id=str(
                    (db_item := persisted_rows[item.expression_id])["expression_id"]
                ),
                expression_text=str(db_item["expression_text"]),
                normalized_text=str(db_item["normalized_text"]),
                structure_sig=str(db_item["structure_sig"] or ""),
                hypothesis_id=db_item["hypothesis_id"],
                generation_strategy=str(db_item["generation_strategy"]),
                generation_layer=str(db_item["generation_layer"]),
                rationale=item.rationale,
                created_at=str(db_item["created_at"]),
            )
            for item in rows
        )

    def generate_llm_grammar(
        self, hypothesis_id: str, limit: int = 8
    ) -> tuple[GeneratedExpression, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self._require_quality_gate()
        (
            statement,
            mechanism,
            horizon,
            data_category,
            topic,
            mappings,
            positive,
            repairs,
        ) = self._context(hypothesis_id)
        raw = self.llm.generate_json(
            system_prompt=(
                "You are a quantitative expression generator. Use only the restricted grammar "
                "and never emit arbitrary code, URLs, or conditions."
            ),
            user_prompt=self._prompt(
                hypothesis_id,
                statement,
                mechanism,
                horizon,
                data_category,
                topic,
                mappings,
                positive,
                repairs,
            ),
            json_schema={
                **EXPRESSION_JSON_SCHEMA,
                "properties": {
                    "expressions": {
                        **EXPRESSION_JSON_SCHEMA["properties"]["expressions"],
                        "maxItems": limit,
                    }
                },
            },
        )
        try:
            proposed = self._validate_structured_output(raw, limit)
        except ExpressionGenerationError:
            raise
        accepted: list[tuple[str, str]] = []
        rejected: list[str] = []
        for expression, rationale in proposed:
            gate_ok, gate_reason = self._quality_gate(expression)
            if not gate_ok:
                rejected.append(f"{expression}: quality gate ({gate_reason})")
                continue
            valid, validation_reason = self._validator(expression)
            if not valid:
                rejected.append(f"{expression}: validator ({validation_reason})")
                continue
            fields = [str(mapping.get("data_field") or "") for mapping in mappings]
            if not fields or not any(
                _contains_mapped_field(expression, field) for field in fields
            ):
                rejected.append(f"{expression}: no mapped data field")
                continue
            accepted.append((expression, rationale))
        if not accepted:
            detail = "; ".join(rejected) or "no candidates returned"
            raise ExpressionGenerationError(
                f"all expression candidates rejected: {detail}"
            )
        return self._persist(
            accepted,
            hypothesis_id=hypothesis_id,
            strategy_for=lambda _expr: "llm_grammar",
        )

    def generate_templates(
        self,
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: Any,
        library_skeletons: set[str],
        tried_exact: set[str] | None = None,
    ) -> tuple[GeneratedExpression, ...]:
        self._require_quality_gate()
        generated = self.factory.generate(
            history_seen,
            history_skeletons,
            history_pools,
            library_skeletons,
            tried_exact=tried_exact,
        )
        try:
            generated = list(generated)
        except TypeError:
            raise ExpressionGenerationError(
                "factory.generate must return an iterable of candidates"
            ) from None
        if not generated:
            raise ExpressionGenerationError(
                "factory.generate returned no template candidates"
            )
        accepted: list[tuple[str, str, str]] = []
        rejected: list[str] = []
        for candidate in generated:
            raw_expression = getattr(candidate, "expression", None)
            raw_family = getattr(candidate, "family", None)
            if not isinstance(raw_expression, str) or not raw_expression.strip():
                rejected.append("expression must be a non-empty string")
                continue
            if not isinstance(raw_family, str) or not raw_family.strip():
                rejected.append("family must be a non-empty string")
                continue
            expression = raw_expression.strip()
            family = raw_family.strip()
            gate_ok, gate_reason = self._quality_gate(expression)
            if not gate_ok:
                rejected.append(f"{expression}: quality gate ({gate_reason})")
                continue
            valid, validation_reason = self._validator(expression)
            if not valid:
                rejected.append(f"{expression}: validator ({validation_reason})")
                continue
            accepted.append((expression, "legacy ExpressionFactory template", family))
        if not accepted and rejected:
            raise ExpressionGenerationError(
                f"all template candidates rejected: {'; '.join(rejected)}"
            )
        strategy_iter = iter(accepted)
        return self._persist(
            [(expression, rationale) for expression, rationale, _family in accepted],
            hypothesis_id=None,
            strategy_for=lambda _expression: f"template_{next(strategy_iter)[2]}",
        )


__all__ = [
    "EXPRESSION_JSON_SCHEMA",
    "ExpressionGenerationError",
    "ExpressionGenerator",
    "GeneratedExpression",
    "HypothesisContextNotFound",
]
