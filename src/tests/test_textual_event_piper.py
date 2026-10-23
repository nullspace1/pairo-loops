import asyncio
from uuid import uuid4
from pairo.core.events import EventPipeline, MessageSent
from pairo.ui.pairo_event_piper import PairoEvent, TextualEventPiper

def test_textual_event_piper_fifo_and_detach() -> None:
    async def scenario() -> None:
        pipeline=EventPipeline(); received:list[str]=[]
        piper=TextualEventPiper(pipeline, lambda message: received.append(message.event.message) or True)
        piper.attach(); pipeline.push(MessageSent(agent_id=uuid4(), session_name="s", message="one")); pipeline.push(MessageSent(agent_id=uuid4(), session_name="s", message="two"))
        await asyncio.sleep(0); assert received == ["one", "two"]
        piper.detach(); pipeline.push(MessageSent(agent_id=uuid4(), session_name="s", message="three")); await asyncio.sleep(0); assert received == ["one", "two"]
    asyncio.run(scenario())

def test_pairo_event_preserves_raw_model() -> None:
    event=MessageSent(agent_id=uuid4(), session_name="s", message="visible")
    assert PairoEvent(event).event is event

