#!/usr/bin/env python3
"""Candidate workflow operator entry point.

Default mode only prepares the local queue (simulate/check/description draft)
and never writes to WorldQuant.  A real batch has two explicit stages: first
freeze the collection, then rerun with ``--允许提交`` and the confirmation
phrase after FactoryControl enables both platform write permissions.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env file for WQ credentials
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    # Fallback: manual .env parsing
    env_file = _ROOT / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alpha 队列准备与受保护批次提交")
    parser.add_argument("--database", default="数据/本地运行产物/数据库/research_memory.sqlite")
    parser.add_argument("--input", default="待提交Alpha列表.csv", help="兼容 CSV；启动时幂等导入")
    parser.add_argument("--once", action="store_true", help="只运行一轮准备链")
    parser.add_argument("--interval", type=float, default=30.0, help="空队列或下一轮准备的等待秒数")
    parser.add_argument("--batch-size", type=int, default=5, help="每批最多冻结的候选数（1-5）")
    parser.add_argument("--允许提交", action="store_true", help="执行已确认批次的真实平台写入")
    parser.add_argument("--candidate-id", action="append", default=[], help="指定批次候选 ID；可重复传入")
    parser.add_argument("--确认短语", default="", help="真实提交必须为 I_UNDERSTAND_REAL_SUBMISSION")
    args = parser.parse_args(argv)

    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus, initialize_authoritative_database

    database = Path(args.database)
    if database.resolve() == (_ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite").resolve():
        initialize_authoritative_database(database, _ROOT / "research_memory.sqlite")
    service = CandidateWorkflowService(database, max_simulations_per_24h=100)
    service.store.import_csv(Path(args.input))
    service.store.project_csv(Path(args.input), _ROOT / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv")

    if args.允许提交:
        candidate_ids = args.candidate_id or [
            item.candidate_id for item in service.list_items(
                states=[WorkflowStatus.AWAITING_BATCH_CONFIRMATION.value], limit=max(1, min(5, args.batch_size))
            )
        ]
        if not candidate_ids:
            print("[提交Alpha] 没有已冻结且待确认的批次")
            return 0
        batch = service.submit_batch(candidate_ids, confirmation=args.确认短语, execute=True)
        print(json.dumps(batch.__dict__, ensure_ascii=False, sort_keys=True))
        service.store.project_csv(Path(args.input))
        return 0

    try:
        while True:
            summary = service.prepare_once(limit=12)
            print(json.dumps(summary.__dict__, ensure_ascii=False, sort_keys=True))
            ready = service.list_items(states=[WorkflowStatus.DESCRIPTION_VALIDATED.value, WorkflowStatus.READY_TO_SUBMIT.value], limit=max(1, min(5, args.batch_size)))
            if ready:
                batch = service.submit_batch([item.candidate_id for item in ready], execute=False)
                print(json.dumps({"batch_prepared": batch.__dict__}, ensure_ascii=False, sort_keys=True))
            service.store.project_csv(Path(args.input))
            if args.once:
                return 0
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("[提交Alpha] 已停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
