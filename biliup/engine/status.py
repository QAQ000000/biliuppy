from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StreamStatus(str, Enum):
    LIVE = "live"
    OFFLINE = "offline"
    UNRECORDABLE = "unrecordable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StreamProbeResult:
    status: StreamStatus
    reason: str | None = None

    @classmethod
    def live(cls) -> StreamProbeResult:
        return cls(StreamStatus.LIVE)

    @classmethod
    def offline(cls) -> StreamProbeResult:
        return cls(StreamStatus.OFFLINE)

    @classmethod
    def unrecordable(cls, reason: str) -> StreamProbeResult:
        return cls(StreamStatus.UNRECORDABLE, reason)

    @classmethod
    def unknown(cls, reason: str | None = None) -> StreamProbeResult:
        return cls(StreamStatus.UNKNOWN, reason)
