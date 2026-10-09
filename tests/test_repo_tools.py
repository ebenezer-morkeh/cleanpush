import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from cleanpush import repo_tools


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("# demo\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "init")
    git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "app.py").write_text("import os\ndef add( a,b ):\n    return a+b\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "feature")
    monkeypatch.setenv("CLEANPUSH_REPO", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def test_rejects_paths_outside_repo(repo):
    with pytest.raises(ValueError):
        repo_tools.read_file("../etc/passwd")
    with pytest.raises(ValueError):
        repo_tools.write_file(".git/config", "x")


def test_changed_files_and_docs(repo):
    assert repo_tools.changed_files("main") == ["app.py"]
    assert repo_tools.list_docs() == ["README.md"]


def test_formatters_clean_python(repo):
    repo_tools.run_formatters(["app.py"])
    assert (repo / "app.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_replace_in_file_requires_unique_match(repo):
    with pytest.raises(ValueError):
        repo_tools.replace_in_file("app.py", "missing", "x")
    repo_tools.replace_in_file("app.py", "return a+b", "return a + b")
    assert "return a + b" in (repo / "app.py").read_text()


def test_mcp_server_over_stdio(repo):
    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "cleanpush.repo_tools"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("changed_files", {"base": "main"})
            return names, result

    names, result = asyncio.run(go())
    assert {"changed_files", "diff", "run_formatters", "list_docs", "run_tests"} <= names
    assert "app.py" in str(result.content)
