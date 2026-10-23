"""Default event/transcript transcribers."""

from .agent_error_transcriber import AgentErrorTranscriber
from .command_execution_completed_transcriber import CommandExecutionCompletedTranscriber
from .command_execution_progress_transcriber import CommandExecutionProgressTranscriber
from .mcp_tool_call_completed_transcriber import McpToolCallCompletedTranscriber
from .mcp_tool_call_progress_transcriber import McpToolCallProgressTranscriber
from .message_sent_transcriber import MessageSentTranscriber
from .network_message_sent_transcriber import NetworkMessageSentTranscriber
from .permission_request_transcriber import PermissionRequestTranscriber
from .session_table_transcriber import SessionTableTranscriber
from .token_usage_updated_transcriber import TokenUsageUpdatedTranscriber
from .transcriber import TranscriberRegistry


def default_transcribers() -> TranscriberRegistry:
    """Build the standard transcriber registry used by the UI."""
    registry = TranscriberRegistry()
    registry.register(MessageSentTranscriber())
    registry.register(AgentErrorTranscriber())
    registry.register(NetworkMessageSentTranscriber())
    registry.register(PermissionRequestTranscriber())
    registry.register_durable(SessionTableTranscriber())
    registry.register(CommandExecutionCompletedTranscriber())
    registry.register(CommandExecutionProgressTranscriber())
    registry.register(McpToolCallCompletedTranscriber())
    registry.register(McpToolCallProgressTranscriber())
    registry.register(TokenUsageUpdatedTranscriber())
    return registry

