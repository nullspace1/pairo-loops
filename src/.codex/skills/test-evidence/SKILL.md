---
name: test-evidence
description: Run assigned test or verification commands and return reproducible evidence to the assigning developer or planner.
---

# Test evidence

Confirm the requested working directory, command, and relevant environment. Run the specified command as given; if it is unavailable, report why and use a nearby alternative only when it answers the same question.

Report the command, working directory, exit status, and a short result summary. For failures, include the failing test or check, the decisive error lines, and any logs or paths needed to reproduce it. Separate observed output from suspected cause. Do not edit production code while carrying out a verification assignment unless separately asked.
