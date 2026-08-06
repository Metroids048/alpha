"""清空FAR_FAIL和REJECTED候选，为降级候选腾出inventory空间"""
import csv
from pathlib import Path

csv_path = Path("待提交Alpha列表.csv")

with open(csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    fieldnames = reader.fieldnames

before = len(rows)
keep = [r for r in rows if r['queue_status'] in ('PENDING_SIMULATION', 'PENDING')]
after = len(keep)

with open(csv_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(keep)

print(f"清理前: {before}行  清理后: {after}行  移除: {before - after}行")
print(f"保留PENDING候选: {[r['expression'][:60] for r in keep]}")
