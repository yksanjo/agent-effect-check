# agent-effect-check

Verify that an agent's filesystem effects match what it declared before it ran.

An agent says it will update `src/*.py`, write `report.md`, and clear
`cache/*.tmp`. It runs. Did it also write a `.env`, delete your README, or
touch anything else? `agent-effect-check` answers that with three offline
steps: hash-snapshot the directory before the run, snapshot after, then
reconcile the diff against a declared-effects manifest. Undeclared changes
come out as errors for a human to review.

The tool never executes the agent and never blocks anything. It is a
review signal, not a sandbox.

## Requirements

Python 3.10+. Standard library only - no dependencies, no network.

## Usage

```
# 1. before the run
python3 effect_check.py snapshot /path/to/project > before.json

# 2. the agent runs (outside this tool)

# 3. after the run
python3 effect_check.py snapshot /path/to/project > after.json

# inspect what changed
python3 effect_check.py diff before.json after.json

# reconcile against the declaration
python3 effect_check.py check before.json after.json --declared effects.json
python3 effect_check.py check before.json after.json --declared effects.json --json
```

Exit codes: `0` when every observed change is declared, `1` on any
undeclared change, `2` for usage or input problems.

Run the demo and tests:

```
python3 demo.py
python3 -m unittest test_effect_check
```

## Declared-effects manifest

```json
{
  "effects": [
    {"effect": "modify", "path": "src/*.py"},
    {"effect": "create", "path": "report.md"},
    {"effect": "delete", "path": "cache/*.tmp"}
  ]
}
```

`effect` is one of `create`, `modify`, `delete`. `path` is a glob matched
against the path relative to the snapshotted root (`fnmatch` rules). A
declaration that matches nothing is reported as info
(`declared-not-observed`) - either the declaration is stale or the agent
did less than it said.

## Rules

| rule | severity | what it catches |
|------|----------|-----------------|
| `invalid-manifest` / `invalid-declaration` | error | malformed declaration input |
| `undeclared-create` | error | a file appeared with no matching declaration |
| `undeclared-modify` | error | a file's content changed with no matching declaration |
| `undeclared-delete` | error | a file disappeared with no matching declaration |
| `declared-not-observed` | info | a declaration matched nothing that happened |

`snapshot --exclude <glob>` skips paths (repeatable), e.g.
`--exclude .git --exclude __pycache__/*`.

## Limits

- **Scope is one directory tree.** Writes outside the snapshotted root,
  network calls, spawned processes, environment changes, and deleted
  snapshots are all invisible to this tool.
- **Hashes prove change, not authorship.** A changed hash shows content
  changed between snapshots; it does not prove the agent (rather than a
  cron job, a human, or another process) changed it.
- **Race window.** Anything that happens between the run and the second
  snapshot is attributed to the run. Snapshot promptly.
- **Globs are `fnmatch`, not a policy language.** Overly broad patterns
  (`*`) will match real violations. Write declarations narrowly.
- A clean result means "no undeclared filesystem changes in this tree" -
  not that the run was safe, correct, or authorized.
- All demo and test data is synthetic.

## License

MIT - see [LICENSE](LICENSE).
