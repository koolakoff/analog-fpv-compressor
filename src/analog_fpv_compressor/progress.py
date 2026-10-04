"""Emit stage-local progress without estimating overall job duration."""

from .models import Event


def emit_progress(sink, stage, completed=None, total=None, *, state="running", unit=None, **details):
    """Use a null fraction when a stage has no measurable total."""
    if sink is None:
        return
    fraction = min(1., max(0., completed / total)) if completed is not None and total is not None and total > 0 else None
    sink(Event("progress", {**details, "stage": stage, "completed": completed,
                            "total": total, "fraction": fraction, "state": state, "unit": unit}))
