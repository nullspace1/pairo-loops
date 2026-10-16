from enum import Enum


class InterruptState(str, Enum):
    """Tracks an interrupt request from intent through provider dispatch."""

    NONE = "none"
    PENDING = "pending"
    SENT = "sent"


