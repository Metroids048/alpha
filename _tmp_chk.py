import csv, json, requests
from collections import Counter
from pathlib import Path
c=Counter()
with open("alpha_resim_queue.csv", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        c[row.get("resim_status") or "EMPTY"] += 1
print("queue", dict(c))
for name in (".wq_browser_cookie.json", ".wq_browser_cookie.next.json"):
    p=Path(name)
    print(name, "exists", p.is_file())
    if p.is_file():
        cookie=json.loads(p.read_text(encoding="utf-8")).get("cookie") or ""
        s=requests.Session(); s.headers["Cookie"]=cookie
        r=s.get("https://api.worldquantbrain.com/users/self", timeout=30)
        print(name, "status", r.status_code)
