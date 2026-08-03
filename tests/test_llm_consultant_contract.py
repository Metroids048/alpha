from __future__ import annotations


class _StructuredLlm:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        self.calls.append((system_prompt, user_prompt, json_schema))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _repository(tmp_path):
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

    root = tmp_path / "World quant"
    root.mkdir()
    (root / "guide.md").write_text("# Momentum\n\nclose price momentum uses rank and decay.", encoding="utf-8")
    return WorldQuantKnowledgeRepository(root)


def test_bridge_uses_generate_json_and_accepts_at_most_three_referenced_candidates(tmp_path) -> None:
    from alpha_mining.generator.llm_consultant_bridge import LLMConsultantBridge

    llm = _StructuredLlm(
        {"candidates": [
            {
                "expression": "rank(close)", "strategy_family": "momentum",
                "economic_rationale": "price momentum", "knowledge_refs": ["worldquant:guide.md#1"],
                "expected_turnover_behavior": "moderate", "novelty_reason": "simple baseline",
            }
        ] * 4}
    )
    bridge = LLMConsultantBridge(database=tmp_path / "db.sqlite", llm=llm, knowledge_repository=_repository(tmp_path))

    candidates = bridge.generate(
        hypothesis_id="h1", family="momentum", mechanism="momentum", horizon="medium", fields=("close",)
    )

    assert len(candidates) == 3
    assert len(llm.calls) == 1
    assert candidates[0].knowledge_refs == ("worldquant:guide.md#1",)
    assert candidates[0].economic_rationale == "price momentum"


def test_bridge_fallback_is_deterministic_single_candidate_and_marked_degraded(tmp_path) -> None:
    from alpha_mining.generator.llm_consultant_bridge import LLMConsultantBridge

    bridge = LLMConsultantBridge(
        database=tmp_path / "db.sqlite", llm=_StructuredLlm(RuntimeError("offline")), knowledge_repository=_repository(tmp_path)
    )

    candidates = bridge.generate(
        hypothesis_id="h1", family="momentum", mechanism="momentum", horizon="medium", fields=("close",)
    )

    assert len(candidates) <= 1
    assert candidates and candidates[0].degraded is True
    assert candidates[0].knowledge_refs
