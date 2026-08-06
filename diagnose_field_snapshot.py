"""诊断field_snapshot中的字段数量和来源"""

from pathlib import Path
from alpha_mining.generation.snapshots import load_local_snapshots

def main():
    print("\n=== 加载本地快照 ===")
    snapshots = load_local_snapshots(root=".", allow_partial_offline=True)

    print(f"\nCatalog 来源: {snapshots.catalog_source}")
    print(f"Catalog 目录: {snapshots.catalog_dir}")
    print(f"Catalog 年龄: {snapshots.catalog_age_hours:.1f} 小时")
    print(f"Catalog 信息: {snapshots.catalog.info}")

    print(f"\n=== 字段统计 ===")
    print(f"总字段数: {len(snapshots.catalog.fields)}")
    print(f"总操作符数: {len(snapshots.catalog.operators)}")
    print(f"总数据集数: {len(snapshots.catalog.datasets)}")

    # 列出前30个字段
    print(f"\n=== 前30个字段 ===")
    for i, field_id in enumerate(sorted(snapshots.catalog.fields.keys())[:30], 1):
        field = snapshots.catalog.fields[field_id]
        print(f"{i}. {field_id} (dataset={field.dataset_id}, type={field.field_type})")

    # 检查inventory中使用的字段
    print(f"\n=== Inventory中使用的字段 ===")
    inventory_fields = set()
    for item in snapshots.inventory.records:
        if item.data_fields:
            inventory_fields.update(item.data_fields)

    print(f"Inventory中唯一字段数: {len(inventory_fields)}")
    unknown_fields = [f for f in inventory_fields if f not in snapshots.catalog.fields]
    if unknown_fields:
        print(f"\n⚠️ Inventory中有 {len(unknown_fields)} 个字段不在catalog中:")
        for field in sorted(unknown_fields)[:10]:
            print(f"  - {field}")
    else:
        print("✓ Inventory中所有字段都在catalog中")

    # 检查操作符
    print(f"\n=== 操作符列表 ===")
    for name, op in sorted(snapshots.catalog.operators.items())[:15]:
        print(f"  {name} (arity={op.arity})")

if __name__ == "__main__":
    main()
