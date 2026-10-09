---
name: implementation-design
description: Design an implementation plan or review a developer's change against feature scope and existing architecture. Use when technical boundaries, integration, or sequencing matter.
---

# Implementation design

Inspect the relevant code path and tests before recommending changes. Trace the entry point, data flow, persistence or external calls, and failure boundaries. Use targeted reads; ask the intern for bounded summaries when the code area is large. Verify any facts received from another agent that drive a design decision.

For a plan, define component responsibilities and their interfaces, then order the work into small verifiable steps. Name files only when confirmed. Call out migration, compatibility, concurrency, and error handling where they are real risks. Tie verification to the product owner's acceptance criteria and existing tests.

For a review, compare the diff and evidence with the brief and plan. Prioritize correctness, integration, and maintenance costs. Give each finding a location, consequence, and requested change. Escalate product ambiguity to the product owner and send technical corrections to the developer.
