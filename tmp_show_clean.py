import csv
from pathlib import Path
queue = Path(".validation_workspace") / "待提交Alpha列表.csv"
with queue.open(encoding="utf-8-sig", newline="") as h:
    for row in csv.DictReader(h):
        if str(row.get("candidate_id") or "").startswith("60cfb0306e64418e"):
            print("candidate_id ", row.get("candidate_id"))
            print("expression   ", row.get("expression"))
            print("datasets     ", row.get("datasets"))
            print("queue_status ", row.get("queue_status"))
