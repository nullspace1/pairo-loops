from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    GENERATING = "generating"
    STOPPED = "stopped"

