"""Static regression test: Playwright declared in requirements-browser.txt."""

from pathlib import Path


def test_playwright_declared_in_requirements_browser():
    req_file = Path("requirements-browser.txt")
    assert req_file.exists(), "requirements-browser.txt must exist"
    content = req_file.read_text(encoding="utf-8")
    assert "playwright" in content.lower(), (
        "requirements-browser.txt must declare playwright dependency"
    )


def test_browser_login_imports_playwright():
    browser_login = Path("alpha_mining/auth/browser_login.py")
    if not browser_login.exists():
        return  # module may not exist in all configurations
    content = browser_login.read_text(encoding="utf-8", errors="ignore")
    assert "playwright" in content.lower() or "from playwright" in content.lower(), (
        "browser_login.py uses Playwright but requirements-browser.txt must declare it"
    )
