---
name: spec-manager-director
description: Create, modify, delete, and assign tasks through the spec manager.
---

# Spec Manager Director

Use `list_tasks` and `get_task` as the authoritative backlog instead of mining
repository logs. Create tasks with a unique name, clear description, and
independently testable acceptance criteria named `AC1`, `AC2`, and so on.
Before assignment, use `modify_task` to
correct a pending task or `delete_task` to remove one that is no longer needed.
Tasks can only be modified or deleted while pending; create a follow-up task
instead of changing active or accepted work. Check pair availability before
assigning pending work. After assignment, report the delegated task and pending
completion condition before using `wait_for_task`. Treat `still_running` as a
normal bounded-wait result, provide a useful status update, and wait again.
