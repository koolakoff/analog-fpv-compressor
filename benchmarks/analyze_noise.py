"""Evaluate simple no-signal hypotheses on locally extracted frame metrics.

This is a research script, not a production classifier. Approximate annotations
are evaluation references, never inputs to the candidate decision.
"""

import argparse
import json
from pathlib import Path

import numpy as np


POLICIES = {
    "conservative": {"corr_max": 0.12, "mad_min": 25, "structure_ratio_max": 0.55},
    "broad": {"corr_max": 0.20, "mad_min": 22, "structure_ratio_max": 0.70},
}
REFERENCES = {
    "air-school-stadion-oneflight": [(279.0, float("inf"))],
    "air-school-stadion-with-termination": [(75.5, 145.0)],
}


def runs(mask):
    """Return half-open index spans of consecutive true values."""
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def confirmed_intervals(mask, times, step, gap=0.5, minimum=1.0, margin=0.25):
    """Bridge short rejected runs, require duration and occupancy, retain edges."""
    bridged = mask.copy()
    for start, end in runs(~mask):
        if start > 0 and end < len(mask) and times[end] - times[start] <= gap:
            bridged[start:end] = True
    result = []
    for start, end in runs(bridged):
        raw_end = times[end] if end < len(times) else times[-1] + step
        duration = raw_end - times[start]
        occupancy = float(mask[start:end].mean())
        if duration >= minimum and occupancy >= 0.70:
            # Shrink the deletion proposal: preserve potentially useful boundaries.
            proposed_start, proposed_end = times[start] + margin, raw_end - margin
            if proposed_end > proposed_start:
                result.append({"candidate_start_s": float(times[start]),
                               "candidate_end_s": float(raw_end),
                               "proposed_cut_start_s": float(proposed_start),
                               "proposed_cut_end_s": float(proposed_end),
                               "candidate_occupancy": occupancy})
    return result


def distribution(data, mask):
    """Report robust quantiles of the measured luminance features."""
    return {key: dict(zip(["p01", "p10", "p50", "p90", "p99"],
                         np.quantile(data[key][mask], [.01, .1, .5, .9, .99]).tolist()))
            for key in ["mean", "std", "coarse_std", "mad", "corr"]}


def top_events(data, allowed, limit=20):
    """Rank temporal changes for visual review without labelling their cause."""
    mean_change = np.abs(np.diff(data["mean"], prepend=data["mean"][0]))
    score = data["mad"] + mean_change
    ranked = np.argsort(score)[::-1]
    selected = []
    for index in ranked:
        instant = float(data["avi_time_s"][index])
        if not allowed[index] or any(abs(instant - event["time_s"]) < 1 for event in selected):
            continue
        selected.append({"time_s": instant, "frame": int(data["frame"][index]),
                         "review_score": float(score[index]),
                         "mean_change": float(mean_change[index]),
                         **{key: float(data[key][index]) for key in ["mean", "std", "coarse_std", "mad", "corr"]}})
        if len(selected) == limit:
            break
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("benchmarks/results/initial"))
    args = parser.parse_args()
    summary = {"kind": "research_only", "time_basis": "decoded AVI best_effort timestamps",
               "feature_resolution": "central 128x96 from bilinear 160x120 luminance",
               "policies": POLICIES,
               "temporal_rules": {"bridge_gap_s": .5, "minimum_interval_s": 1,
                                  "minimum_occupancy": .7, "preserved_edge_margin_s": .25},
               "annotation_warning": "Approximate snow labels; boundary discrepancies are not verified false positives.",
               "files": {}}
    for source in sorted(args.directory.glob("*.metrics.csv")):
        stem = source.name.removesuffix(".metrics.csv")
        if stem not in REFERENCES:
            print(f"{stem}: skipped; no snow-only reference annotation", flush=True)
            continue
        data = np.genfromtxt(source, delimiter=",", names=True)
        times = data["avi_time_s"]
        step = float(np.median(np.diff(times)))
        reference = np.zeros(len(data), dtype=bool)
        for start, end in REFERENCES[stem]:
            reference |= (times >= start) & (times < end)
        ratio = data["coarse_std"] / np.maximum(data["std"], 1e-6)
        report = {"frame_count": len(data), "median_step_s": step,
                  "timestamp_gaps": [{"previous_time_s": float(times[index - 1]),
                                      "next_time_s": float(times[index]),
                                      "gap_s": float(times[index] - times[index - 1])}
                                     for index in np.flatnonzero(np.r_[False, np.diff(times) > step * 1.5])],
                  "approximate_reference_intervals_s": [[a, b if np.isfinite(b) else float(times[-1] + step)] for a, b in REFERENCES[stem]],
                  "reference_distributions": {"snow": distribution(data, reference), "outside_snow": distribution(data, ~reference)},
                  "policies": {}, "temporal_review_candidates": top_events(data, ~reference)}
        for name, policy in POLICIES.items():
            mask = ((data["corr"] < policy["corr_max"]) &
                    (data["mad"] > policy["mad_min"]) &
                    (ratio < policy["structure_ratio_max"]) & (data["std"] > 15))
            intervals = confirmed_intervals(mask, times, step)
            outside = mask & ~reference
            report["policies"][name] = {
                "candidate_frames": int(mask.sum()),
                "snow_reference_candidate_fraction": float(mask[reference].mean()),
                "outside_reference_candidate_frames": int(outside.sum()),
                "outside_reference_candidate_times_s": times[outside].tolist(),
                "confirmed_intervals": intervals}
            print(f"{stem}: {name}: {mask.sum()} candidate frames, {len(intervals)} confirmed intervals", flush=True)
        destination = args.directory / f"noise-{stem}.json"
        destination.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        summary["files"][stem] = str(destination)
    (args.directory / "noise-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
