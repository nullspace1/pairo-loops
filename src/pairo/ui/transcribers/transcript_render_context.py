"""Context supplied while rendering a transcript entry."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pairo.core.events import EventPipeline

if TYPE_CHECKING:
    from .transcriber import TranscriptMutations



@dataclass(frozen=True, slots=True)
class TranscriptRenderContext:
    agent_id: UUID
    session_name: str
    event_pipeline: EventPipeline
    mutations: "TranscriptMutations | None"
    is_live: bool
    agent_name: str = "Agent"
