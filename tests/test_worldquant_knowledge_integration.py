from __future__ import annotations

from pathlib import Path


def test_repository_scans_real_markdown_and_returns_bounded_deterministic_context(tmp_path) -> None:
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

    root = tmp_path / "World quant"
    root.mkdir()
    (root / "operators.md").write_text(
        "# Rank\n\nrank creates a cross-sectional signal for price_volume close fields.\n\n"
        "# Decay\n\nts_decay_linear smooths momentum turnover.\n",
        encoding="utf-8",
    )
    (root / "notes.txt").write_text("not knowledge", encoding="utf-8")
    repository = WorldQuantKnowledgeRepository(root)

    first = repository.retrieve(dataset="price_volume", fields=("close",), mechanism="momentum")
    second = repository.retrieve(dataset="price_volume", fields=("close",), mechanism="momentum")

    assert first.completeness_status == "COMPLETE"
    assert first.snippets == second.snippets
    assert 1 <= len(first.snippets) <= 5
    assert sum(len(item.text) for item in first.snippets) <= 6000
    assert all(item.path.endswith("operators.md") for item in first.snippets)
    assert all(item.ref_id.startswith("worldquant:") for item in first.snippets)


def test_repository_reports_missing_when_no_real_markdown_exists(tmp_path) -> None:
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

    context = WorldQuantKnowledgeRepository(tmp_path / "missing").retrieve(
        dataset="ds", fields=("field",), mechanism="value"
    )

    assert context.snippets == ()
    assert context.completeness_status == "MISSING"
    assert context.missing_refs == ("WORLDQUANT_MARKDOWN",)


def test_idea_retrieval_excludes_index_and_requires_positive_relevance(tmp_path) -> None:
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

    root = tmp_path / "World quant"
    (root / "alpha_inspiration" / "posts").mkdir(parents=True)
    (root / "index.md").write_text("# Index\n\nprice_volume close momentum links", encoding="utf-8")
    (root / "alpha_inspiration" / "posts" / "momentum.md").write_text(
        "# Momentum\n\nPrice volume close momentum should use a decayed rank.", encoding="utf-8"
    )
    repository = WorldQuantKnowledgeRepository(root)

    context = repository.retrieve(dataset="price_volume", fields=("close",), mechanism="momentum")
    irrelevant = repository.retrieve(dataset="options", fields=("implied_volatility",), mechanism="volatility")

    assert context.completeness_status == "COMPLETE"
    assert all(item.path != "index.md" for item in context.snippets)
    assert context.context_hash
    assert irrelevant.snippets == ()
    assert irrelevant.completeness_status == "NO_RELEVANT_MATCH"


def test_context_is_bounded_per_source_and_per_snippet(tmp_path) -> None:
    from alpha_mining.knowledge.worldquant_repository import WorldQuantKnowledgeRepository

    root = tmp_path / "World quant" / "alpha_inspiration" / "posts"
    root.mkdir(parents=True)
    body = "close momentum " + ("evidence " * 400)
    (root / "one.md").write_text("\n".join(f"# {i}\n\n{body}" for i in range(4)), encoding="utf-8")
    (root / "two.md").write_text(f"# Two\n\n{body}", encoding="utf-8")

    context = WorldQuantKnowledgeRepository(root.parents[1]).retrieve(
        dataset="price_volume", fields=("close",), mechanism="momentum"
    )

    assert len(context.snippets) <= 5
    assert sum(len(item.text) for item in context.snippets) <= 6000
    assert all(len(item.text) <= 1200 for item in context.snippets)
    assert sum(item.path == "alpha_inspiration/posts/one.md" for item in context.snippets) <= 2


def test_real_chinese_worldquant_rules_are_retrievable_without_index_or_auth() -> None:
    from alpha_mining.knowledge.worldquant_repository import KnowledgeIntent, WorldQuantKnowledgeRepository

    root = Path("World quant")
    repository = WorldQuantKnowledgeRepository(root)
    self_corr = repository.retrieve(
        dataset="fundamental", fields=("基本面",), mechanism="自相关 相关性", intent=KnowledgeIntent.QUALITY_RULE,
    )
    workflow = repository.retrieve(
        dataset="fundamental", fields=("基本面",), mechanism="高质量 Alpha 工作流", intent=KnowledgeIntent.IDEA_GENERATION,
    )
    diversity = repository.retrieve(
        dataset="fundamental", fields=("基本面",), mechanism="算子多样性 假说优先", intent=KnowledgeIntent.QUALITY_RULE,
    )

    assert self_corr.snippets
    assert workflow.snippets
    assert diversity.snippets
    for context in (self_corr, workflow, diversity):
        assert all(item.path not in {"README.md", "核心：API认证问题及解决方案.md", "Alpha灵感启示录.md"} for item in context.snippets)
