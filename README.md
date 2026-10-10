# cleanpush

An AI agent that cleans up a branch **before it lands on `main`**: it formats the code, removes
leftovers, and updates the docs so they match the change. Built with Claude tool calling and an
MCP server.

> Open a pull request. About a minute later a `chore(cleanpush)` commit appears on it: code
> formatted, debug prints and unused imports gone, README and CHANGELOG updated, tests re-run.

## What it does

For the files a branch changed, the agent will:

- run the project's formatters and linters (ruff for Python; ESLint and prettier for JS/TS)
- remove leftover debug output, commented-out code, and unused imports and variables
- add a docstring to new public functions that lack one
- update the README and add a CHANGELOG entry that reflect the diff
- run your tests, and **revert everything if they fail**

It is deliberately conservative. Edits are limited to that list. Anything else it notices (an
inlinable variable, a missing test, a likely bug) goes into a **"Suggestions (not applied)"**
section of the commit message instead of being changed.

## How it works

```
 pull request / manual run / git hook
                │
                ▼
        ┌──────────────┐   MCP over stdio   ┌───────────────────────────────┐
        │ agent.py     │ ─────────────────▶ │ repo_tools.py (MCP server)    │
        │ Claude +     │                    │  changed_files  diff          │
        │ tool runner  │ ◀───────────────── │  read_file  write_file        │
        └──────┬───────┘   tool results     │  replace_in_file  search      │
               │                            │  run_formatters  run_linters  │
               ▼                            │  list_docs  run_tests         │
   tests pass again (plain code)?           └───────────────────────────────┘
        │ no → revert, exit 1
        ▼ yes
   commit only the files the agent touched
```

- **`cleanpush/repo_tools.py`** is an MCP server exposing ten tools. Every path is confined to
  the repository, and `.git/` can't be written.
- **`cleanpush/agent.py`** starts that server, hands its tools to Claude through the Anthropic
  SDK tool runner, streams a readable log, and finishes with a safety net.

### Design decisions

- **Tests are re-run by plain code, not by the model.** "Did the tests pass?" must not depend on
  the AI reporting honestly. If they fail, the working tree is reverted.
- **A closed list of allowed edits.** A first version allowed "clearly safe" tidying, and the
  agent inlined a variable on its own. Replacing that with an explicit list plus a suggestions
  channel fixed it.
- **Only the agent's files are committed.** Staging uses the tool log, not `git add -A`, so stray
  files from a test run can't slip in.
- **Loop guard.** The workflow stops when the newest commit is cleanpush's own, so a push made
  with a personal token can't trigger endless runs.
- **Pinned install.** The workflow installs a release tag, so changes to cleanpush never reach a
  repo until you bump the tag.

## Languages

| Language | Formatter | Linter | Tested |
|---|---|---|---|
| Python | ruff | ruff | Locally and on real PRs in CI |
| JavaScript / TypeScript | ESLint `--fix`, prettier | ESLint | Locally on a sample project and on a real PR in CI |
| Go | gofmt | none | Code path only, not run |
| Others | none | none | Not supported |

For any language the agent can still do the language-independent parts (remove debug output,
update docs), but without a formatter style fixes depend on the model's judgment.

## Quick start

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
cd your-repo            # on a feature branch, clean working tree
cleanpush               # leaves the changes uncommitted so you can review them
cleanpush --commit      # ...or commit the result
```

| Option / env var | Meaning |
|---|---|
| `--base origin/main` | branch the code is merging into |
| `--commit` | commit the result (only files the agent touched) |
| `--check` | exit 1 if anything needed cleaning (CI gate) |
| `CLEANPUSH_MODEL` | model to use (default `claude-sonnet-5-5`) |
| `CLEANPUSH_TEST_CMD` | test command; auto-detects pytest, `npm test` and `go test` |

Each run saves its tool calls and summary to `.git/cleanpush-last-run.log`.

## Run it on every pull request

Copy [.github/workflows/cleanpush.yml](.github/workflows/cleanpush.yml) into your repo, then:

1. Add a repository secret `ANTHROPIC_API_KEY`.
2. **If your repo has required checks:** create a fine-grained personal access token with
   *Contents: Read and write* on that repo and add it as the secret `CLEANPUSH_TOKEN`. Pushes
   made with GitHub's built-in token don't start other workflows normally, so without it your
   required checks can sit "waiting for approval" on the cleanup commit.
3. Set `CLEANPUSH_TEST_CMD` in the workflow if your tests aren't detected automatically.
4. Optional: turn on branch protection for `main` so nothing merges without passing through it.

The workflow also has a **Run workflow** button (Actions tab) to clean any branch on demand.
To upgrade cleanpush, change the tag in the install step.

### Optional local hooks

- `hooks/pre-commit`: instant, free ruff formatting of staged Python files (no AI).
- `hooks/pre-push`: runs the full agent when pushing to `main`.

## Use the MCP server on its own

`repo-tools` is a standard MCP server, so other clients can use it too, e.g. Claude Code:

```bash
claude mcp add repo-tools -e CLEANPUSH_REPO=$PWD -- cleanpush-mcp
```

## Limitations

- It is prompt-driven, so it can occasionally step outside its rules. Review the cleanup commit
  like any other change.
- Verified on small projects (Python and JS). Larger or messier repos haven't been tried.
- Cost per run isn't measured. Runs on the demo diffs took roughly 15 seconds locally and about
  a minute in CI, including install.
- No formatter integration beyond Python, JS/TS and Go; test detection covers pytest, `npm test`
  and `go test` only.
- A fine-grained token expires, after which the workflow falls back to the built-in token.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```
