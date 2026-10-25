# SpecManager component workflow

Use this workflow to run a user-approved plan as sequential, independently
verified components. It is for users and contributors operating the repository
example. The `spec_manager` package is local to `example/`; it is deliberately
not exported by the reusable `pairo` package.

## Prerequisites

- Configure one planner as the director and at least one distinct
  developer/verifier pair in the agent mapping.
- Call `install_spec_manager(...)`, start the returned manager before
  `run_agents(...)`, and close it during shutdown. The verified integration is
  in [`main.py`](main.py).
- Optionally register a separate documenter. A documenter cannot also hold a
  planner, developer, or verifier role.

The installer adds the `spec_manager` MCP server, role skill, and role-specific
instructions to each planner, developer, and verifier. It does not configure
the optional documenter with those capabilities.

## Component lifecycle

The diagram answers: **who owns each decision, and when can the next component
start?**

```mermaid
sequenceDiagram
    actor User
    participant Planner
    participant SpecManager
    participant Developer
    participant Verifier
    participant Documenter

    User->>Planner: Request a feature
    Planner->>Planner: Inspect code and tests
    Planner->>User: Present the complete plan
    User-->>Planner: Approve the plan
    loop Each planner-authored component
        Planner->>SpecManager: create_task, then assign_task
        SpecManager-->>Developer: Deliver assignment when IDLE
        Developer->>Developer: Implement and run focused checks
        Developer->>SpecManager: verify_task(evidence)
        SpecManager-->>Verifier: Deliver evidence when IDLE
        alt Corrections required
            Verifier->>SpecManager: reject_task(corrections)
            SpecManager-->>Developer: Deliver corrections when IDLE
        else Component accepted
            Verifier->>SpecManager: accept_task
            SpecManager-->>Documenter: Notify with history path when IDLE (optional)
            Planner->>SpecManager: Assign the next component
        end
    end
```

The planner creates tasks only after the user approves the complete plan. Each
task has stable acceptance-criterion IDs such as `AC1` and `AC2`. The developer
implements only the assigned component, runs focused verification, and calls
`verify_task`. The verifier either accepts it or returns all structured
corrections. The planner assigns the next component only after acceptance; a
final integration component owns the full-suite checks.

`verify_task` evidence includes an implementation summary, changed files, one
evidence record per acceptance criterion, summarized test runs, correction
resolutions when applicable, and remaining risks. Test output should be
summarized rather than pasted verbatim.

## Request a specification change

If the assigned specification must change, the developer first asks the user.
Only after approval may the developer call `request_specification_change` with
nonblank `proposal`, `rationale`, and `prior_user_approval` fields. The request
notifies the planner and does not mutate the active component. The planner may
modify or delete only pending components, or create a follow-up component;
active and accepted components remain immutable.

## Handoffs and persisted records

Every SpecManager-originated assignment, verification request, correction,
specification-change request, and documenter notice waits until its recipient
is idle and is delivered once. Delivery has bounded timeout and shutdown
behavior; a failed assignment is returned to pending rather than silently
remaining active.

Records live under the `folder` passed to `install_spec_manager`. The current
example passes `Path("./tmp")`, so tasks are stored in `tmp/FEATURES.md` as
structured `pairo-task` blocks and accepted evidence is appended to
`tmp/output/FEATURE_HISTORY.md`. Existing structured task files remain
compatible; the former unstructured format is not supported.
