"""A replaceable group of sidebar fields."""

from textual.containers import Vertical

from .sidebar_field import SidebarField


class SidebarFieldGroup(Vertical):
    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self._fields: tuple[tuple[str, str], ...] = ()

    async def set_fields(self, fields: tuple[tuple[str, object], ...]) -> None:
        normalized = tuple((label, str(value)) for label, value in fields)
        if normalized != self._fields:
            self._fields = normalized
            await self.remove_children()
            await self.mount(*(SidebarField(label, value) for label, value in normalized))
