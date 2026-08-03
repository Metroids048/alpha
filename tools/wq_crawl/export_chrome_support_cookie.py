"""Export support.worldquantbrain.com cookies from Chrome Profile 2.

Requires Chrome to release the Cookies DB lock (Chrome must be closed).
Writes ``.wq_browser_cookie_support.json`` without printing secret values.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / ".wq_browser_cookie_support.json"
PROFILE_COOKIES = (
    Path(os.environ["LOCALAPPDATA"])
    / "Google/Chrome/User Data/Profile 2/Network/Cookies"
)


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    import browser_cookie3

    if not PROFILE_COOKIES.exists():
        log(f"missing {PROFILE_COOKIES}")
        return 1

    td = Path(tempfile.mkdtemp(prefix="p2cookies_export_"))
    dst = td / "Cookies"
    last_err: Exception | None = None
    for attempt in range(10):
        try:
            shutil.copy2(PROFILE_COOKIES, dst)
            last_err = None
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"copy attempt {attempt+1}/10 failed: {type(exc).__name__}")
            time.sleep(1)
    if last_err is not None:
        log(f"could not copy Cookies DB: {last_err}")
        return 1

    # Sanity: count WQ-related rows (names only)
    con = sqlite3.connect(str(dst))
    cur = con.cursor()
    cur.execute(
        "SELECT host_key, name FROM cookies "
        "WHERE host_key LIKE '%worldquant%' OR host_key LIKE '%zendesk%' "
        "OR name LIKE '%zendesk%'"
    )
    rows = cur.fetchall()
    con.close()
    log(f"sqlite_rows={len(rows)} names={sorted({n for _, n in rows})}")

    cj = browser_cookie3.chrome(cookie_file=str(dst), domain_name="worldquantbrain.com")
    items = list(cj)
    # Also try zendesk host cookies if domain filter missed shared hosts
    try:
        cj2 = browser_cookie3.chrome(cookie_file=str(dst), domain_name="zendesk.com")
        items.extend(list(cj2))
    except Exception:
        pass

    merged: dict[str, str] = {}
    for c in items:
        if not c.name or c.value is None:
            continue
        merged[c.name] = c.value

    if not merged:
        log("no decryptable cookies found")
        return 1

    # Prefer keeping a realistic Chrome UA; Profile 2 may be newer than 141
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    )
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            if old.get("user_agent"):
                ua = str(old["user_agent"])
        except Exception:
            pass

    hdr = "; ".join(f"{k}={v}" for k, v in merged.items())
    OUT.write_text(
        json.dumps({"cookie": hdr, "user_agent": ua, "source": "chrome:Profile 2"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"wrote {OUT.name} cookie_names={sorted(merged)} len={len(hdr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
