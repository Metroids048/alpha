"""Thin adapter from the preserved v50 candidate generator to factory input."""

from __future__ import annotations

import hashlib
import importlib
import os
import re
from dataclasses import dataclass
from typing import Any

from alpha_mining.domain.expression_normalization import expression_identity, extract_fields
from alpha_mining.domain.operator_registry import BASE_VARS
from alpha_mining.generator.consultant_generator import KnowledgeUsageMode


def generate_candidates(*, knowledge_database: str | os.PathLike[str] | None = None) -> tuple[list[Any], Any]:
    """Return knowledge-primary candidates plus the preserved v50 fallback.

    The LLM path is deliberately opt-in because it can make an external model
    call.  A missing capability remains an honest v50 ``NONE`` source rather
    than a deterministic candidate carrying fabricated knowledge citations.
    """
    module = importlib.import_module("auto_alpha_pipeline_rebuilt_v50")
    config_type = getattr(module, "PipelineConfig")
    pipeline_type = getattr(module, "WorldQuantAlphaPipeline")
    pipeline = pipeline_type(
        config_type(
            username=os.environ.get("WQ_USERNAME", ""),
            password=os.environ.get("WQ_PASSWORD", ""),
            mode="generate",
            region="USA",
            universe="TOP3000",
            delay=1,
            auto_submit_when_passed=False,
            dry_run_submit=True,
            enable_fields_disk_cache=True,
        )
    )
    candidates, catalog = pipeline.generate_candidates()
    if knowledge_database is None or os.environ.get("ALPHA_ENABLE_KNOWLEDGE_LLM") != "1":
        return candidates, catalog
    return _knowledge_primary_candidates(candidates, catalog, database=knowledge_database), catalog


def _knowledge_primary_candidates(
    candidates: list[Any],
    catalog: Any,
    *,
    database: str | os.PathLike[str],
) -> list[Any]:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return candidates
    from alpha_mining.generator.llm_consultant_bridge import LLMConsultantBridge
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository
    from alpha_mining.llm.deepseek import DeepSeekStructuredLLM

    llm = DeepSeekStructuredLLM()
    generated: list[Any] = []
    try:
        bridge = LLMConsultantBridge(
            database=database,
            llm=llm,
            knowledge_repository=WorldQuantKnowledgeRepository(),
            max_per_hypothesis=1,
        )
        for index, candidate in enumerate(candidates[:3]):
            expression = str(getattr(candidate, "expression", "") or "")
            fields = tuple(field for field in extract_fields(expression) if field not in BASE_VARS)
            if not fields:
                continue
            family = _normalise_family(str(getattr(candidate, "family", "") or "v50"))
            source = str(getattr(candidate, "source", "") or "v50")
            generated.extend(bridge.generate(
                hypothesis_id=f"v50:{index}", family=family, mechanism=source,
                horizon="medium", fields=fields, dataset=_dataset_for(fields, catalog),
            ))
    finally:
        llm.close()
    return [*generated, *candidates]


def _dataset_for(fields: tuple[str, ...], catalog: Any) -> str:
    mapping = getattr(catalog, "field_dataset", {}) or {}
    datasets = {str(mapping.get(field) or "").strip() for field in fields}
    return next(iter(datasets)) if len(datasets) == 1 else ""


@dataclass(frozen=True)
class FactoryCandidateProposal:
    candidate_id: str
    expression: str
    topic_id: str
    hypothesis_id: str
    research_family: str
    strategy_family: str
    mechanism: str
    dataset: str
    parent_template: str
    exact_hash: str
    parameter_skeleton: str
    field_skeleton: str
    field_family: str
    generator_source: str = "v50"
    repair_origin: str = ""
    parent_candidate_id: str = ""
    knowledge_refs: tuple[str, ...] = ()
    knowledge_usage_mode: str = KnowledgeUsageMode.NONE.value
    context_refs: tuple[str, ...] = ()
    knowledge_context_hash: str = ""
    degraded: bool = False


def adapt_v50_candidate(candidate: Any, catalog: Any) -> FactoryCandidateProposal:
    """Adapt one v50 ``ExpressionCandidate`` without adding runtime behavior.

    The adapter deliberately rejects expressions with no owned data fields or
    with fields from more than one dataset.  It never guesses missing catalog
    metadata because the next stage must be able to validate the expression.
    """

    expression = str(getattr(candidate, "expression", "") or "").strip()
    if not expression:
        raise ValueError("v50 candidate expression is empty")
    identity = expression_identity(expression)
    fields = [field for field in extract_fields(expression) if field not in BASE_VARS]
    if not fields:
        raise ValueError("v50 candidate has no catalog data fields")

    field_dataset = getattr(catalog, "field_dataset", {}) or {}
    datasets = {str(field_dataset.get(field) or "").strip() for field in fields}
    if "" in datasets:
        missing = sorted(field for field in fields if not field_dataset.get(field))
        raise ValueError(f"v50 candidate references unknown fields: {', '.join(missing)}")
    if len(datasets) != 1:
        raise ValueError("v50 candidate mixes datasets: " + ", ".join(sorted(datasets)))

    family = _normalise_family(str(getattr(candidate, "family", "") or "v50"))
    source = str(getattr(candidate, "source", "") or "v50").strip() or "v50"
    candidate_id = hashlib.sha256(
        f"v50\0{identity.exact_hash}\0{family}\0{source}".encode("utf-8")
    ).hexdigest()
    dataset = next(iter(datasets))
    return FactoryCandidateProposal(
        candidate_id=candidate_id,
        expression=expression,
        topic_id="",
        hypothesis_id="",
        research_family=family,
        strategy_family=family,
        mechanism=source,
        dataset=dataset,
        parent_template=source,
        exact_hash=identity.exact_hash,
        parameter_skeleton=identity.parameter_skeleton,
        field_skeleton=identity.field_skeleton,
        field_family=dataset,
        generator_source=source,
        knowledge_refs=tuple(getattr(candidate, "knowledge_refs", ()) or ()),
        knowledge_usage_mode=_usage_mode(candidate),
        context_refs=tuple(getattr(candidate, "context_refs", ()) or ()),
        knowledge_context_hash=str(getattr(candidate, "knowledge_context_hash", "") or ""),
        degraded=bool(getattr(candidate, "degraded", False)),
    )


def _normalise_family(value: str) -> str:
    normalised = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalised or "v50"


def _usage_mode(candidate: Any) -> str:
    value = getattr(candidate, "knowledge_usage_mode", KnowledgeUsageMode.NONE)
    return str(getattr(value, "value", value) or KnowledgeUsageMode.NONE.value)
