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
        self._fields: tuple[tuple[str, str, str], ...] = ()

    async def set_fields(self, fields: tuple[tuple[str, str, object], ...]) -> None:
        normalized = tuple((key, label, str(value)) for key, label, value in fields)
        if normalized == self._fields:
            return
        current_keys = tuple(key for key, _, _ in self._fields)
        next_keys = tuple(key for key, _, _ in normalized)
        self._fields = normalized
        if current_keys != next_keys:
            await self.remove_children()
            await self.mount(*(SidebarField(key, label, value) for key, label, value in normalized))
            return
        # Periodic refreshes keep the same provider ordering.  Mutate those
        # fields in place so focus and widget identity are preserved.
        mounted_fields = (child for child in self.children if isinstance(child, SidebarField))
        for field, (_, label, value) in zip(mounted_fields, normalized):
            field.update(label, value)

