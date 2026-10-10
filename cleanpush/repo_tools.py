"""repo-tools: an MCP server that gives an agent safe, scoped access to one git repo.

Every path argument is resolved against the repo root and rejected if it escapes it,
so the agent can only touch files inside the repository it was launched on.

Run standalone (any MCP client can use it, e.g. Claude Code or Claude Desktop):
    CLEANPUSH_REPO=/path/to/repo cleanpush-mcp
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

MAX_OUTPUT = 40_000  # chars returned per tool call; larger outputs say they were cut

PY_EXT = {".py", ".pyi"}
PRETTIER_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".css", ".scss", ".html", ".md", ".yml", ".yaml"}
GO_EXT = {".go"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
DOC_GLOBS = ["README*", "CHANGELOG*", "CONTRIBUTING*", "ARCHITECTURE*", "docs/**/*.md", "docs/**/*.mdx", "*.md"]


def repo_root() -> Path:
    return Path(os.environ.get("CLEANPUSH_REPO", os.getcwd())).resolve()


def _safe_path(path: str) -> Path:
    root = repo_root()
    p = (root / path).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"path escapes the repository: {path}")
    if ".git" in p.relative_to(root).parts:
        raise ValueError("refusing to touch the .git directory")
    return p


def _run(cmd: list[str], timeout: int = 300) -> str:
    try:
        proc = subprocess.run(cmd, cwd=repo_root(), capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return f"$ {shlex.join(cmd)}\n(command not found)"
    except subprocess.TimeoutExpired:
        return f"$ {shlex.join(cmd)}\n(timed out after {timeout}s)"
    out = (proc.stdout + proc.stderr).strip()
    return f"$ {shlex.join(cmd)}  [exit {proc.returncode}]\n{_clip(out)}"


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n... [output cut: {len(text) - MAX_OUTPUT} more chars; narrow the request]"


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


mcp = MCPServer(
    "repo-tools",
    instructions="Tools for inspecting, formatting, linting and documenting files in one git repository.",
)


@mcp.tool()
def changed_files(base: str = "origin/main") -> list[str]:
    """List files added/modified on this branch relative to `base` (committed and uncommitted)."""
    root = repo_root()
    names: set[str] = set()
    for args in (
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"],
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
    ):
        proc = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            names.update(n for n in proc.stdout.splitlines() if n)
    return sorted(names)


@mcp.tool()
def diff(base: str = "origin/main", path: str | None = None) -> str:
    """Show the unified diff of this branch against `base`, optionally for a single path."""
    cmd = ["git", "diff", f"{base}...HEAD"]
    if path:
        _safe_path(path)
        cmd += ["--", path]
    return _run(cmd)


@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file from the repository."""
    return _clip(_safe_path(path).read_text())


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file in the repository with `content`."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {path}"


@mcp.tool()
def replace_in_file(path: str, old: str, new: str) -> str:
    """Replace exactly one occurrence of `old` with `new` in a file. Fails if `old` is missing or ambiguous."""
    p = _safe_path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one match for `old` in {path}, found {count}")
    p.write_text(text.replace(old, new, 1))
    return f"edited {path}"


@mcp.tool()
def search(pattern: str) -> str:
    """Search tracked files for a regex (git grep). Use it to find docs that mention a changed symbol."""
    return _run(["git", "grep", "-n", "-I", "-E", pattern])


@mcp.tool()
def list_docs() -> list[str]:
    """List documentation files in the repository (README, CHANGELOG, docs/ ...)."""
    root = repo_root()
    found = {str(p.relative_to(root)) for g in DOC_GLOBS for p in root.glob(g) if p.is_file()}
    return sorted(f for f in found if not f.startswith((".git/", "node_modules/", ".venv/")))


@mcp.tool()
def run_formatters(paths: list[str]) -> str:
    """Run the deterministic formatters/auto-fixers that match each file type, in place.

    Python: ruff check --fix + ruff format. JS/TS/JSON/CSS/MD/YAML: prettier (if installed in the repo).
    Go: gofmt. Always run this before making manual cleanups.
    """
    files = [str(_safe_path(p).relative_to(repo_root())) for p in paths]
    py = [f for f in files if Path(f).suffix in PY_EXT]
    web = [f for f in files if Path(f).suffix in PRETTIER_EXT]
    go = [f for f in files if Path(f).suffix in GO_EXT]
    reports = []
    if py:
        # fix first: removing unused imports can leave blank lines that format then tidies
        reports.append(_run(["ruff", "check", "--fix", *py]))
        reports.append(_run(["ruff", "format", *py]))
    bin_dir = repo_root() / "node_modules" / ".bin"
    js = [f for f in files if Path(f).suffix in JS_EXT]
    if js and (bin_dir / "eslint").exists():
        # auto-fix what ESLint can fix; remaining problems are reported by run_linters
        reports.append(_run(["npx", "--no-install", "eslint", "--fix", *js]))
    if web and (bin_dir / "prettier").exists():
        reports.append(_run(["npx", "--no-install", "prettier", "--write", *web]))
    elif web:
        reports.append("prettier is not installed in this repo; skipped " + ", ".join(web))
    if go and _has("gofmt"):
        reports.append(_run(["gofmt", "-w", *go]))
    return "\n\n".join(reports) or "no formatter applies to these files"


@mcp.tool()
def run_linters(paths: list[str]) -> str:
    """Report remaining lint problems (no changes made) for the given files."""
    files = [str(_safe_path(p).relative_to(repo_root())) for p in paths]
    py = [f for f in files if Path(f).suffix in PY_EXT]
    js = [f for f in files if Path(f).suffix in JS_EXT]
    reports = []
    if py:
        reports.append(_run(["ruff", "check", *py]))
    if js and (repo_root() / "node_modules" / ".bin" / "eslint").exists():
        reports.append(_run(["npx", "--no-install", "eslint", *js]))
    return "\n\n".join(reports) or "no linter applies to these files"


@mcp.tool()
def run_tests() -> str:
    """Run the project's test suite (CLEANPUSH_TEST_CMD, else pytest / npm test if detected)."""
    cmd = test_command()
    if cmd is None:
        return "no test command detected (set CLEANPUSH_TEST_CMD); tests were NOT run"
    return _run(cmd, timeout=900)


def _python() -> str:
    """The repo's own virtualenv if it has one, else whatever python3/python is on PATH."""
    venv = repo_root() / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return shutil.which("python3") or shutil.which("python") or sys.executable


def test_command() -> list[str] | None:
    """Work out how to run this repo's tests, or None if we can't tell."""
    if cmd := os.environ.get("CLEANPUSH_TEST_CMD"):
        return shlex.split(cmd)
    root = repo_root()
    if (root / "package.json").exists() and '"test"' in (root / "package.json").read_text():
        return ["npm", "test", "--silent"]
    if (root / "go.mod").exists():
        return ["go", "test", "./..."]
    has_py_tests = (root / "tests").is_dir() or any(root.glob("test_*.py")) or any(root.glob("*_test.py"))
    if has_py_tests:
        return [_python(), "-m", "pytest", "-q"]
    return None


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
