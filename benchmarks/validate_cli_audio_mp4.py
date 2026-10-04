"""Validate real AAC/MP4 preservation of unusual-rate home DVR audio."""

import argparse
from datetime import datetime, timezone
import json
from log_reports import read_report
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    args = parser.parse_args()
    destination = ROOT / "outputs/cli-audio-mp4" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    ffmpeg, ffprobe = str(args.ffmpeg_bin / "ffmpeg.exe"), str(args.ffmpeg_bin / "ffprobe.exe")
    records = {}

    def run(name, command):
        start = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, cwd=ROOT)
        record = {"command": command, "exit_code": completed.returncode, "elapsed_seconds": time.perf_counter() - start}
        (destination / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (destination / f"{name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        records[name] = record
        (destination / "commands.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"{name}: exit={completed.returncode}, elapsed={record['elapsed_seconds']:.2f}s", flush=True)
        return completed

    source = ROOT / "examples/home-other-helmet.AVI"
    clip, output = destination / "gray-source-with-audio.AVI", destination / "gray-keep.mp4"
    completed = run("extract", [ffmpeg, "-hide_banner", "-nostdin", "-n", "-i", str(source), "-ss", "120", "-t", "4",
                                "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(clip)])
    if completed.returncode:
        raise RuntimeError("Audio clip extraction failed")
    completed = run("probe-source", [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(clip)])
    source_probe = json.loads(completed.stdout)
    (destination / "source-probe.json").write_text(json.dumps(source_probe, indent=2), encoding="utf-8")
    hashes = []
    for name, path, filters in [("original", source, "trim=start=120:end=124,setpts=PTS-STARTPTS"), ("clip", clip, None)]:
        command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-i", str(path), "-map", "0:v:0", "-an"]
        if filters:
            command += ["-vf", filters]
        completed = run(f"hash-{name}", command + ["-fps_mode", "passthrough", "-f", "framemd5", "-"])
        hashes.append([line.rsplit(",", 1)[-1].strip() for line in completed.stdout.splitlines() if line and not line.startswith("#")])
    completed = run("cli-aac", [sys.executable, "-m", "analog_fpv_compressor", "-i", str(clip), "-o", str(output),
                                "--ffmpeg-dir", str(args.ffmpeg_bin), "--audio", "keep", "--cut-no-signal", "off",
                                "--deinterlace", "off", "--events-jsonl", "--log-file", str(output) + ".session.log"])
    summary = {"directory": str(destination), "source_probe": source_probe,
               "source_video_hashes_match": hashes[0] == hashes[1], "source_video_frames": len(hashes[1]),
               "cli_exit_code": completed.returncode, "output_exists": output.exists(), "report": None}
    report_path = Path(str(output) + ".report.json")
    summary["report"] = read_report(report_path)
    if output.exists():
        completed_probe = run("probe-output", [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)])
        summary["output_probe"] = json.loads(completed_probe.stdout)
    audio_stream = next((s for s in summary.get("output_probe", {}).get("streams", []) if s["codec_type"] == "audio"), None)
    audio_validation = summary["report"]["validation"].get("audio") if summary["report"] else None
    summary["checks"] = {
        "aac_audio_present": bool(audio_stream and audio_stream["codec_name"] == "aac"),
        "audio_clock_validated": bool(audio_validation and audio_validation["maximum_sample_clock_error_seconds"] <= .001),
        "audio_count_within_existing_tolerance": bool(audio_validation and abs(audio_validation["decoded_samples"] - audio_validation["target_samples"]) <= audio_validation["padding_tolerance_samples"]),
    }
    summary["passed"] = bool(all(summary["checks"].values()) and summary["source_video_hashes_match"] and summary["source_video_frames"] == 120
                              and summary["cli_exit_code"] == 0 and summary["output_exists"] and summary["report"]
                              and summary["report"]["validation"]["decoded_frames"] == 120
                              and summary["report"]["validation"]["full_decode_passed"])
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Audio/MP4 validation passed: {summary['passed']}; evidence: {destination}", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
