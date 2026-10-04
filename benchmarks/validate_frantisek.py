"""Independently check full-run frames against the inspected native source times."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess

from analog_fpv_compressor.runtime import discover_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    source_times = json.loads((directory / "inspection/Frantisek.frame-times.json").read_text())
    ffprobe = discover_tools()["ffprobe"]
    runs = []
    for name in ("default", "scale480"):
        events = [json.loads(line) for line in (directory / (name + "-session.log")).read_text(encoding="utf-8").splitlines()]
        if events[-1]["code"] != "session.finished" or events[-1]["data"]["failed"]:
            raise RuntimeError(f"Run is not complete: {name}")
        reports = [event["data"]["report"] for event in events if event["code"] == "processing_completed"]
        parts = []
        for report in reports:
            path = Path(report["output_path"])
            command = [ffprobe, "-v", "error", "-threads", "2", "-select_streams", "v:0", "-show_frames",
                       "-show_streams", "-show_format", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)]
            result = subprocess.run(command, capture_output=True, check=True)
            if result.stderr.strip():
                raise RuntimeError(f"Decode diagnostics: {path}")
            data = json.loads(result.stdout)
            actual = [float(frame["best_effort_timestamp_time"]) for frame in data["frames"]]
            plan = report["plan"]
            expected = [stamp - start for start, end in plan["keep_intervals"] for stamp in source_times if start <= stamp < end]
            errors = [abs(a - b) for a, b in zip(actual, expected)]
            if len(actual) != len(expected) or max(errors, default=0) > .0011:
                raise RuntimeError(f"Source timestamp mapping failed: {path}")
            video = data["streams"][0]
            selected = plan["selected"]
            if [video["width"], video["height"]] != [selected["width"], selected["height"]] or video.get("color_range") != "tv":
                raise RuntimeError(f"Output geometry/range failed: {path}")
            parts.append({"path": str(path), "bytes": path.stat().st_size, "frames": len(actual),
                          "maximum_timestamp_error_s": max(errors, default=0), "decode_passed": True,
                          "dimensions": [video["width"], video["height"]],
                          "duration_s": float(data["format"]["duration"]), "keep_intervals": plan["keep_intervals"]})
        if len(parts) != 2:
            raise RuntimeError(f"Unexpected flight count: {name}")
        elapsed = (datetime.fromisoformat(events[-1]["timestamp"]) - datetime.fromisoformat(events[0]["timestamp"])).total_seconds()
        runs.append({"name": name, "parts": parts, "bytes": sum(part["bytes"] for part in parts),
                     "frames": sum(part["frames"] for part in parts), "session_wall_seconds": elapsed,
                     "cuts": reports[0]["plan"]["removed_intervals"], "all_checks_passed": True})
    (directory / "full-validation.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    print(json.dumps(runs, indent=2))


if __name__ == "__main__":
    main()
