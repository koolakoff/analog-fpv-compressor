"""Compress analog FPV DVR recordings while retaining useful flight information."""

from .models import Analysis, CancelToken, Event, Plan, Result, Settings

__version__ = "0.1.0"
__all__ = ["Analysis", "CancelToken", "Event", "Plan", "Result", "Settings", "analyze", "build_plan", "process", "execute"]


def analyze(settings, emit=None, cancel=None):
    """Analyze input without eagerly importing numerical dependencies."""
    from .controller import analyze as implementation
    return implementation(settings, emit=emit, cancel=cancel)


def build_plan(settings, analysis):
    """Resolve processing options from the shared measured input."""
    from .controller import build_plan as implementation
    return implementation(settings, analysis)


def execute(plan, emit=None, cancel=None):
    """Execute and validate an already resolved plan."""
    from .processing import execute as implementation
    return implementation(plan, emit=emit, cancel=cancel)


def process(plan, on_event=None, cancel=None):
    """Convenience execution entry point for clients using on_event callbacks."""
    return execute(plan, emit=on_event, cancel=cancel)
