"""Default event/transcript transcribers."""

from .agent_error_transcriber import AgentErrorTranscriber
from .command_execution_transcriber import CommandExecutionTranscriber
from .mcp_tool_call_transcriber import McpToolCallTranscriber
from .message_sent_transcriber import MessageSentTranscriber
from .network_message_sent_transcriber import NetworkMessageSentTranscriber
from .permission_request_transcriber import PermissionRequestTranscriber
from .session_table_transcriber import SessionTableTranscriber
from .token_usage_updated_transcriber import TokenUsageUpdatedTranscriber
from .transcriber import TranscriberRegistry


def default_transcribers(command_output_limit: int = CommandExecutionTranscriber.DEFAULT_COMMAND_OUTPUT_LIMIT) -> TranscriberRegistry:
    """Build the standard transcriber registry used by the UI."""
    registry = TranscriberRegistry()
    registry.register(MessageSentTranscriber())
    registry.register(AgentErrorTranscriber())
    registry.register(NetworkMessageSentTranscriber())
    registry.register(PermissionRequestTranscriber())
    registry.register_durable(SessionTableTranscriber())
    registry.register(CommandExecutionTranscriber(command_output_limit))
    registry.register(McpToolCallTranscriber())
    registry.register(TokenUsageUpdatedTranscriber())
    return registry

