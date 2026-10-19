"""Image-publication command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pairo.core.events import ImageSent

from ..commands import CommandOutcome
from ..models import EventDetailLevel, MessageTranscriptMetadata, TranscriptEntry
from .base import SlashCommand

if TYPE_CHECKING:
    from ..commands import AgentCommandController


class ImageCommand(SlashCommand):
    name = "/image"

    async def invoke(
        self,
        controller: AgentCommandController,
        agent_id: UUID,
        argument: str,
    ) -> CommandOutcome:
        if not argument:
            raise ValueError("Usage: /image PATH")
        controller.event_pipeline.push(ImageSent(image_path=argument))
        return CommandOutcome(
            TranscriptEntry[MessageTranscriptMetadata](
                "user", f"Image: {argument}", EventDetailLevel.NORMAL, {"type": "message"}
            )
        )

