"""Measure native-resolution variation in visually flat blue DVR frames."""

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np

from run_research import RESULTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    args = parser.parse_args()
    ffmpeg = str(args.ffmpeg_bin / "ffmpeg.exe")
    report = {"warning": "Independent distribution measurements, not frame-aligned reconstruction error.", "sources": {}}
    for name, source, trim in [
        ("original", "examples/home-other-helmet.AVI", "trim=start=89.7:end=94.5,"),
        ("av1_medium_c48", str(RESULTS / "home-blue-only-c48.mkv"), "")]:
        command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-i", source,
                   "-an", "-vf", trim + "format=rgb24", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
        decoded = np.frombuffer(subprocess.check_output(command), np.uint8).reshape(-1, 480, 720, 3)
        measured = {"frames": len(decoded), "command": command, "regions": {}}
        for region_name, region in [("full_frame", decoded), ("central_80_percent", decoded[:, 48:432, 72:648])]:
            region = region.astype(np.float32)
            average = region.mean(axis=(1, 2), keepdims=True)
            centered = region - average
            delta = np.diff(centered, axis=0)
            measured["regions"][region_name] = {
                "channels": "RGB",
                "mean_rgb": average.mean(axis=0).ravel().tolist(),
                "median_per_frame_spatial_std_rgb": np.median(region.std(axis=(1, 2)), axis=0).tolist(),
                "brightness_normalized_temporal_mad_rgb": np.abs(delta).mean(axis=(0, 1, 2)).tolist(),
                "brightness_normalized_temporal_rms_rgb": np.sqrt((delta * delta).mean(axis=(0, 1, 2))).tolist(),
                "per_frame_rgb_mean_std": average.reshape(len(region), 3).std(axis=0).tolist(),
                "temporal_changed_pixel_fraction": float((np.abs(np.diff(region, axis=0)).max(axis=-1) > 2).mean())}
            if region_name == "central_80_percent":
                per_frame_std = region.std(axis=(1, 2)).max(axis=1)
                flat = per_frame_std < 1
                pairs = flat[:-1] & flat[1:]
                measured["flat_center_frames"] = int(flat.sum())
                measured["nonflat_center_frame_ordinals"] = np.flatnonzero(~flat).tolist()
                measured["consecutive_flat_pairs"] = int(pairs.sum())
                measured["flat_pairs_normalized_temporal_mad_rgb"] = np.abs(delta[pairs]).mean(axis=(0, 1, 2)).tolist() if pairs.any() else None
        report["sources"][name] = measured
    (RESULTS / "home-blue-native-analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
