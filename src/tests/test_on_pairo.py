import asyncio
from uuid import uuid4
from pydantic import BaseModel
from textual.app import App
from pairo.ui.pairo_event_piper import PairoEvent, on_pairo

class First(BaseModel): value: str
class Second(First): pass
class Other(BaseModel): value: str
class DecoratedApp(App[None]):
    def __init__(self) -> None:
        super().__init__(); self.received: list[str] = []
    @on_pairo(First)
    def first(self, event: First) -> None: self.received.append(event.value)
    @on_pairo(Second)
    async def second(self, event: Second) -> None: self.received.append(f"async:{event.value}")

def test_on_pairo_filters_inheritance_and_async_handlers() -> None:
    async def scenario() -> None:
        app = DecoratedApp()
        async with app.run_test() as pilot:
            app.post_message(PairoEvent(Other(value="ignored")))
            app.post_message(PairoEvent(Second(value="matched")))
            await pilot.pause()
            assert app.received == ["matched", "async:matched"]
    asyncio.run(scenario())

def test_on_pairo_accepts_textual_compatible_selector_arguments_without_model_selector_data() -> None:
    class SelectorApp(App[None]):
        @on_pairo(First, "#unused", control="#also-unused")
        def receive(self, event: First) -> None: self.value = event.value
    async def scenario() -> None:
        app = SelectorApp()
        async with app.run_test() as pilot:
            app.post_message(PairoEvent(First(value="delivered"))); await pilot.pause()
            assert app.value == "delivered"
    asyncio.run(scenario())
