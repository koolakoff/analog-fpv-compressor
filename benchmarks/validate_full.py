"""Validate completed full-file research outputs against source frame timestamps.

Counts must match exactly. Internal timestamp deltas allow 1.1 ms for Matroska
rounding. Concatenated segment origins are intentionally not compared with
absolute source time; duration allows one source-frame interval per segment.
Video-only concat can estimate the final frame's duration from sparse segment
cadence, so summed requested interval lengths are not an exact output duration.
"""

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks/results/initial"


def probe(path, ffprobe):
    """Decode frames with bounded decoder threads and preserve diagnostic evidence."""
    command = [ffprobe, "-v", "error", "-threads", "2", "-show_streams", "-show_format",
               "-show_frames", "-show_entries", "frame=media_type,best_effort_timestamp_time",
               "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (RESULTS / f"{path.stem}.validation-probe.json").write_text(result.stdout, encoding="utf-8")
    (RESULTS / f"{path.stem}.validation-probe.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode or result.stderr.strip():
        raise RuntimeError(f"Validation decode failed for {path.name}; inspect validation-probe.log")
    return json.loads(result.stdout)


def validate(record, ffprobe):
    """Check decoded counts and timing independently of encoder log counters."""
    spec = record["spec"]
    output = RESULTS / f"{spec['id']}.mkv"
    source = Path(spec["input"])
    source_times = json.loads((RESULTS / f"{source.stem}.frame-times.json").read_text(encoding="utf-8"))
    source_probe = json.loads((RESULTS / f"{source.stem}.probe.json").read_text(encoding="utf-8"))
    source_video = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
    numerator, denominator = map(int, source_video["time_base"].split("/"))
    step = numerator / denominator
    intervals = spec.get("keep", [[0, float(source_video["duration"]) + step]])
    segments = [[t for t in source_times if begin <= t < end] for begin, end in intervals]
    data = probe(output, ffprobe)
    output_times = [float(f["best_effort_timestamp_time"]) for f in data["frames"] if f["media_type"] == "video"]
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    checks = {"exact_frame_count": len(output_times) == sum(map(len, segments)),
              "monotonic_pts": all(b > a for a, b in zip(output_times, output_times[1:])),
              "dimensions": [video["width"], video["height"]] == spec.get("scale", [640, 480]),
              "limited_range": video.get("color_range") == "tv",
              "no_audio": all(s["codec_type"] != "audio" for s in data["streams"])}
    offset = 0
    reports = []
    for interval, source_segment in zip(intervals, segments):
        output_segment = output_times[offset:offset + len(source_segment)]
        source_deltas = [b - a for a, b in zip(source_segment, source_segment[1:])]
        output_deltas = [b - a for a, b in zip(output_segment, output_segment[1:])]
        errors = [abs(a - b) for a, b in zip(source_deltas, output_deltas)]
        aligned = len(source_deltas) == len(output_deltas)
        reports.append({"source_interval_seconds": interval, "expected_frames": len(source_segment),
                        "actual_frames": len(output_segment),
                        "internal_gap_count": sum(d > 1.5 * step for d in source_deltas),
                        "maximum_internal_delta_error_seconds": max(errors, default=0),
                        "internal_deltas_preserved": aligned and max(errors, default=0) <= .0011,
                        "source_first_seconds": source_segment[0] if source_segment else None,
                        "source_last_seconds": source_segment[-1] if source_segment else None,
                        "output_first_seconds": output_segment[0] if output_segment else None,
                        "output_last_seconds": output_segment[-1] if output_segment else None})
        offset += len(source_segment)
    checks["internal_deltas_preserved"] = all(r["internal_deltas_preserved"] for r in reports)
    expected_duration = sum(s[-1] - s[0] + step for s in segments if s)
    actual_duration = float(data["format"]["duration"])
    duration_tolerance = step * len(segments) + .002
    checks["duration_within_frame_tolerance"] = abs(actual_duration - expected_duration) <= duration_tolerance
    decode_record_path = RESULTS / f"{spec['id']}.decode.run.json"
    decode_record = json.loads(decode_record_path.read_text(encoding="utf-8")) if decode_record_path.exists() else {}
    checks["independent_full_decode_completed"] = decode_record.get("exit_code") == 0
    return {"id": spec["id"], "passed": all(checks.values()), "checks": checks,
            "expected_frames": sum(map(len, segments)), "actual_frames": len(output_times),
            "expected_duration_seconds": expected_duration, "actual_duration_seconds": actual_duration,
            "duration_tolerance_seconds": duration_tolerance, "segments": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--manifest", nargs="+", type=Path,
                        default=[ROOT / "benchmarks/full-uncut.json", ROOT / "benchmarks/full-cut-v2.json"])
    args = parser.parse_args()
    results, pending = [], []
    expected_ids = {spec["id"] for manifest in args.manifest
                    for spec in json.loads(manifest.read_text(encoding="utf-8"))}
    for run_id in sorted(expected_ids):
        path = RESULTS / f"{run_id}.run.json"
        if not path.exists():
            pending.append(run_id)
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("exit_code") != 0 or "spec" not in record:
            pending.append(path.stem)
            continue
        result = validate(record, args.ffprobe)
        results.append(result)
        print(f"{result['id']}: {'PASS' if result['passed'] else 'FAIL'}, {result['actual_frames']} frames", flush=True)
    summary = {"results": results, "pending": pending,
               "all_completed_passed": bool(results) and all(r["passed"] for r in results),
               "all_expected_completed": not pending,
               "timing_notes": [
                   "Frame counts and internal gaps are checked against source timestamps exactly, within Matroska rounding.",
                   "Segment origins shift during concat; absolute source timestamps cannot be compared directly.",
                   "Video-only concat may estimate the final frame duration from sparse segment cadence; summed requested interval lengths are not an exact output duration."]}
    (RESULTS / "full-validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Validated {len(results)} files; {len(pending)} pending", flush=True)
    if any(not r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
