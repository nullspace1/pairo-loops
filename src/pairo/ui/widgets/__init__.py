"""Public Textual widget API with cycle-safe lazy exports."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent_chat_pane import AgentChatPane
    from .agent_sidebar import AgentSidebar
    from .sidebar_field import SidebarField
    from .sidebar_field_group import SidebarFieldGroup
    from .sidebar_statistics import (
        DefaultSidebarStatisticValues,
        FixedStatistic,
        SidebarFieldDefinition,
        SidebarStatistic,
        SidebarStatisticCategory,
        SidebarStatisticFields,
        TimestampFormatter,
        TokenUsageStatistic,
        UpdateAgentStatistic,
        UpdateSessionStatistic,
        create_default_sidebar_statistics,
        default_sidebar_statistics,
    )
    from .transcript import Transcript


_MODULES = {
    "AgentChatPane": "agent_chat_pane",
    "AgentSidebar": "agent_sidebar",
    "SidebarField": "sidebar_field",
    "SidebarFieldGroup": "sidebar_field_group",
    "DefaultSidebarStatisticValues": "sidebar_statistics",
    "FixedStatistic": "sidebar_statistics",
    "SidebarFieldDefinition": "sidebar_statistics",
    "SidebarStatistic": "sidebar_statistics",
    "SidebarStatisticCategory": "sidebar_statistics",
    "SidebarStatisticFields": "sidebar_statistics",
    "TimestampFormatter": "sidebar_statistics",
    "TokenUsageStatistic": "sidebar_statistics",
    "UpdateAgentStatistic": "sidebar_statistics",
    "UpdateSessionStatistic": "sidebar_statistics",
    "create_default_sidebar_statistics": "sidebar_statistics",
    "default_sidebar_statistics": "sidebar_statistics",
    "Transcript": "transcript",
}


def __getattr__(name: str) -> Any:
    module_name = _MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


__all__ = [
    "AgentChatPane",
    "AgentSidebar",
    "DefaultSidebarStatisticValues",
    "FixedStatistic",
    "SidebarField",
    "SidebarFieldDefinition",
    "SidebarFieldGroup",
    "SidebarStatistic",
    "SidebarStatisticCategory",
    "SidebarStatisticFields",
    "TimestampFormatter",
    "TokenUsageStatistic",
    "UpdateAgentStatistic",
    "UpdateSessionStatistic",
    "create_default_sidebar_statistics",
    "default_sidebar_statistics",
    "Transcript",
]
