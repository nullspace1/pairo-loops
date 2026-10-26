---
name: spec-manager-worker
description: Implement an assigned spec-manager task and request verification.
---

# Spec Manager Worker

Implement only the assigned planner-authored component against its acceptance
criteria. Do not generate a second plan or seek plan approval. Run focused
verification and call `verify_task` before the next component proceeds; the
planner owns the final integration component and full-suite evidence. On
rejection, use the correction IDs, criterion IDs, and messages returned by
`verify_task` (the idle-safe notification is supplemental) before requesting
verification again. If completion is
impossible, stop safely and perform no further speculative implementation.
Describe the blocker and relevant partial work through
`report_unsuccessful_work`; the planner, not the worker, obtains the user's
response and creates a revised plan.

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
