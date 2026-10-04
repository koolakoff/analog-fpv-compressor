"""Inspect AVI timing, decoder health, and sampled field structure.

This research tool reports container timing, not camera capture cadence.
All generated evidence is local and excluded from Git.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import time


def run(command, destination):
    """Record the exact command, output, exit status, and elapsed wall time."""
    start = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - start
    destination.with_suffix(".command.json").write_text(
        json.dumps({"argv": command, "exit_code": result.returncode, "elapsed_seconds": elapsed}, indent=2), encoding="utf-8")
    destination.write_text(result.stdout, encoding="utf-8")
    destination.with_suffix(".stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Command failed; see {destination.with_suffix('.stderr.log')}")
    return result.stdout, result.stderr, elapsed


def timing_summary(packets, stream_index, expected_step=None):
    """Summarize timestamp intervals without inferring physical dropped frames."""
    selected = [p for p in packets if p["stream_index"] == stream_index]
    timestamps = [float(p["pts_time"]) for p in selected if "pts_time" in p]
    deltas = [round(b - a, 6) for a, b in zip(timestamps, timestamps[1:])]
    duration_sum = sum(float(p.get("duration_time", 0)) for p in selected)
    last = selected[-1] if selected else {}
    gaps = [{"previous_pts_seconds": a, "next_pts_seconds": b,
             "delta_seconds": round(b - a, 6),
             "missing_timeline_slots": round((b - a) / expected_step) - 1}
            for a, b in zip(timestamps, timestamps[1:])
            if expected_step and b - a > expected_step * 1.5]
    return {"packet_count": len(selected), "timestamp_count": len(timestamps),
            "first_pts_seconds": timestamps[0] if timestamps else None,
            "last_pts_seconds": timestamps[-1] if timestamps else None,
            "last_end_seconds": float(last.get("pts_time", 0)) + float(last.get("duration_time", 0)),
            "sum_duration_seconds": duration_sum,
            "nonpositive_deltas": sum(x <= 0 for x in deltas),
            "gaps": gaps,
            "delta_histogram_seconds": dict(Counter(deltas).most_common(12)),
            "packet_size_bytes": sum(int(p.get("size", 0)) for p in selected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/timing"))
    parser.add_argument("--input", type=Path, help="Analyze one input instead of the original stadium samples")
    parser.add_argument("--sample-starts", nargs="+", type=float,
                        help="Original-time starts for 400-frame idet samples")
    args = parser.parse_args()
    if args.input and not args.sample_starts:
        parser.error("--input requires explicit --sample-starts from useful video regions")
    args.output.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(args.ffmpeg_bin / "ffmpeg.exe")
    ffprobe = str(args.ffmpeg_bin / "ffprobe.exe")
    samples = {
        "air-school-stadion-oneflight": [10, 24, 60, 150, 260],
        "air-school-stadion-with-termination": [10, 50, 150, 230, 310],
    }
    summaries = []
    targets = [(args.input, args.sample_starts)] if args.input else [
        (Path("examples") / f"{stem}.AVI", args.sample_starts or starts) for stem, starts in samples.items()]
    for source, starts in targets:
        stem = source.stem
        directory = args.output / stem
        directory.mkdir(exist_ok=True)
        print(f"Analyzing {source}", flush=True)
        raw, _, _ = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)], directory / "metadata.json")
        metadata = json.loads(raw)
        raw, _, _ = run([ffprobe, "-v", "error", "-show_packets", "-show_entries", "packet=stream_index,pts_time,dts_time,duration_time,size,flags", "-of", "json", str(source)], directory / "packets.json")
        packets = json.loads(raw)["packets"]
        stream_timing = []
        for stream in metadata["streams"]:
            numerator, denominator = map(int, stream["time_base"].split("/"))
            step = numerator / denominator if stream["codec_type"] == "video" else None
            stream_timing.append({"stream_index": stream["index"], "codec_type": stream["codec_type"],
                                  **timing_summary(packets, stream["index"], step)})
        _, errors, elapsed = run([ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], directory / "decode.txt")
        audio_timing = None
        audio_streams = [s for s in metadata["streams"] if s["codec_type"] == "audio"]
        if audio_streams:
            raw, _, _ = run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_frames",
                             "-show_entries", "frame=best_effort_timestamp_time,nb_samples",
                             "-of", "json", str(source)], directory / "audio-frames.json")
            frames = json.loads(raw)["frames"]
            rate = int(audio_streams[0]["sample_rate"])
            sample_count = sum(int(f["nb_samples"]) for f in frames)
            audio_timing = {"decoded_frames": len(frames), "decoded_samples": sample_count,
                            "sample_rate": rate, "sample_duration_seconds": sample_count / rate,
                            "first_pts_seconds": float(frames[0]["best_effort_timestamp_time"]) if frames else None,
                            "last_end_seconds": float(frames[-1]["best_effort_timestamp_time"]) + int(frames[-1]["nb_samples"]) / rate if frames else None}
        idet = []
        for start in starts:
            sample_name = f"{start:g}".replace(".", "-")
            _, diagnostic, sample_elapsed = run([ffmpeg, "-hide_banner", "-nostats", "-ss", str(start), "-i", str(source), "-map", "0:v:0", "-frames:v", "400", "-an", "-vf", "idet", "-f", "null", "-"], directory / f"idet-{sample_name}.txt")
            counts = re.findall(r"(Single|Multi) frame detection: TFF:\s*(\d+) BFF:\s*(\d+) Progressive:\s*(\d+) Undetermined:\s*(\d+)", diagnostic)
            idet.append({"start_seconds": start, "requested_frames": 400, "elapsed_seconds": sample_elapsed,
                         "counts": {kind: {"tff": int(tff), "bff": int(bff), "progressive": int(prog), "undetermined": int(und)} for kind, tff, bff, prog, und in counts}})
        # Exact decoded-frame hashes catch identical frames only; noisy repeats can differ.
        raw, _, _ = run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(source), "-map", "0:v:0", "-an", "-f", "framemd5", "-"], directory / "frames.framemd5")
        hashes = [line.rsplit(",", 1)[-1].strip() for line in raw.splitlines() if line and not line.startswith("#")]
        identical = [i for i in range(1, len(hashes)) if hashes[i] == hashes[i - 1]]
        summary = {"input": str(source), "metadata": metadata, "stream_timing": stream_timing,
                   "audio_decode_timing": audio_timing,
                   "full_decode": {"elapsed_seconds": elapsed, "error_log_empty": not errors.strip()},
                   "idet_samples": idet, "decoded_frames": len(hashes),
                   "exact_adjacent_duplicate_count": len(identical), "exact_adjacent_duplicate_indices": identical}
        (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        summaries.append(summary)
        print(f"Completed {stem}: {len(hashes)} frames, {len(identical)} exact adjacent duplicates", flush=True)
    (args.output / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
