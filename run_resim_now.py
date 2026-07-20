import asyncio, csv, json, math, ssl, time
from datetime import datetime, timezone
from urllib.parse import urljoin
import aiohttp

BASE = "https://api.worldquantbrain.com"
SIM_URL = f"{BASE}/simulations"
QUEUE_CSV = "alpha_resim_queue.csv"
LOG_FILE = "resim_run.log"
JWT_EXP = 1784349647
MAX_CONCURRENT = 3

COOKIE = json.loads(open(".wq_browser_cookie.json").read())["cookie"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, */*",
    "Content-Type": "application/json",
    "Origin": "https://platform.worldquantbrain.com",
    "Referer": "https://platform.worldquantbrain.com/",
    "Cookie": COOKIE,
}

def utc(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def jwt_left(): return max(0, JWT_EXP - int(time.time()))

def log(msg):
    line = f"[{utc()}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def clean(s):
    try: s = json.loads(s or "{}")
    except: s = {}
    for k,d in [("decay",4),("truncation",0.05),("delay",1)]:
        if k in s:
            try:
                v = float(s[k])
                if math.isnan(v) or math.isinf(v): s[k] = d
            except: s[k] = d
    return s

def load_q():
    with open(QUEUE_CSV, newline="", encoding="utf-8-sig") as f:
        return [dict(r) for r in csv.DictReader(f)]

def save_q(rows):
    flds = ["alpha_id","expression","settings_json","region","universe","delay",
            "neutralization","decay","truncation","sharpe","fitness","turnover",
            "returns","drawdown","margin","date_created","new_alpha_id","resim_status"]
    with open(QUEUE_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=flds, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def get_id(body):
    if not isinstance(body, dict): return None
    for k in ("id","alphaId","alpha_id"):
        v = body.get(k)
        if isinstance(v, str) and len(v) > 4: return v.strip()
    return None

async def post_sim(sess, payload):
    for attempt in range(12):
        try:
            async with sess.post(SIM_URL, json=payload, timeout=aiohttp.ClientTimeout(total=90)) as r:
                text = await r.text()
                hdrs = dict(r.headers)
                try: body = json.loads(text)
                except: body = {}
                if r.status in (200,201,202):
                    loc = hdrs.get("Location","").strip()
                    if not loc and isinstance(body,dict):
                        for k in ("location","url","href"):
                            v = body.get(k,"")
                            if isinstance(v,str) and v.strip(): loc=v.strip(); break
                    if loc:
                        url = loc if loc.startswith("http") else urljoin(f"{BASE}/",loc.lstrip("/"))
                        return url, "ok"
                    aid = get_id(body)
                    if aid: return f"{BASE}/alphas/{aid}", "ok"
                    return None, "no_loc"
                if r.status == 400: return None, f"bad:{text[:80]}"
                if "CONCURRENT" in text:
                    wait = 15 + attempt * 10
                    await asyncio.sleep(wait)
                    continue
                if r.status == 429:
                    wait = float(hdrs.get("Retry-After", 30))
                    log(f"  429 sleep={wait:.0f}s")
                    await asyncio.sleep(wait)
                    continue
                return None, f"http_{r.status}"
        except Exception as e:
            if attempt >= 11: return None, f"ex:{type(e).__name__}"
            await asyncio.sleep(5)
    return None, "timeout"

async def poll_sim(sess, url, max_wait=480.0):
    dl = asyncio.get_event_loop().time() + max_wait
    s = 3.0
    while asyncio.get_event_loop().time() < dl:
        try:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status == 200:
                    b = await r.json()
                    st = str(b.get("status") or b.get("state") or "").lower()
                    if st in ("failed","error","rejected"): return None, f"plat_{st}"
                    aid = get_id(b)
                    if aid: return aid, "ok"
        except Exception: pass
        await asyncio.sleep(s)
        s = min(s*1.1, 8.0)
    return None, "timeout"

async def run_one(sess, rec, queue, idx_map, stats, lock, ctr):
    aid = rec["alpha_id"]
    expr = (rec.get("expression") or "").strip()
    if not expr:
        async with lock:
            i = idx_map.get(aid)
            if i is not None: queue[i]["resim_status"] = "FAIL:no_expr"
            stats["fail"] += 1; ctr[0] += 1
        return
    setts = clean(rec.get("settings_json","{}"))
    try: json.dumps(setts, allow_nan=False)
    except:
        setts = {"instrumentType":"EQUITY","region":rec.get("region","USA"),
                 "universe":rec.get("universe","TOP3000"),"delay":1,"decay":4,
                 "neutralization":rec.get("neutralization","MARKET"),"truncation":0.05,
                 "pasteurization":"ON","unitHandling":"VERIFY","nanHandling":"ON",
                 "language":"FASTEXPR","visualization":False}
    payload = {"type":"REGULAR","regular":expr,"settings":setts}

    url, st = await post_sim(sess, payload)
    if not url:
        async with lock:
            i = idx_map.get(aid)
            if i is not None: queue[i]["resim_status"] = f"FAIL:{st}"
            stats["fail"] += 1; ctr[0] += 1
        if stats["fail"] <= 5 or stats["fail"] % 50 == 0:
            log(f"  FAIL {aid} {st}")
        return

    new_aid, ps = await poll_sim(sess, url)
    async with lock:
        i = idx_map.get(aid)
        if i is not None:
            if new_aid:
                queue[i]["new_alpha_id"] = new_aid
                queue[i]["resim_status"] = "DONE"
                stats["ok"] += 1
            else:
                queue[i]["resim_status"] = f"FAIL:{ps}"
                stats["fail"] += 1
        ctr[0] += 1

    total = ctr[1]
    completed = ctr[0]
    if completed % 5 == 0 or completed == total or completed == 1:
        log(f"Progress {completed}/{total} ok={stats['ok']} fail={stats['fail']} jwt={jwt_left()}s")
        async with lock:
            save_q(queue)

async def main():
    log("BLOCKED legacy resimulation writer; use python -m alpha_mining legacy triage")
    return
    queue = load_q()
    pending = [r for r in queue if r.get("resim_status","PENDING") == "PENDING"]
    total = len(pending)
    log(f"START pending={total} jwt_left={jwt_left()}s window={MAX_CONCURRENT}")

    ssl_ctx = ssl.create_default_context()
    conn = aiohttp.TCPConnector(ssl=ssl_ctx, limit=20, enable_cleanup_closed=True)
    stats = {"ok":0,"fail":0}
    ctr = [0, total]  # [completed, total]
    idx_map = {r["alpha_id"]:i for i,r in enumerate(queue)}
    lock = asyncio.Lock()

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def bounded(rec):
        async with sem:
            if jwt_left() < 30:
                return
            await run_one(sess, rec, queue, idx_map, stats, lock, ctr)

    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as sess:
        tasks = [asyncio.create_task(bounded(r)) for r in pending]
        await asyncio.gather(*tasks, return_exceptions=True)

    save_q(queue)
    log(f"DONE ok={stats['ok']} fail={stats['fail']} total={total} jwt_left={jwt_left()}s")

if __name__ == "__main__":
    asyncio.run(main())
