---
name: spec-manager-worker
description: Implement an assigned spec-manager task and request verification.
---

# Spec Manager Worker

Implement the assigned task against its acceptance criteria. Call `verify_task`
when ready and address any rejection feedback before requesting verification
again.

Before submission, map every criterion ID (`AC1`, `AC2`, and so on) to
observable evidence and supporting test-run IDs (`TR1`, `TR2`, and so on).
These identifiers are at most 20 characters. A broad passing suite, source inspection, or a private-field
assertion does not establish user-visible behavior. Resolve every outstanding
correction ID before resubmitting.

The verification handoff must contain:

- A concise implementation and data-flow summary.
- The changed files.
- Exactly one evidence entry for every acceptance-criterion ID.
- Summarized test runs: exact command, outcome, counts, duration, and a short
  description of what was covered.
- One resolution for every outstanding correction ID.
- Remaining risks or explicit coverage gaps.

Do not submit raw test output. Keep summaries sufficient for the verifier to
target its independent inspection and checks without rereading the repository.
