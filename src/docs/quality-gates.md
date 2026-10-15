# Quality gate

Run the complete project gate from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/quality_gate.py
```

The command runs, in order, the complete pytest suite, `mypy --strict pairo`, and the invariant architecture check.

## Architecture check

`INVARIANTS.md` prohibits nonspecific `Any`/`object` annotations, type-conditional dispatch, and procedural or static utility logic. The checker parses `pairo` with the Python AST and rejects each new occurrence. Existing technical debt is recorded by content fingerprint in `invariant-legacy-baseline.json`; changing or adding a construct is therefore rejected rather than silently grandfathered.

A narrowly scoped external boundary may be explicitly allowed immediately above the construct:

```python
# invariant: boundary-exception[external-sdk] -- SDK payload has no stable schema at this integration boundary.
def decode(payload: Any) -> JsonObject: ...
```

The explanation is mandatory and must name the boundary. Prefer a concrete domain type or polymorphic collaborator instead. Do not use wildcard excludes, blanket ignores, or broad module exemptions.
