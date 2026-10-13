from enum import Enum


class WebSearchMode(str, Enum):
    DISABLED = "disabled"
    CACHED = "cached"
    LIVE = "live"
