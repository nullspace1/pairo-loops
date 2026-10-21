---
name: spec-manager-worker
description: Implement an assigned spec-manager task and request verification.
---

# Spec Manager Worker

Implement the assigned task against its acceptance criteria. Call `verify_task`
when ready and address any rejection feedback before requesting verification
again.

The verification handoff must contain:

- A concise implementation and data-flow summary.
- The changed files.
- One evidence entry for every acceptance criterion.
- Summarized test runs: exact command, outcome, counts, duration, and a short
  description of what was covered.
- Remaining risks or explicit coverage gaps.

Do not submit raw test output. Keep summaries sufficient for the verifier to
target its independent inspection and checks without rereading the repository.
