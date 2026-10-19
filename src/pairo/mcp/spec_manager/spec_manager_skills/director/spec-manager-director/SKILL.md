---
name: spec-manager-director
description: Create, modify, delete, and assign tasks through the spec manager.
---

# Spec Manager Director

Create tasks with a unique name, clear description, and testable acceptance criteria. Before assignment, use `modify_task` to correct a pending task or `delete_task` to remove one that is no longer needed. Tasks can only be modified or deleted while pending; create a follow-up task instead of changing active or accepted work. Check pair availability before assigning pending work. Use `sleep_on_task` to wait for an assigned task to be accepted.
