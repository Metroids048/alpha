"""诊断UNKNOWN_FIELD问题：检查field_snapshot与plan字段的匹配情况"""

import sys
from alpha_mining.generation.high_quality import HighQualityGenerator
from alpha_mining.snapshots.local_snapshots import LocalSnapshots
from alpha_mining.knowledge.context import KnowledgeContext

def main():
    # 加载快照
    snapshots = LocalSnapshots()
    knowledge = KnowledgeContext()

    # 统计field_snapshot
    print(f"\n=== Field Snapshot Stats ===")
    print(f"Total fields in catalog: {len(snapshots.catalog.fields)}")
    print(f"Catalog source: {snapshots.catalog.info.get('source', 'unknown')}")

    # 列出前20个字段
    print(f"\n=== First 20 Fields ===")
    for i, field in enumerate(list(snapshots.catalog.fields)[:20]):
        print(f"{i+1}. {field}")

    # 检查最近使用的字段
    print(f"\n=== Fields Used in Inventory ===")
    inventory_fields = set()
    for item in snapshots.inventory.records:
        if item.data_fields:
            inventory_fields.update(item.data_fields)
    print(f"Unique fields in inventory: {len(inventory_fields)}")
    print(f"Sample inventory fields: {list(inventory_fields)[:10]}")

    # 检查降级兜底会选择的字段
    from alpha_mining.generation.high_quality import _field_quality_component
    print(f"\n=== Top Fields by Quality ===")
    field_scores = []
    for field in list(snapshots.catalog.fields)[:100]:  # 只看前100个
        score = _field_quality_component((field,), snapshots)
        field_scores.append((field, score))
    field_scores.sort(key=lambda x: -x[1])
    for field, score in field_scores[:10]:
        print(f"{field}: {score:.3f}")

    # 模拟降级兜底选择的字段
    print(f"\n=== Simulated Fallback Field Selection ===")
    test_plan = {
        "fields_to_use": list(snapshots.catalog.fields)[:50],  # 取前50个
        "operators_to_use": ["ts_rank", "ts_zscore", "ts_mean", "rank"],
        "knowledge_refs": [item.ref_id for item in knowledge.snippets[:3]],
    }
    ranked_fields = sorted(
        test_plan["fields_to_use"],
        key=lambda item: (-_field_quality_component((item,), snapshots), item)
    )[:3]
    print(f"Top 3 fallback fields would be: {ranked_fields}")

    # 检查这些字段是否在catalog中
    print(f"\n=== Validation Check ===")
    for field in ranked_fields:
        in_catalog = field in snapshots.catalog.fields
        print(f"{field}: {'✓ IN' if in_catalog else '✗ NOT IN'} catalog")

if __name__ == "__main__":
    main()
