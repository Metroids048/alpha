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


def generate_candidates() -> tuple[list[Any], Any]:
    """Invoke only the preserved v50 candidate-generation boundary."""
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
    return pipeline.generate_candidates()


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
    )


def _normalise_family(value: str) -> str:
    normalised = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalised or "v50"
