# cleanpush

An AI agent that runs before code lands on `main`. It **formats and tidies the code** and
**updates the documentation**, so `main` always has clean code and up-to-date docs.

Built with **Claude tool calling** and an **MCP server**.

```
git push / PR to main
        │
        ▼
 ┌──────────────┐  MCP (stdio)  ┌───────────────────────────────┐
 │ cleanpush    │ ────────────▶ │ repo-tools MCP server         │
 │ agent        │               │  changed_files  diff          │
 │ (Claude +    │ ◀──────────── │  read_file  write_file        │
 │ tool runner) │  tool results │  replace_in_file  search      │
 └──────┬───────┘               │  run_formatters  run_linters  │
        │                       │  list_docs  run_tests         │
        ▼                       └───────────────────────────────┘
 tests still pass? ── no ──▶ revert everything, block the push
        │ yes
        ▼
 commit "chore(cleanpush): format code and update docs"
```

## What the agent does

1. Reads the branch diff against `origin/main`.
2. Runs the formatters (ruff for Python, prettier for JS/TS/MD/JSON/YAML, gofmt for Go), then the linters, and fixes whatever lint problems remain.
3. Makes small, safe cleanups in the changed files: removes leftover debug prints, commented-out code and unused imports, and adds docstrings to new public functions. It never changes behavior.
4. Updates the docs to match the diff: README usage, `docs/`, CLI flags, environment variables, and a `CHANGELOG` entry under "Unreleased".
5. Runs the tests. **This check is done in plain code, not by the model:** if the tests fail after cleanup, every change is reverted.

## Install

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
```

## Use it

```bash
cleanpush                      # clean the current branch against origin/main
cleanpush --commit             # ...and commit the result
cleanpush --check              # CI gate: exit 1 if anything needed cleaning
cleanpush --base origin/develop
```

The working tree must be clean before you run it, so the agent's changes never mix with yours.

| Env var | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `CLEANPUSH_MODEL` | model to use (default `claude-sonnet-5-5`) |
| `CLEANPUSH_TEST_CMD` | test command (default: auto-detects `pytest` or `npm test`) |
| `CLEANPUSH_SKIP=1` | skip the pre-push hook once |

## Enforce it on main

The main enforcement point is the **pull request**: the agent runs once the code is ready for review, so the docs match the final diff.

**Locally, two optional hooks.**
- `hooks/pre-commit` formats staged Python files with ruff on every commit. It is instant and free, with no AI.
- `hooks/pre-push` runs the full agent when you push to `main`. If it makes a cleanup commit, the push stops so you can review it and push again.

```bash
git config core.hooksPath /path/to/cleanpush/hooks
```

**In CI (recommended, runs on pull requests).** [.github/workflows/cleanpush.yml](.github/workflows/cleanpush.yml) runs on every PR into `main` and pushes the cleanup commit back to the PR branch. Add `ANTHROPIC_API_KEY` as a repo secret, then turn on branch protection for `main` (require pull requests). After that, nothing reaches `main` without passing through cleanpush.

### If your repo has required checks (recommended token setup)

Pushes made with GitHub's built-in token do not start other workflows normally, so your required
checks can sit "waiting for approval" on the cleanup commit and block the merge. To avoid that,
let the workflow push with your own token:

1. Create a **fine-grained personal access token** (GitHub → Settings → Developer settings →
   Personal access tokens). Limit it to the one repository, with **Contents: Read and write**.
2. Add it as a repository secret named `CLEANPUSH_TOKEN`.

The workflow uses it automatically when it exists and falls back to the built-in token when it
doesn't. It also stops itself on its own commit (it checks the newest commit's title), so it
never loops.

## Use the MCP server on its own

`repo-tools` is a standard MCP server, so other clients can use it too, e.g. Claude Code:

```bash
claude mcp add repo-tools -e CLEANPUSH_REPO=$PWD -- cleanpush-mcp
```

Every path is confined to the repo root, and `.git/` can't be written.

## Develop

```bash
pip install -e ".[dev]"
pytest
```
