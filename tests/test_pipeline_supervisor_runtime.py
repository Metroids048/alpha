from __future__ import annotations

import run_pipeline_supervisor as supervisor


def test_wmic_csv_parser_keeps_parent_relationship() -> None:
    output = (
        "Node,CommandLine,ParentProcessId,ProcessId\r\n"
        'SSSS,"C:\\alpha\\.venv\\Scripts\\python.exe C:\\alpha\\run_pipeline_loop.py",999,100\r\n'
        'SSSS,"C:\\Python\\python.exe C:\\alpha\\run_pipeline_cycle.py",100,101\r\n'
    )

    processes = supervisor._parse_pipeline_processes(output)

    assert [(item.pid, item.parent_pid) for item in processes] == [(100, 999), (101, 100)]


def test_orphaned_pipeline_root_is_selected_for_takeover() -> None:
    processes = [
        supervisor.PipelineProcess(100, 999, "python run_pipeline_loop.py"),
        supervisor.PipelineProcess(101, 100, "python run_pipeline_cycle.py"),
    ]

    roots = supervisor._orphaned_pipeline_roots(
        processes,
        pid_exists=lambda pid: pid != 999,
    )

    assert [process.pid for process in roots] == [100]


def test_owned_pipeline_is_not_selected_for_takeover() -> None:
    processes = [
        supervisor.PipelineProcess(100, 50, "python run_pipeline_loop.py"),
        supervisor.PipelineProcess(101, 100, "python run_pipeline_cycle.py"),
    ]

    roots = supervisor._orphaned_pipeline_roots(
        processes,
        pid_exists=lambda _pid: True,
    )

    assert roots == []
