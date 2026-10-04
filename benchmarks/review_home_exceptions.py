"""Extract every rejected snow frame inside the proposed home snow interval."""

import argparse
import json
from pathlib import Path

import numpy as np

from analyze_noise import POLICIES
from run_research import RESULTS, run_logged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    args = parser.parse_args()
    data = np.genfromtxt(RESULTS / "home-other-helmet.metrics.csv", names=True, delimiter=",")
    policy = POLICIES["conservative"]
    snow = ((data["corr"] < policy["corr_max"]) & (data["mad"] > policy["mad_min"]) &
            (data["coarse_std"] / np.maximum(data["std"], 1e-6) < policy["structure_ratio_max"]) & (data["std"] > 15))
    rejected = data[(data["avi_time_s"] >= 61.35) & (data["avi_time_s"] < 87.816667) & ~snow]
    rows = [{name: float(row[name]) for name in data.dtype.names} for row in rejected]
    (RESULTS / "home-snow-rejected-frames.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    indices = [int(row["frame"]) for row in rejected]
    expression = "+".join(f"eq(n,{index})" for index in indices)
    run_logged("home-snow-all-exceptions", [str(args.ffmpeg_bin / "ffmpeg.exe"), "-hide_banner", "-nostdin", "-n",
                 "-threads", "2", "-i", "examples/home-other-helmet.AVI", "-vf",
                 f"select='{expression}',scale=360:240,tile=6x4", "-frames:v", "1", "-update", "1",
                 str(RESULTS / "home-snow-all-exceptions.jpg")])
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
