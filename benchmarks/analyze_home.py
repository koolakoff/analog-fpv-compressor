"""Research snow, static blue screens, and a useful stationary scene."""

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np

from analyze_noise import POLICIES, confirmed_intervals, runs
from run_research import RESULTS, run_logged


def spans(mask, times, step):
    """Convert sampled index runs to timestamp spans."""
    return [[float(times[a]), float(times[b]) if b < len(times) else float(times[-1] + step)]
            for a, b in runs(mask)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    args = parser.parse_args()
    ffmpeg = str(args.ffmpeg_bin / "ffmpeg.exe")
    source = "examples/home-other-helmet.AVI"
    data = np.genfromtxt(RESULTS / "home-other-helmet.metrics.csv", names=True, delimiter=",")
    times = data["avi_time_s"]
    step = float(np.median(np.diff(times)))
    report = {"status": "research_only", "median_step_s": step, "policies": {}}
    ratio = data["coarse_std"] / np.maximum(data["std"], 1e-6)
    for name, policy in POLICIES.items():
        mask = ((data["corr"] < policy["corr_max"]) & (data["mad"] > policy["mad_min"]) &
                (ratio < policy["structure_ratio_max"]) & (data["std"] > 15))
        report["policies"][name] = {"candidate_frames": int(mask.sum()),
                                   "candidate_frames_in_gray_box_120_124": int((mask & (times >= 120) & (times < 124)).sum()),
                                   "candidate_frames_outside_61_95": int((mask & ((times < 61) | (times >= 95))).sum()),
                                   "confirmed_intervals": confirmed_intervals(mask, times, step)}
    command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-i", source,
               "-an", "-vf", "scale=160:120:flags=bilinear,format=rgb24", "-fps_mode", "passthrough",
               "-f", "rawvideo", "pipe:1"]
    pixels = subprocess.check_output(command)
    frames = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, 120, 160, 3)
    if len(frames) != len(times):
        raise RuntimeError("Color decode and timestamp counts differ")
    center = frames[:, 12:108, 16:144, :].astype(np.float32)
    blue_fraction = ((center[..., 2] > center[..., 0] + 50) &
                     (center[..., 2] > center[..., 1] + 50) & (center[..., 2] > 100)).mean(axis=(1, 2))
    spatial_std = center.std(axis=(1, 2)).max(axis=1)
    blue = (blue_fraction > .98) & (spatial_std < 10)
    report["blue_hypothesis"] = {"rule": "central >98% strongly blue pixels and max spatial RGB std <10",
                                 "candidate_frames": int(blue.sum()),
                                 "candidate_frames_outside_61_95": int((blue & ((times < 61) | (times >= 95))).sum()),
                                 "candidate_spans": spans(blue, times, step),
                                 "confirmed_intervals": confirmed_intervals(blue, times, step)}
    report["windows"] = {}
    for name, start, end in [("mixed_no_signal", 61, 90), ("stationary_gray_box", 120, 124)]:
        mask = (times >= start) & (times < end)
        report["windows"][name] = {
            "interval_s": [start, end], "frames": int(mask.sum()),
            "metrics_quantiles_p10_p50_p90": {k: np.quantile(data[k][mask], [.1, .5, .9]).tolist()
                                              for k in ["mean", "std", "coarse_std", "mad", "corr"]},
            "blue_fraction_quantiles": np.quantile(blue_fraction[mask], [.1, .5, .9]).tolist(),
            "blue_candidate_frames": int((mask & blue).sum())}
    # Measure native-pixel temporal variation in the useful stationary scene.
    command_native = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-i", source,
                      "-vf", "trim=start=120:end=124,format=gray", "-an", "-fps_mode", "passthrough",
                      "-f", "rawvideo", "pipe:1"]
    metadata = json.loads((RESULTS / "home-other-helmet.probe.json").read_text(encoding="utf-8"))
    stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    width, height = stream["width"], stream["height"]
    native = np.frombuffer(subprocess.check_output(command_native), np.uint8).reshape(-1, height, width).astype(np.float32)
    regions = {"central_scene": native[:, height // 10:9 * height // 10, width // 10:9 * width // 10],
               "center_patch": native[:, height // 2 - 60:height // 2 + 60, width // 2 - 60:width // 2 + 60]}
    report["stationary_native_temporal"] = {}
    for name, region in regions.items():
        residual = region - region.mean(axis=(1, 2), keepdims=True)
        delta = np.diff(residual, axis=0)
        report["stationary_native_temporal"][name] = {
            "frames": len(region), "brightness_normalized_temporal_mad": float(np.abs(delta).mean()),
            "brightness_normalized_temporal_rms": float(np.sqrt((delta * delta).mean())),
            "per_frame_temporal_mad_p10_p50_p90": np.quantile(np.abs(delta).mean(axis=(1, 2)), [.1, .5, .9]).tolist(),
            "warning": "Includes any motion, JPEG changes, camera noise, and reception noise; not an isolated noise estimate."}
    report["commands"] = [command, command_native]
    (RESULTS / "home-noise-analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, start, duration, rate, layout in [
        ("home-nosignal-overview", 60, 32, 1, "8x4"),
        ("home-graybox-overview", 120, 4, 3, "4x3")]:
        if (RESULTS / f"{name}.run.json").exists():
            continue
        run_logged(name, [ffmpeg, "-hide_banner", "-nostdin", "-n", "-threads", "2", "-i", source,
                          "-vf", f"trim=start={start}:end={start + duration},setpts=PTS-STARTPTS,fps={rate},scale=240:180,tile={layout}",
                          "-frames:v", "1", "-update", "1", str(RESULTS / f"{name}.jpg")])
    print(json.dumps({"policies": report["policies"], "blue_hypothesis": report["blue_hypothesis"],
                      "windows": report["windows"], "stationary_native_temporal": report["stationary_native_temporal"]}, indent=2))


if __name__ == "__main__":
    main()
