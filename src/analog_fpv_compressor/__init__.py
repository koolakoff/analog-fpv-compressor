"""Compress analog FPV DVR recordings while retaining useful flight information."""

from .models import Analysis, CancelToken, Event, Plan, Result, Settings

__version__ = "0.2.0"
__all__ = ["Analysis", "CancelToken", "Event", "Plan", "Result", "Settings", "analyze", "build_plan", "process", "execute", "make_jobs", "plan_outputs"]


def make_jobs(inputs, **options):
    """Resolve input patterns and output naming into per-input Settings."""
    from .jobs import make_jobs as implementation
    return implementation(inputs, **options)


def plan_outputs(plan, split_flights=False):
    """Resolve one source plan into joined or numbered independent outputs."""
    from .jobs import plan_outputs as implementation
    return implementation(plan, split_flights=split_flights)


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
