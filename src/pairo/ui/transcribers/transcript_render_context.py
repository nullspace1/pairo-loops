"""Context supplied while rendering a transcript entry."""

from dataclasses import dataclass
from uuid import UUID

from pairo.core.events import EventPipeline




@dataclass(frozen=True, slots=True)
class TranscriptRenderContext:
    agent_id: UUID
    session_name: str
    event_pipeline: EventPipeline
    is_live: bool
    agent_name: str = "Agent"
