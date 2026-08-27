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


class ValidationAuthPaused(RuntimeError):
    """The persistent browser profile is not authenticated for validation."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha 队列准备与受保护批次提交")
    parser.add_argument("--database", default="数据/本地运行产物/数据库/research_memory.sqlite")
    parser.add_argument("--input", default="待提交Alpha列表.csv", help="兼容 CSV；启动时幂等导入")
    parser.add_argument("--once", action="store_true", help="只运行一轮准备链")
    parser.add_argument("--interval", type=float, default=30.0, help="空队列或下一轮准备的等待秒数")
    parser.add_argument("--batch-size", type=int, default=5, help="每批最多冻结的候选数（1-5）")
    parser.add_argument("--允许提交", action="store_true", help="执行已确认批次的真实平台写入")
    parser.add_argument("--candidate-id", action="append", default=[], help="指定批次候选 ID；可重复传入")
    parser.add_argument("--确认短语", default="", help="真实提交必须为 I_UNDERSTAND_REAL_SUBMISSION")
    parser.add_argument(
        "--validation-transport", choices=("browser", "direct"), default="browser",
        help="simulate 验证通道；browser 复用持久 Chrome 会话，direct 仅在显式指定时使用",
    )
    parser.add_argument(
        "--browser-profile-dir", default=str(Path(".validation_workspace") / "wq_browser_profile"),
        help="持久 Chrome profile 目录；已登录时不再要求重新扫脸",
    )
    parser.add_argument("--browser-auth-timeout", type=float, default=900.0, help="等待一次合法登录/扫脸的秒数")
    parser.add_argument("--lock-path", default="worldquant_api.lock", help="平台访问全局锁文件")
    return parser


def _build_validation_service(
    database: Path,
    *,
    transport_mode: str,
    browser_profile_dir: Path,
    lock_path: Path,
    auth_timeout: float,
    allow_writes: bool = False,
):
    """Build the simulate/prepare service and, for browser mode, its transport.

    Browser mode fails closed: without a proven session it raises instead of
    silently falling back to another authentication mechanism, so one legitimate
    login covers the whole batch and never turns into extra prompts.
    """

    from alpha_mining.factory.operator_service import CandidateWorkflowService

    if transport_mode != "browser":
        return CandidateWorkflowService(database, max_simulations_per_24h=100), None

    from alpha_mining.platform import browser_transport as browser_transport_module
    from alpha_mining.platform.gateway import PlatformGateway

    transport = browser_transport_module.BrowserBackedWorldQuantTransport(
        profile_dir=browser_profile_dir,
        database=database,
        lock_path=lock_path,
        write_capability=allow_writes,
    )
    try:
        transport.open()
        print(
            "[提交Alpha] 请在专用 Chrome 窗口完成一次 WorldQuant 登录/扫脸；不导出任何凭据。",
            flush=True,
        )
        status = transport.wait_for_authentication(timeout_seconds=auth_timeout)
        if status != 200:
            raise ValidationAuthPaused(f"AUTH_PAUSED: browser identity probe returned HTTP {status}")
        gateway = PlatformGateway(database=database, lock_path=lock_path, transport=transport)
        service = CandidateWorkflowService(database, gateway, max_simulations_per_24h=100)
    except BaseException:
        # This function owns the browser until a service is fully built, so every
        # abort closes it and re-raises unchanged.  BaseException is deliberate:
        # the operator waits here for a login, making Ctrl+C the likeliest exit.
        transport.close()
        raise
    return service, transport


def _run_real_submission(args, database: Path) -> int:
    """Real platform writes reuse the authenticated browser persona and guards."""

    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.storage.work_items import WorkflowStatus

    service, transport = _build_validation_service(
        database,
        transport_mode="browser",
        browser_profile_dir=Path(args.browser_profile_dir),
        lock_path=Path(args.lock_path),
        auth_timeout=args.browser_auth_timeout,
        allow_writes=True,
    )
    try:
        service.store.import_csv(Path(args.input))
        service.store.project_csv(Path(args.input), _ROOT / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv")
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
    finally:
        if transport is not None:
            transport.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    from alpha_mining.storage.work_items import WorkflowStatus, initialize_authoritative_database

    database = Path(args.database)
    if database.resolve() == (_ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite").resolve():
        initialize_authoritative_database(database, _ROOT / "research_memory.sqlite")

    if args.允许提交:
        return _run_real_submission(args, database)

    try:
        service, transport = _build_validation_service(
            database,
            transport_mode=args.validation_transport,
            browser_profile_dir=Path(args.browser_profile_dir),
            lock_path=Path(args.lock_path),
            auth_timeout=args.browser_auth_timeout,
        )
    except ValidationAuthPaused as exc:
        print(json.dumps({"validation_transport": args.validation_transport, "status": "AUTH_PAUSED", "detail": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1

    # Ownership transferred from the builder: every statement that can fail while
    # the browser is open must sit inside this finally, queue import included.
    try:
        service.store.import_csv(Path(args.input))
        service.store.project_csv(Path(args.input), _ROOT / "数据" / "本地运行产物" / "状态" / "generation_queue_events.csv")
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
    finally:
        # One transport for the whole process: closing per cycle or per
        # candidate would reintroduce repeated logins.
        if transport is not None:
            transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
