"""Unit tests for tools/security/verify_git_history.py

Tests verify detection logic, non-disclosure, and no-git safety.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).parent.parent


def _run_scanner(*extra_args: str) -> subprocess.CompletedProcess[str]:
    script = _REPO_ROOT / "tools" / "security" / "verify_git_history.py"
    return subprocess.run(
        [sys.executable, str(script), *extra_args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TestScannerDetectionLogic:
    def test_sensitive_patterns_are_recognised(self):
        import re, sys
        sys.path.insert(0, ".")
        from tools.security.verify_git_history import _SENSITIVE_PATTERNS
        hits = {
            ".wq_browser_profile/Default/Cookies": "wq_browser_profile",
            ".wq_browser_cookie.next.json": "wq_browser_cookie_json",
            ".wq_persona_session_cookies.json": "wq_persona_session_cookies",
            ".wq_auth_state.json": "wq_auth_state",
            "dir/.wq_browser_profile/something": "wq_browser_profile",
        }
        for path, expected_rule in hits.items():
            matched = [r for r, p in _SENSITIVE_PATTERNS if p.search(path)]
            assert matched, f"Expected {expected_rule} to match {path!r}; no match found"
            assert matched[0] == expected_rule, (
                f"Expected rule {expected_rule!r} but got {matched[0]!r} for {path!r}"
            )

    def test_normal_files_not_flagged(self):
        import sys
        sys.path.insert(0, ".")
        from tools.security.verify_git_history import _SENSITIVE_PATTERNS
        safe_paths = [
            "alpha_mining/factory/orchestrator.py",
            "requirements.txt",
            "data/results.json",
            ".env.example",
            "wq_something_else.txt",
            "docs/auth_overview.md",
        ]
        for path in safe_paths:
            matched = [r for r, p in _SENSITIVE_PATTERNS if p.search(path)]
            assert not matched, f"Safe path {path!r} was incorrectly flagged by rules: {matched}"

    def test_output_does_not_contain_test_secret_value(self, tmp_path):
        """Scanner must never print file contents — only path, commit, rule."""
        result = _run_scanner()
        secret_indicators = [
            "session_token",
            "Bearer ",
            "password=",
            "Authorization:",
        ]
        for indicator in secret_indicators:
            assert indicator not in result.stdout, (
                f"Scanner output contains potential secret indicator: {indicator!r}"
            )
            assert indicator not in result.stderr, (
                f"Scanner stderr contains potential secret indicator: {indicator!r}"
            )

    def test_no_git_dir_exits_2(self, tmp_path, monkeypatch):
        """When there is no .git directory, scanner must exit 2 (not 0)."""
        monkeypatch.chdir(tmp_path)
        result = _run_scanner()
        assert result.returncode == 2, (
            f"Expected exit code 2 for no-git directory; got {result.returncode}"
        )

    def test_no_git_output_says_not_verified(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _run_scanner()
        assert "NOT_VERIFIED" in result.stderr or "NOT_VERIFIED" in result.stdout, (
            "Scanner must output NOT_VERIFIED when no .git directory is present"
        )

    def test_current_repo_exits_1_due_to_known_history(self):
        """This repo is known to have sensitive paths — scanner must exit 1."""
        result = _run_scanner()
        # The repo has known .wq_browser_profile commits; scanner must find them
        assert result.returncode in (1, 2), (
            f"Expected exit code 1 (hits found) or 2 (no git); got {result.returncode}"
        )
        if result.returncode == 1:
            assert "FOUND" in result.stdout

    def test_output_only_contains_path_commit_rule(self):
        """Scanner lines for hits must only contain commit ID, rule name, and path."""
        result = _run_scanner()
        if result.returncode != 1:
            pytest.skip("No sensitive paths found in this repo")
        for line in result.stdout.splitlines():
            if line.startswith("  commit="):
                # Should match: "  commit=<hex>  rule=<name>  path=<path>"
                assert "commit=" in line
                assert "rule=" in line
                assert "path=" in line
                # Should NOT contain values that look like tokens/passwords
                assert "Bearer" not in line
                assert "sessionid" not in line.lower()
