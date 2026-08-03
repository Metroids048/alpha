#!/usr/bin/env python3
"""P0 security scanner: detect sensitive authentication material in git history.

Outputs only file paths, commit IDs, and hit types — never file contents.
Exits 1 if any sensitive paths are found; exits 2 if no git repo found.

Usage:
    python tools/security/verify_git_history.py
    python tools/security/verify_git_history.py --write-path-list sensitive-paths.txt
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "wq_browser_profile",
        re.compile(r"(^|/)\.wq_browser_profile(/|$)", re.IGNORECASE),
    ),
    (
        "wq_browser_cookie_json",
        re.compile(r"(^|/)\.wq_browser_cookie[^/]*\.json$", re.IGNORECASE),
    ),
    (
        "wq_persona_session_cookies",
        re.compile(r"(^|/)\.wq_persona_session_cookies\.json$", re.IGNORECASE),
    ),
    (
        "wq_auth_state",
        re.compile(r"(^|/)\.wq_auth_state\.json$", re.IGNORECASE),
    ),
]


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def scan(*, write_path_list: str | None = None) -> int:
    """Scan all reachable git objects for sensitive paths.

    Returns the number of hits found.
    Exits with 2 if this is not a git repository.
    """
    check = _run(["git", "rev-parse", "--git-dir"])
    if check.returncode != 0:
        print("NOT_VERIFIED: no .git directory found", file=sys.stderr)
        sys.exit(2)

    proc = _run(["git", "log", "--all", "--name-only", "--format=COMMIT:%H"])
    if proc.returncode != 0:
        print(f"ERROR: git log failed: {proc.stderr[:200]}", file=sys.stderr)
        sys.exit(2)

    current_commit: str = ""
    hits: list[tuple[str, str, str]] = []  # (commit, path, rule_name)
    seen_paths: set[str] = set()

    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if line.startswith("COMMIT:"):
            current_commit = line[7:]
            continue
        if not line:
            continue
        for rule_name, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(line):
                hits.append((current_commit, line, rule_name))
                seen_paths.add(line)
                break

    if hits:
        print(f"FOUND {len(hits)} sensitive path hit(s) in git history:")
        for commit, path, rule in hits[:200]:
            print(f"  commit={commit[:16]}  rule={rule}  path={path}")
        if len(hits) > 200:
            print(f"  ... and {len(hits) - 200} more")

        if write_path_list:
            out = Path(write_path_list)
            out.write_text("\n".join(sorted(seen_paths)) + "\n", encoding="utf-8")
            print(f"Sensitive path list written to: {out}")

        return len(hits)
    else:
        print("OK: no sensitive authentication paths found in git history")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-path-list",
        metavar="FILE",
        help="Write unique sensitive paths to FILE (for git filter-repo --paths-from-file)",
    )
    args = parser.parse_args()
    hit_count = scan(write_path_list=args.write_path_list)
    sys.exit(1 if hit_count > 0 else 0)


if __name__ == "__main__":
    main()
