"""Structured LLM consultant bridge with deterministic, attributable fallback."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from alpha_mining.generator.consultant_generator import ConsultantCandidate, ConsultantGenerator
from alpha_mining.knowledge.worldquant_repository import KnowledgeContext, WorldQuantKnowledgeRepository


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": [
                    "expression", "strategy_family", "economic_rationale", "knowledge_refs",
                    "expected_turnover_behavior", "novelty_reason",
                ],
            },
        }
    },
}


class LLMConsultantBridge:
    """Accepts only JSON candidates tied to retrieved, local knowledge refs."""

    def __init__(
        self,
        *,
        database: str | Path,
        llm: Any,
        knowledge_repository: WorldQuantKnowledgeRepository | None = None,
        max_per_hypothesis: int = 3,
    ) -> None:
        self.database = Path(database)
        self.llm = llm
        self.knowledge_repository = knowledge_repository or WorldQuantKnowledgeRepository()
        self.max_per_hypothesis = min(3, max(1, int(max_per_hypothesis)))

    def generate(
        self,
        *,
        hypothesis_id: str,
        family: str,
        mechanism: str,
        horizon: str,
        fields: tuple[str, ...],
        parent_expression: str = "",
        dataset: str = "",
    ) -> list[ConsultantCandidate]:
        context = self.knowledge_repository.retrieve(
            dataset=dataset,
            fields=tuple(fields),
            mechanism=mechanism,
        )
        if not context.snippets:
            return []
        try:
            raw = self.llm.generate_json(
                system_prompt=(
                    "You generate WorldQuant FASTEXPR candidates. Return JSON only; do not invent "
                    "field names or knowledge references."
                ),
                user_prompt=self._user_prompt(family, mechanism, horizon, fields, context),
                json_schema=_SCHEMA,
            )
            return self._from_response(raw, hypothesis_id, family, context)
        except Exception:
            return self._fallback(hypothesis_id, family, mechanism, horizon, fields, parent_expression, context)

    def _from_response(
        self,
        raw: Any,
        hypothesis_id: str,
        family: str,
        context: KnowledgeContext,
    ) -> list[ConsultantCandidate]:
        rows = raw.get("candidates", []) if isinstance(raw, dict) else []
        allowed_refs = {snippet.ref_id for snippet in context.snippets}
        candidates: list[ConsultantCandidate] = []
        for row in rows[: self.max_per_hypothesis]:
            if not isinstance(row, dict):
                continue
            expression = str(row.get("expression") or "").strip()
            rationale = str(row.get("economic_rationale") or "").strip()
            refs = tuple(str(ref) for ref in row.get("knowledge_refs", []) if str(ref) in allowed_refs)
            turnover = str(row.get("expected_turnover_behavior") or "").strip()
            if not (expression and rationale and refs and turnover):
                continue
            candidate_id = "llm_" + hashlib.sha256(
                f"{hypothesis_id}\0{expression}".encode("utf-8")
            ).hexdigest()[:24]
            candidates.append(
                ConsultantCandidate(
                    candidate_id=candidate_id,
                    hypothesis_id=hypothesis_id,
                    family=family,
                    mutation_type="llm_structured",
                    expression=expression,
                    economic_rationale=rationale,
                    knowledge_refs=refs,
                    expected_signal=str(row.get("novelty_reason") or ""),
                    expected_turnover_behavior=turnover,
                )
            )
        return candidates

    def _fallback(
        self,
        hypothesis_id: str,
        family: str,
        mechanism: str,
        horizon: str,
        fields: tuple[str, ...],
        parent_expression: str,
        context: KnowledgeContext,
    ) -> list[ConsultantCandidate]:
        generated = ConsultantGenerator(max_per_hypothesis=1, max_same_behavior=1).generate(
            hypothesis_id=hypothesis_id,
            family=family,
            mechanism=mechanism,
            horizon=horizon,
            fields=fields,
            parent_expression=parent_expression,
        )
        if not generated:
            return []
        item = generated[0]
        return [
            ConsultantCandidate(
                candidate_id=item.candidate_id,
                hypothesis_id=item.hypothesis_id,
                family=item.family,
                mutation_type="deterministic_fallback",
                expression=item.expression,
                economic_rationale=f"Deterministic fallback for {mechanism}",
                knowledge_refs=(context.snippets[0].ref_id,),
                expected_signal="fallback",
                expected_turnover_behavior="unknown",
                degraded=True,
            )
        ]

    @staticmethod
    def _user_prompt(
        family: str,
        mechanism: str,
        horizon: str,
        fields: tuple[str, ...],
        context: KnowledgeContext,
    ) -> str:
        evidence = "\n".join(f"[{item.ref_id}] {item.text}" for item in context.snippets)
        return (
            f"family={family}\nmechanism={mechanism}\nhorizon={horizon}\n"
            f"allowed_fields={', '.join(fields)}\nknowledge:\n{evidence}"
        )
