"""Inspect a new DVR sample with native timestamps and the production snow policy.

Research artifacts are explicit and stay in an ignored directory. This does not
change the application's default single-session logging policy.
"""

import argparse
import hashlib
from pathlib import Path

import numpy as np

import run_research as research
from analog_fpv_compressor.detection import snow_intervals, true_runs
from analog_fpv_compressor.runtime import discover_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=research.ROOT / "examples/Frantisek.AVI")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--review-boundaries", action="store_true", help="Add native-frame boundary sheets to an existing inspection")
    args = parser.parse_args()
    research.RESULTS = args.directory.resolve()
    if args.review_boundaries:
        tools = discover_tools()
        data = np.genfromtxt(research.RESULTS / (args.input.stem + ".metrics.csv"), delimiter=",", names=True)
        relative = args.input.resolve().relative_to(research.ROOT).as_posix()
        for name, instant in (("snow-start-native", 85.92), ("block-screen-native", 112.44),
                              ("signal-return-native", 113.20), ("single-candidate-native", 307.39),
                              ("tail-start-native", 338.88)):
            research.sheet({"id": name, "input": relative,
                            "first_frame": max(0, int(np.searchsorted(data["avi_time_s"], instant)) - 5)}, tools["ffmpeg"])
        return
    research.RESULTS.mkdir(parents=True, exist_ok=False)
    source = args.input.resolve()
    tools = discover_tools()
    research.inspect(source, tools["ffmpeg"], tools["ffprobe"])
    data = np.genfromtxt(research.RESULTS / (source.stem + ".metrics.csv"), delimiter=",", names=True)
    times = data["avi_time_s"]
    rows = np.column_stack([data[key] for key in ("mean", "std", "coarse_std", "mad", "corr")])
    step = float(np.median(np.diff(times)))
    cuts, policy = snow_intervals(rows, times, step)
    ratio = data["coarse_std"] / np.maximum(data["std"], 1e-6)
    candidates = (data["corr"] < .12) & (data["mad"] > 25) & (ratio < .55) & (data["std"] > 15)
    reference = ((times >= 87) & (times < 113)) | (times >= 339)
    mean_change = np.abs(np.diff(data["mean"], prepend=data["mean"][0]))
    events = []
    for index in np.argsort(data["mad"] + mean_change)[::-1]:
        instant = float(times[index])
        if reference[index] or any(abs(instant - event["time_s"]) < 1.5 for event in events):
            continue
        events.append({"frame": int(index), "time_s": instant, "mean_change": float(mean_change[index]),
                       **{key: float(data[key][index]) for key in ("mean", "std", "coarse_std", "mad", "corr")}})
        if len(events) == 16:
            break
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    summary = {"source": str(source), "bytes": source.stat().st_size, "sha256": digest,
               "decoded_frames": len(times), "frame_step_s": step,
               "timestamp_gap_count": int((np.diff(times) > step * 1.5).sum()),
               "approximate_user_snow_intervals_s": [[87, 113], [339, float(times[-1] + step)]],
               "production_cuts_s": cuts, "production_policy": policy,
               "reference_candidate_fraction": float(candidates[reference].mean()),
               "outside_reference_candidates": times[candidates & ~reference].tolist(),
               "candidate_runs": [[float(times[a]), float(times[b - 1] + step), int(b - a)]
                                  for a, b in true_runs(candidates)], "review_events": events}
    research.save_json(research.RESULTS / "inspection.json", summary)
    relative = source.relative_to(research.ROOT).as_posix()
    for name, start, fps, tile in (("overview", 0, "1/15", "5x4"),
                                   ("battery-boundary", 83, 1, "6x6"),
                                   ("tail-boundary", 335, 2, "5x4")):
        research.sheet({"id": name, "input": relative, "start": start, "fps": fps, "tile": tile}, tools["ffmpeg"])
    for number, event in enumerate(events[:10], 1):
        research.sheet({"id": f"event-{number:02d}", "input": relative,
                        "first_frame": max(0, event["frame"] - 5)}, tools["ffmpeg"])
    print(f"Production cuts: {cuts}; review events: {len(events)}", flush=True)


if __name__ == "__main__":
    main()
