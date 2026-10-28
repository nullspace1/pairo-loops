# Pairo Loops

Pairo Loops is a Python library for building and running development agents. It combines an asynchronous agent runtime with a Textual terminal interface and a Codex provider, so developers can configure agents, interact with them, and resume their conversations.

## Features

- Configure agents with their own instructions, workspace, model, sandbox, approval policy, skills, and MCP servers.
- Run multiple agents in a terminal UI, with activity transcripts and interactive permission requests.
- Persist sessions and transcripts in SQLite and switch between conversations.
- Connect agents through an event pipeline; optional tools support event logging, chatrooms, and workspace isolation.
- Use a fake agent for testing and a Codex adapter for real sessions.

## Getting started

You need Python 3.10 or newer and an installed, authenticated Codex CLI for Codex-backed agents. Choose a model available to your Codex account. From the repository root, create an environment and install the project dependencies (PowerShell):

```powershell
py -m venv src\.venv
.\src\.venv\Scripts\python.exe -m pip install -r src\requirements.txt aiosqlite
```

The package lives in `src`. To run the following example from the repository root, save it as `src\quickstart.py` and run `.\src\.venv\Scripts\python.exe src\quickstart.py`:

```python
import asyncio

from pairo import (
    AgentConfiguration,
    ApprovalMode,
    CodexAgent,
    EventPipeline,
    SandboxPermission,
    run_agents,
)
from pairo.ui import SqliteSessionRepository, TextualPermissionRequester


async def main() -> None:
    pipeline = EventPipeline()
    repository = SqliteSessionRepository()
    permissions = TextualPermissionRequester(pipeline, repository)
    configuration = AgentConfiguration(
        name="reviewer",
        workspace=".",
        model="YOUR_CODEX_MODEL",
        sandbox=SandboxPermission.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.USER,
        permission_requester=permissions,
    )
    agent = CodexAgent(configuration, pipeline)
    await run_agents([agent], pipeline, repository)


asyncio.run(main())
```

Replace `YOUR_CODEX_MODEL` with an available model. The workspace is resolved from the current directory and must exist. The UI starts with a persisted session; use `/help` to see available commands, including `/sessions`, `/session new [NAME]`, and `/interrupt`.

For a larger example, run `.\src\.venv\Scripts\python.exe example\main.py` from the repository root. It configures several agents and a [specification management workflow](example/SPEC_MANAGER_WORKFLOW.md). Its GitHub MCP integration uses Docker and the GitHub MCP server; its agent models and workspace configuration may need adjustment for your environment.

## Documentation and help

- [Documentation index and system overview](src/docs/README.md)
- [Feature reference](src/docs/feature-reference.md) for configuration, sessions, commands, and integrations
- [Codex provider architecture](src/docs/codex-agent.md)
- [Quality gate](src/docs/quality-gates.md) and [project invariants](src/INVARIANTS.md)

For questions or defects, use the repository's issue tracker and include enough context to reproduce the problem.

## Contributing

Contributions are welcome through issues and pull requests. Review the [project invariants](src/INVARIANTS.md) and [quality gate documentation](src/docs/quality-gates.md) before making changes using agents. From `src`, run the checks with the project interpreter:

```powershell
New-Item -ItemType Directory -Force .verify-readme | Out-Null
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.verify-readme\pytest
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts\quality_gate.py
```

Use a unique existing parent directory for each test run or agent. The repository does not currently identify individual maintainers or include a separate contribution guide or license file; consult the repository for its current ownership and contribution process.
