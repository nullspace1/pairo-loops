import asyncio
from uuid import uuid4

from pydantic import BaseModel

from pairo.core.events import (
    AgentActivityStarted,
    AgentError,
    AgentPlanDelta,
    AgentRecognizedProviderNoOp,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentTokenUsageUpdated,
    AgentUnknownProviderEvent,
    EventPipeline,
    MessageSent,
    SessionCreated,
    TokenUsage,
)
from pairo.ui.navigation_events import AgentSwitchRequested
from pairo.ui.pairo_event_piper import PairoEvent, TextualEventPiper, TextualIngressPolicy
from pairo.ui.transcript_events import TranscriptEntryAdded
from pairo.ui.models import EventDetailLevel, TranscriptEntry


class CustomUiEvent(BaseModel):
    value: str


def test_textual_event_piper_fifo_policy_and_detach() -> None:
    async def scenario() -> None:
        pipeline = EventPipeline()
        received: list[BaseModel] = []
        agent_id = uuid4()
        policy = TextualIngressPolicy((
            MessageSent, SessionCreated, AgentError, AgentTokenUsageUpdated,
            TranscriptEntryAdded, AgentSwitchRequested, CustomUiEvent,
        ))
        piper = TextualEventPiper(pipeline, lambda message: received.append(message.event) or True, policy)
        piper.attach()
        allowed = (
            MessageSent(agent_id=agent_id, session_name="s", message="one"),
            SessionCreated(agent_id=agent_id, session_name="s", identifier={}),
            AgentError(agent_id=agent_id, session_name="s", message="error"),
            AgentTokenUsageUpdated(agent_id=agent_id, session_name="s", last=TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=1, reasoning_output_tokens=0, total_tokens=2), total=TokenUsage(input_tokens=1, cached_input_tokens=0, output_tokens=1, reasoning_output_tokens=0, total_tokens=2)),
            TranscriptEntryAdded(agent_id=agent_id, session_name="s", entry=TranscriptEntry("system", "added", EventDetailLevel.NORMAL, {"type": "system"})),
            AgentSwitchRequested(agent_id=agent_id),
            CustomUiEvent(value="custom"),
        )
        rejected = (
            AgentTextDelta(agent_id=agent_id, session_name="s", text="text"),
            AgentPlanDelta(agent_id=agent_id, session_name="s", text="plan"),
            AgentReasoningDelta(agent_id=agent_id, session_name="s", text="reason"),
            AgentActivityStarted(agent_id=agent_id, session_name="s", activity={}),
            AgentRecognizedProviderNoOp(agent_id=agent_id, session_name="s", category="file_change_progress"),
            AgentUnknownProviderEvent(agent_id=agent_id, session_name="s", description="unknown"),
        )
        for event in (*allowed, *rejected):
            pipeline.push(event)
        await asyncio.sleep(0)
        assert received == list(allowed)
        piper.detach()
        pipeline.push(MessageSent(agent_id=agent_id, session_name="s", message="three"))
        await asyncio.sleep(0)
        assert received == list(allowed)

    asyncio.run(scenario())


def test_pairo_event_preserves_raw_model() -> None:
    event = MessageSent(agent_id=uuid4(), session_name="s", message="visible")
    assert PairoEvent(event).event is event
