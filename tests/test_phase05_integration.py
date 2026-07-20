from __future__ import annotations

import ast
import inspect
from pathlib import Path

import run_pipeline_supervisor as supervisor


ROOT = Path(__file__).resolve().parents[1]


def test_main_pipeline_has_no_internal_self_authenticate_calls() -> None:
    source = (ROOT / "auto_alpha_pipeline_rebuilt_v50.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls: list[bool] = []
    compatibility_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(
                child.func, ast.Attribute
            ):
                continue
            if (
                child.func.attr == "authenticate"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
            ):
                raise AssertionError(
                    f"internal self.authenticate() remains in {node.name}"
                )
            if child.func.attr != "ensure_authenticated":
                continue
            force = next((kw.value for kw in child.keywords if kw.arg == "force"), None)
            if isinstance(force, ast.Constant) and isinstance(force.value, bool):
                if node.name == "authenticate":
                    compatibility_calls += 1
                else:
                    calls.append(force.value)
    assert calls.count(True) == 3
    assert calls.count(False) == 5
    assert compatibility_calls == 1


def test_supervisor_prepares_shared_auth_state_environment(tmp_path: Path) -> None:
    state_path = tmp_path / "shared-auth.json"
    env = supervisor._child_environment({"BASE": "kept"}, state_path)
    assert env["BASE"] == "kept"
    assert Path(env["WQ_AUTH_STATE_FILE"]) == state_path.resolve()


def test_async_batch_change_is_limited_to_authentication_function() -> None:
    source = (ROOT / "alpha_mining" / "simulate" / "async_batch.py").read_text(
        encoding="utf-8"
    )
    assert "ensure_authenticated_async" in source
    assert "async def _authenticate" in source
    assert "def _sim_payload(payload: dict)" in source


def test_supervisor_public_restart_defaults_remain_unchanged() -> None:
    source = inspect.getsource(supervisor.parse_args)
    assert "default=200" in source
    assert "default=90" in source
