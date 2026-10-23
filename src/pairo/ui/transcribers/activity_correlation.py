"""Opaque transcript keys for the lifecycle of routed provider activities."""

from hashlib import sha256
from uuid import UUID


class ActivityCorrelation:
    """Derive a stable entry key without retaining a provider activity ID."""

    def __init__(self, agent_id: UUID, session_name: str, family: str, activity_id: str) -> None:
        self._agent_id = agent_id
        self._session_name = session_name
        self._family = family
        self._activity_id = activity_id

    @property
    def entry_id(self) -> UUID:
        material = "\x1f".join((str(self._agent_id), self._session_name, self._family, self._activity_id))
        return UUID(bytes=sha256(material.encode("utf-8")).digest()[:16])
