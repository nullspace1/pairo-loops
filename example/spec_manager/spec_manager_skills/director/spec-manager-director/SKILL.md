---
name: spec-manager-director
description: Create, modify, delete, and assign tasks through the spec manager.
---

# Spec Manager Director

Inspect the relevant code and tests, present the user with a complete plan, and
obtain approval before writing tasks. Then use `list_tasks` and `get_task` as
the authoritative backlog instead of mining repository logs. Split the approved
plan into independently verifiable planner-authored components, with a final
integration component for full-suite evidence. Create tasks with a unique name,
clear description, and independently testable acceptance criteria named `AC1`,
`AC2`, and so on. Assign only the next component after the prior component is
accepted.
Before assignment, use `modify_task` to
correct a pending task or `delete_task` to remove one that is no longer needed.
Tasks can only be modified or deleted while pending; create a follow-up task
instead of changing active or accepted work. Check pair availability before
assigning pending work. After assignment, report the delegated task and pending
completion condition before using `wait_for_task`. Treat `still_running` as a
normal bounded-wait result, provide a useful status update, and wait again.
After a worker records unsuccessful work, clearly inform the user of the
recorded failure and wait for the user's response. Use that response to produce
a revised full plan, preserve the immutable unsuccessful record, create new
replacement component tasks, and assign the next replacement sequentially.
Verifier rejection is separate: it returns the active component to the worker
for correction and resubmission rather than triggering replanning.
