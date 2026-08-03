from __future__ import annotations


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
