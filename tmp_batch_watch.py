"""TEMPORARY_VALIDATION_HARNESS / UNTRACKED / NOT_FOR_COMMIT.

Tails the UTF-16LE PowerShell redirect log from tmp_batch_gen.py and emits one
line per meaningful event.  Read-only: never writes to the queue or the DB.
"""

from __future__ import annotations

import sys
import time

PATH = sys.argv[1] if len(sys.argv) > 1 else "tmp_batchA.log"
DEADLINE = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 21600)

KEEP_PREFIX = ("[cycle", "enqueued_total", "rejection_totals")
KEEP_SUBSTR = (
    "target reached",
    "summary ===",
    "FAILED",
    "Traceback",
    "max_cycles reached",
    "BATCH_A",
    "Error",
    "error_class",
)

seen = 0
while time.time() < DEADLINE:
    try:
        raw = open(PATH, "rb").read()
    except OSError as exc:  # log not created yet / transient lock
        print(f"[watch] cannot read log: {exc.__class__.__name__}", flush=True)
        time.sleep(20)
        continue
    try:
        text = raw.decode("utf-16-le")
    except Exception:
        text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    for line in lines[seen:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith(KEEP_PREFIX) or any(tok in s for tok in KEEP_SUBSTR):
            print(s[:400], flush=True)
    seen = len(lines)
    if "rejection_totals" in text or "max_cycles reached" in text:
        print("[watch] batch driver finished", flush=True)
        break
    time.sleep(20)
