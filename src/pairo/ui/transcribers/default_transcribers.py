"""Default event/transcript transcribers."""

from .agent_error_transcriber import AgentErrorTranscriber
from .activity_completed_transcriber import ActivityCompletedTranscriber
from .message_sent_transcriber import MessageSentTranscriber
from .network_message_sent_transcriber import NetworkMessageSentTranscriber
from .permission_request_transcriber import PermissionRequestTranscriber
from .token_usage_updated_transcriber import TokenUsageUpdatedTranscriber
from .transcriber import TranscriberRegistry


def default_transcribers() -> TranscriberRegistry:
    """Build the standard transcriber registry used by the UI."""
    registry = TranscriberRegistry()
    registry.register(MessageSentTranscriber())
    registry.register(AgentErrorTranscriber())
    registry.register(NetworkMessageSentTranscriber())
    registry.register(PermissionRequestTranscriber())
    registry.register(ActivityCompletedTranscriber())
    registry.register(TokenUsageUpdatedTranscriber())
    return registry

