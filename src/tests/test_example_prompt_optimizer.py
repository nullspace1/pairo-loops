"""Example optimizer agent should read prior execution logs and example assets."""

import asyncio
from pathlib import Path

from example.agents.prompt_optimizer.prompt_optimizer import get_prompt_optimizer
from pairo.core.events import EventPipeline


def test_prompt_optimizer_has_access_to_logs_and_example_directory() -> None:
    async def scenario() -> None:
        agent = get_prompt_optimizer(EventPipeline(), None)

        permissions = dict(agent.configuration().folder_permissions)
        resolved = {path.resolve() for path in permissions}

        assert (Path.cwd() / ".log").resolve() in resolved
        assert (Path.cwd() / "example").resolve() in resolved
        await agent.close()

    asyncio.run(scenario())
