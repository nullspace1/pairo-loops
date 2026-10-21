---
name: write-project-documentation
description: Create or revise verified user documentation and Mermaid diagrams for project concepts, workflows, architecture, and agent interactions; also maintain AGENTS.md files, role prompts, skills, and MCP guidance when those are the requested artifacts.
---

# Write project documentation

Identify the audience and the question the documentation must answer. Inspect
the existing documentation structure, then verify the relevant behavior through
targeted reads of public interfaces, tests, examples, prompts, skills, and tool
schemas. Prefer an existing authoritative page over creating a competing one.
When invoked from a SpecManager feature-history notification, use the referenced
history entry as the initial change summary and follow its changed-file and
verification references instead of reconstructing the handoff from other agents.

For user documentation, lead with the outcome and prerequisites. Explain the
smallest useful mental model, provide commands or examples a reader can run,
and separate verified behavior from limitations or future intent. Use public
names consistently and avoid exposing internal details that do not help the
reader operate or understand the system.

Add a Mermaid diagram when it makes a relationship materially easier to
understand:

- Use a flowchart for components and data flow.
- Use a sequence diagram for interactions over time.
- Use a state diagram for lifecycle transitions and allowed decisions.
- Use a small dependency graph for ownership or integration boundaries.

Keep diagrams focused, label edges with meaningful actions, and use names from
the implementation or public interface. Explain the diagram in adjacent prose
so the page remains understandable when diagram rendering is unavailable. Do
not diagram a one-step action, duplicate a simple list, or combine unrelated
flows into one large graph. Check Mermaid syntax and confirm every diagrammed
claim against the same sources used for the prose.

For agent-facing artifacts, choose the narrowest authoritative location:

- Shared repository facts, architecture, commands, and constraints belong in
  the nearest applicable `AGENTS.md`.
- A role's authority, responsibilities, collaboration boundaries, and handoff
  belong in its prompt.
- Reusable task-specific judgment belongs in a skill with a discriminating name
  and description.
- Machine-enforceable requirements belong in schemas or configuration. Explain
  their purpose in prose without duplicating their full definition.

Assume both users and agents are capable. Include information that changes
decisions: verified paths, ownership boundaries, execution flow, constraints,
evidence requirements, and known failure modes. Remove generic advice,
historical narrative, redundant procedures, and examples that do not clarify a
real choice.

Before completion, check that commands and paths exist, linked instructions
resolve, tool names and inputs match their schemas, and edited pages do not
contradict each other. Validate changed skills with the available skill
validator. Report changed files, verification sources, diagram purpose, and any
claim that remains intentionally unverified.
