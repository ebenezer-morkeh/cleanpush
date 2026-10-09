"""cleanpush: an AI agent that cleans code and updates docs before it reaches main.

Flow:
  1. Launch the repo-tools MCP server over stdio and discover its tools.
  2. Hand those tools to Claude via the SDK tool runner; Claude formats, fixes lint,
     tidies the changed code and brings the docs in line with the diff.
  3. Safety net in plain code: re-run the test suite; if it fails, revert everything.
  4. Optionally commit the result as a single "chore(cleanpush)" commit.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from cleanpush import repo_tools

MODEL = os.environ.get("CLEANPUSH_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = """\
You are cleanpush, a pre-merge agent. Code on this branch is about to land on the main branch.
Your job is to make sure what lands is clean and that the documentation matches the code.

Work in this order:
1. Call changed_files, then diff, to understand what this branch changes. Only work on those
   files and on documentation; leave the rest of the repository alone.
2. Call run_formatters on the changed source files, then run_linters. Fix any remaining lint
   problems by hand with replace_in_file.
3. Tidy the changed code, but ONLY with these edits, and only on lines this branch added or changed:
   - remove leftover debug output (print, console.log, debugger statements)
   - remove commented-out code
   - remove imports and variables that are completely unused
   - fix obvious typos in comments and docstrings
   - add a docstring to a new public function that has none
   Nothing else. Do not inline or rename variables, reorder code, simplify expressions, restructure
   functions, add type hints or error handling, or touch logic or public signatures, even when the
   result would be an improvement. If you notice something worth changing that is not on the list,
   do NOT edit it; put it under "Suggestions (not applied)" in your summary.
4. Update documentation so it reflects the diff: README usage/examples, docs/ pages, CLI flags,
   config keys, environment variables, and CHANGELOG (add an entry under "Unreleased", creating
   the section if needed). Use search to find docs that mention renamed or removed symbols.
   Do not invent features; document only what the diff shows.
5. Call run_tests once at the end.

Finish with a short plain-text summary: one line per file you changed and why, the test result,
and a "Suggestions (not applied)" list for anything you noticed but left alone. If nothing needed
changing, say so.
"""


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def log_path(repo: Path) -> Path:
    # inside .git so it never shows up as an uncommitted change
    return Path(git("rev-parse", "--absolute-git-dir", cwd=repo).strip()) / "cleanpush-last-run.log"


async def run_agent(repo: Path, base: str) -> str:
    log: list[str] = []
    env = {
        **os.environ,
        "CLEANPUSH_REPO": str(repo),
        # the server needs to find ruff etc. installed alongside this interpreter
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    server = StdioServerParameters(command=sys.executable, args=["-m", "cleanpush.repo_tools"], env=env)
    client = AsyncAnthropic()

    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        print(f"[cleanpush] connected to repo-tools MCP server ({len(tools)} tools)", file=sys.stderr)

        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=16000,
            max_iterations=60,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            # if a safety classifier declines, retry on a fallback model instead of failing
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=[async_mcp_tool(t, session) for t in tools],
            messages=[
                {
                    "role": "user",
                    "content": f"Clean up this branch before it merges into `{base}`, then update the docs.",
                }
            ],
        )

        last = None
        async for message in runner:
            last = message
            for block in message.content:
                if block.type == "tool_use":
                    print(f"[cleanpush] -> {block.name}({_short(block.input)})", file=sys.stderr)
                    log.append(f"-> {block.name} {block.input}")

    if last is None:
        raise RuntimeError("agent produced no response")
    if last.stop_reason == "refusal":
        raise RuntimeError(f"model declined the request: {last.stop_details}")
    summary = "\n".join(b.text for b in last.content if b.type == "text").strip()
    log.append(f"\nSUMMARY\n{summary}")
    log_path(repo).write_text("\n".join(log) + "\n")
    return summary


def _short(value: object, limit: int = 80) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleanpush", description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository to clean (default: current directory)")
    parser.add_argument("--base", default="origin/main", help="branch the code is merging into")
    parser.add_argument("--commit", action="store_true", help="commit the agent's changes")
    parser.add_argument("--check", action="store_true", help="exit 1 if the agent changed anything (CI gate)")
    args = parser.parse_args(argv)

    repo = Path(git("rev-parse", "--show-toplevel", cwd=Path(args.repo)).strip())
    if git("status", "--porcelain", cwd=repo).strip():
        print("[cleanpush] working tree has uncommitted changes; commit or stash them first.", file=sys.stderr)
        return 2

    summary = asyncio.run(run_agent(repo, args.base))
    print(summary)
    print(f"[cleanpush] full tool log: {log_path(repo)}", file=sys.stderr)

    if not git("status", "--porcelain", cwd=repo).strip():
        print("[cleanpush] already clean, nothing to commit.", file=sys.stderr)
        return 0

    # Safety net that doesn't depend on the model: cleanup must never break the build.
    os.environ["CLEANPUSH_REPO"] = str(repo)
    cmd = repo_tools.test_command()
    if cmd is None:
        print("[cleanpush] WARNING: no test command found, so nothing verified the cleanup.", file=sys.stderr)
        print("[cleanpush] Set CLEANPUSH_TEST_CMD to enable the safety net.", file=sys.stderr)
    if cmd and subprocess.run(cmd, cwd=repo, check=False).returncode != 0:
        print("[cleanpush] tests fail after cleanup; reverting the agent's changes.", file=sys.stderr)
        git("checkout", "--", ".", cwd=repo)
        git("clean", "-fd", cwd=repo)
        return 1

    if args.commit:
        git("add", "-A", cwd=repo)
        git("commit", "-m", "chore(cleanpush): format code and update docs", "-m", summary, cwd=repo)
        print("[cleanpush] committed cleanup.", file=sys.stderr)
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
