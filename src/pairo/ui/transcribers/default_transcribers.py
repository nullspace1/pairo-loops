"""Default event/transcript transcribers."""

from .agent_error_transcriber import AgentErrorTranscriber
from .activity_completed_transcriber import ActivityCompletedTranscriber
from .message_sent_transcriber import MessageSentTranscriber
from .network_message_sent_transcriber import NetworkMessageSentTranscriber
from .permission_request_transcriber import PermissionRequestTranscriber
from .token_usage_updated_transcriber import TokenUsageUpdatedTranscriber
from .transcriber import TranscriberList


def default_transcribers() -> TranscriberList:
    return [
        MessageSentTranscriber(),
        AgentErrorTranscriber(),
        NetworkMessageSentTranscriber(),
        PermissionRequestTranscriber(),
        ActivityCompletedTranscriber(),
        TokenUsageUpdatedTranscriber(),
    ]
