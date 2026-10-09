---
name: feature-implementation
description: Implement or revise a planned software feature in the repository, including targeted verification and a handoff for technical review.
---

# Feature implementation

Read the brief, plan, and directly affected code. State the component responsibilities and expected execution flow before making material changes. Keep the high-level flow visible in callers, use small functions with clear names, and avoid duplicating business rules.

Implement in coherent increments. Make error handling contextual at the right boundary. Add tests when they verify important behavior or a regression risk. Ask the intern for bounded test runs or documentation updates when useful, then inspect the evidence and final diff yourself.

Report to the planner with changed files, behavior, checks run and outcomes, deviations from the plan, and unresolved risks. If the plan needs a material architectural change, surface it before committing to that direction.
