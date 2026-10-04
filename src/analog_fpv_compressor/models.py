"""Shared data contracts for analysis, planning, execution, and front ends."""

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any, Callable


@dataclass(frozen=True)
class Settings:
    """Requested options; auto values remain unresolved until planning."""

    input_path: Path
    output_path: Path
    crf: str | int = "auto"
    bitrate: str = "auto"
    preset: str | int = "auto"
    scale: str = "auto"
    denoise: str = "auto"
    deinterlace: str = "auto"
    field_order: str = "auto"
    cut_no_signal: str = "auto"
    no_signal_min_duration: str | float = "auto"
    audio: str = "remove"
    codec: str = "av1"
    ffmpeg_dir: Path | None = None
    threads: int = 4
    report_path: Path | None = None
    log_path: Path | None = None


@dataclass(frozen=True)
class Analysis:
    """Measured input properties and cautious source-time cut proposals."""

    input_path: Path
    probe: dict[str, Any]
    timestamps: tuple[float, ...] = ()
    frame_pts: tuple[int, ...] = ()
    cut_intervals: tuple[tuple[float, float], ...] = ()
    interlace: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[Any, ...] = ()
    tools: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """Resolved options, source intervals, and explanations for execution."""

    settings: Settings
    analysis: Analysis
    selected: dict[str, Any]
    keep_intervals: tuple[tuple[float, float], ...]
    removed_intervals: tuple[tuple[float, float], ...]
    mapping: tuple[dict[str, Any], ...]
    reasons: dict[str, Any]
    policy_version: str


@dataclass(frozen=True)
class Result:
    """Verified output and its in-memory diagnostic report."""

    output_path: Path
    report_path: Path | None
    bytes: int
    duration_seconds: float
    report: dict[str, Any]

    @property
    def reportdict(self):
        return self.report


@dataclass(frozen=True)
class Event:
    """Locale-independent event code and structured parameters."""

    code: str
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


class CancelToken:
    """Thread-safe cancellation request shared with subprocess controllers."""

    def __init__(self):
        self._event = ThreadEvent()

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()


def to_dict(value: Any) -> Any:
    """Convert shared models to JSON-compatible values without losing paths."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_dict(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dict(item) for item in value]
    return value
