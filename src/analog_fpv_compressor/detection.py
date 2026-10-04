"""Conservative snow detection on native frame timestamps, without blue removal."""

import numpy as np


POLICY_VERSION = "snow-1.0"


def true_runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def snow_intervals(metrics, times, frame_step, minimum=1.0, margin=0.25):
    """Confirm sustained snow and preserve structure-bearing frames inside it.

    Duration occupancy is measured in time, with long source gaps bounded to
    one nominal frame; absent images are never extra evidence of snow.
    Small rejected runs may bridge only unstructured noise or uniform screens.
    A structure-bearing rejection is preserved even inside a sustained event.
    """
    data = np.asarray(metrics, dtype=float)
    times = np.asarray(times, dtype=float)
    if not len(times):
        return (), {"candidate_frames": 0, "policy": POLICY_VERSION}
    mean, std, coarse, mad, corr = data.T
    ratio = coarse / np.maximum(std, 1e-6)
    mask = (corr < .12) & (mad > 25) & (ratio < .55) & (std > 15)
    structured = (ratio >= .70) & (std >= 15)
    bridged = mask.copy()
    for start, end in true_runs(~mask):
        if start > 0 and end < len(mask) and times[end] - times[start] <= .5:
            if not structured[start:end].any():
                bridged[start:end] = True
    cuts = []
    duration_weights = np.minimum(np.diff(np.r_[times, times[-1] + frame_step]), frame_step)
    for start, end in true_runs(bridged):
        raw_end = times[end] if end < len(times) else times[-1] + frame_step
        duration = raw_end - times[start]
        weights = duration_weights[start:end]
        occupancy = float((weights * mask[start:end]).sum() / weights.sum())
        if duration >= minimum and weights.sum() >= minimum and occupancy >= .70:
            cut_start = times[start] + margin
            # A sustained snow tail contains no following scene to protect.
            cut_end = raw_end if end == len(times) else raw_end - margin
            if cut_end > cut_start:
                cuts.append((float(cut_start), float(cut_end)))
    return tuple(cuts), {"candidate_frames": int(mask.sum()), "policy": POLICY_VERSION,
                        "minimum_seconds": minimum, "edge_margin_seconds": margin,
                        "bridge_seconds": .5, "minimum_time_occupancy": .70,
                        "protected_structured_frames": int((structured & ~mask).sum())}


def complement(cuts, start, end):
    """Return ordered half-open source intervals remaining after deletion."""
    cursor = start
    kept = []
    for begin, finish in sorted(cuts):
        begin, finish = max(start, begin), min(end, finish)
        if finish <= cursor:
            continue
        if begin > cursor:
            kept.append((cursor, begin))
        cursor = max(cursor, finish)
    if cursor < end:
        kept.append((cursor, end))
    return tuple(kept)
