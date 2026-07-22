#!/usr/bin/env python3
"""Single pipeline cycle entry for the fail-closed vNext Consultant Factory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _strip_resilient_flag(argv: list[str]) -> tuple[list[str], bool]:
    resilient = False
    out: list[str] = []
    for arg in argv:
        if arg == "--resilient-async":
            resilient = True
            continue
        out.append(arg)
    env_on = os.environ.get("ALPHA_RESILIENT_ASYNC", "").strip().lower() in ("1", "true", "yes")
    return out, resilient or env_on


def main() -> int:
    argv, use_resilient = _strip_resilient_flag(sys.argv[1:])
    sys.argv = [sys.argv[0], *argv]
    from alpha_mining.factory.runtime import main as factory_main

    if use_resilient:
        argv.append("--resilient-async")
    return int(factory_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
