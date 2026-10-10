"""Offline checks that JS/TS files are routed to eslint/prettier (the binaries are stubbed)."""

import stat

import pytest

from cleanpush import repo_tools


@pytest.fixture
def js_repo(tmp_path, monkeypatch):
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    # npx is replaced by a stub that records what it was asked to run
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    npx = stub_dir / "npx"
    npx.write_text('#!/bin/sh\necho "ran: $@"\n')
    npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
    for tool in ("eslint", "prettier"):
        (bin_dir / tool).write_text("")
    (tmp_path / "app.ts").write_text("let x=1\n")
    (tmp_path / "package.json").write_text('{"scripts": {"test": "echo ok"}}')
    monkeypatch.setenv("CLEANPUSH_REPO", str(tmp_path))
    monkeypatch.setenv("PATH", f"{stub_dir}:/usr/bin:/bin")
    return tmp_path


def test_ts_file_gets_eslint_fix_then_prettier(js_repo):
    out = repo_tools.run_formatters(["app.ts"])
    assert out.index("eslint --fix app.ts") < out.index("prettier --write app.ts")


def test_ts_file_is_linted_with_eslint(js_repo):
    assert "eslint app.ts" in repo_tools.run_linters(["app.ts"])


def test_npm_test_detected(js_repo):
    assert repo_tools.test_command() == ["npm", "test", "--silent"]


def test_prettier_missing_is_reported_not_crashed(js_repo):
    (js_repo / "node_modules" / ".bin" / "prettier").unlink()
    (js_repo / "node_modules" / ".bin" / "eslint").unlink()
    out = repo_tools.run_formatters(["app.ts"])
    assert "prettier is not installed" in out
