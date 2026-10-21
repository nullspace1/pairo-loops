@./src/INVARIANTS.MD
@./docs

## Verification commands

Always use the project interpreter; do not run bare `python` or `pytest`:

```powershell
.venv\Scripts\python.exe -m pytest <targets> -q -p no:cacheprovider --basetemp=<agent-temp>\pytest
.venv\Scripts\python.exe -m mypy
```

Use a unique, existing parent directory for each agent's `--basetemp`. Do not
reuse `.pytest_cache` or another agent's temporary directory.

## Code inspection

- Start with `rg` and targeted line ranges.
- Do not read complete files when the relevant symbol or range is known.
- Inspect the current diff before rereading unchanged files.
- Reuse structured task evidence supplied by another agent, while independently
  checking evidence relevant to your role.
- Successful test handoffs should contain the command, counts, duration, and a
  short coverage summary—not raw test output.
