"""Exercise real CLI options and safety checks on short local MJPEG samples."""

from datetime import datetime, timezone
import argparse
import hashlib
import json
from log_reports import read_report
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def verify_existing(summary_path, ffprobe=None):
    """Check persisted reports without rerunning any video encoding."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for name, record in summary["cases"].items():
        if "report" not in record:
            continue
        plan = record["report"]["plan"]
        selected = plan["selected"]
        checks = {}
        if name != "gray-default":
            checks["disabled_analysis_skipped"] = (plan["analysis"]["metadata"]["snow_analysis"]["enabled"] is False
                                                   and plan["analysis"]["interlace"]["decision"] == "not_run")
        expected_denoise = {"av1-medium": "medium", "av1-strong": "strong", "av1-off": "off", "analyze-off": "off"}
        if name in expected_denoise:
            checks["denoise_selected"] = selected["denoise_level"] == expected_denoise[name]
        if name == "av1-scale480":
            checks["scale_selected"] = (selected["width"], selected["height"]) == (480, 360)
        if name == "hevc-medium":
            checks["hevc_selected"] = selected["encoder"] == "libx265" and selected["preset"] == "medium" and selected["crf"] == 32
        if name == "av1-bitrate500k":
            checks["bitrate_selected"] = selected["bitrate"] == "500k" and selected["crf"] is None
        if name == "gray-keep-noaudio":
            checks["noaudio_keep_fallback"] = selected["audio"] == "remove" and "No audio" in plan["reasons"]["audio"]
        if name == "gray-default":
            checks["defaults_selected"] = selected["crf"] == 48 and selected["preset"] == 6 and selected["denoise_level"] == "medium" and selected["audio"] == "remove"
        if "validation" in record["report"]:
            validation = record["report"]["validation"]
            expected = 599 if name.startswith(("av1-", "hevc-")) else 120
            checks["decoded_frame_count"] = validation["decoded_frames"] == expected
            checks["full_decode_passed"] = validation["full_decode_passed"] is True
        record["checks"] = checks
        record["passed"] = record["passed"] and all(checks.values())
    references = {"av1-medium": "sun-medium-c48-p6", "av1-strong": "sun-strong-c48", "av1-off": "sun-off-c48-p6",
                  "hevc-medium": "sun-hevc-c32", "av1-scale480": "sun-scale480-c48"}
    for name, reference_name in references.items():
        path = ROOT / "benchmarks/results/initial" / f"{reference_name}.run.json"
        if path.exists():
            reference = json.loads(path.read_text(encoding="utf-8"))
            summary["research_size_reference"][name] = {"research_record": str(path), "research_bytes": reference["bytes"],
                                                       "cli_bytes": summary["cases"][name]["bytes"],
                                                       "size_matches": reference["bytes"] == summary["cases"][name]["bytes"]}
    if ffprobe and summary["cases"]["hevc-medium"].get("output_exists"):
        payloads = []
        for label, source in [("research", ROOT / "benchmarks/results/initial/sun-hevc-c32.mkv"),
                              ("cli", summary_path.parent / "hevc-medium.mkv")]:
            command = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_packets", "-show_data_hash", "SHA256",
                       "-show_entries", "packet=size,data_hash", "-of", "json", str(source)]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            evidence = {"command": command, "exit_code": completed.returncode, "stderr": completed.stderr,
                        "probe": json.loads(completed.stdout) if completed.returncode == 0 else None}
            (summary_path.parent / f"hevc-{label}-packet-hashes.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            payloads.append(evidence["probe"]["packets"] if evidence["probe"] else None)
        summary["research_size_reference"]["hevc-medium"]["video_packet_hashes_match"] = payloads[0] is not None and payloads[0] == payloads[1]
    summary["passed"] = all(record["passed"] for record in summary["cases"].values())
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Persisted case checks passed: {summary['passed']}", flush=True)
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-existing", type=Path, help="Validate persisted results without re-encoding")
    args = parser.parse_args()
    if args.verify_existing:
        return verify_existing(args.verify_existing, str(args.ffmpeg_bin / "ffprobe.exe"))
    destination = args.output or ROOT / "outputs" / "cli-edges" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    ffmpeg, ffprobe = str(args.ffmpeg_bin / "ffmpeg.exe"), str(args.ffmpeg_bin / "ffprobe.exe")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    summary = {"created": datetime.now(timezone.utc).isoformat(), "directory": str(destination),
               "timing_warning": "Concurrent experiments may affect wall time; exact encoded bytes are not promised.",
               "clips": {}, "cases": {}}

    def run(name, command):
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", env=environment, cwd=ROOT)
        record = {"command": command, "exit_code": completed.returncode, "wall_seconds": time.perf_counter() - started}
        (destination / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (destination / f"{name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        (destination / f"{name}.command.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"{name}: exit={completed.returncode}, elapsed={record['wall_seconds']:.2f}s", flush=True)
        return record, completed

    def hashes(name, source, filters=None):
        command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-i", str(source), "-map", "0:v:0", "-an"]
        if filters:
            command += ["-vf", filters]
        command += ["-fps_mode", "passthrough", "-f", "framemd5", "-"]
        record, completed = run(name, command)
        if completed.returncode:
            raise RuntimeError(f"Frame hash extraction failed: {name}")
        return [line.rsplit(",", 1)[-1].strip() for line in completed.stdout.splitlines() if line and not line.startswith("#")]

    for name, source, start, end in [("sun", ROOT / "examples/air-school-stadion-oneflight.AVI", 20, 40),
                                      ("gray", ROOT / "examples/home-other-helmet.AVI", 120, 124)]:
        clip = destination / f"{name}-source.mkv"
        record, completed = run(f"extract-{name}", [ffmpeg, "-hide_banner", "-nostdin", "-n", "-i", str(source),
                                                    "-ss", str(start), "-to", str(end), "-map", "0:v:0", "-an", "-c:v", "copy", str(clip)])
        if completed.returncode:
            raise RuntimeError("Source clip extraction failed")
        original = hashes(f"hash-source-{name}", source, f"trim=start={start}:end={end},setpts=PTS-STARTPTS")
        copied = hashes(f"hash-clip-{name}", clip)
        summary["clips"][name] = {"path": str(clip), "original_source": str(source), "source_interval": [start, end],
                                  "source_decoded_frames": len(original), "clip_decoded_frames": len(copied),
                                  "decoded_pixel_hashes_match_in_order": original == copied, "audio_removed": True}
        if original != copied:
            raise RuntimeError(f"Remuxed {name} does not match selected original frames")

    base = ["--ffmpeg-dir", str(args.ffmpeg_bin), "--events-jsonl", "--threads", "4"]
    explicit = ["--deinterlace", "off", "--cut-no-signal", "off", "--scale", "original", "--preset", "6"]
    variants = [
        ("av1-medium", "sun", ["--crf", "48", "--denoise", "medium"], explicit),
        ("av1-strong", "sun", ["--crf", "48", "--denoise", "strong"], explicit),
        ("av1-off", "sun", ["--crf", "48", "--denoise", "off"], explicit),
        ("av1-scale480", "sun", ["--crf", "48", "--denoise", "medium", "--scale", "480x360"], explicit),
        ("hevc-medium", "sun", ["--codec", "hevc", "--crf", "32", "--denoise", "medium", "--preset", "medium"], explicit),
        ("av1-bitrate500k", "sun", ["--bitrate", "500k", "--denoise", "medium"], explicit),
        ("gray-default", "gray", [], []),
        ("gray-keep-noaudio", "gray", ["--audio", "keep"], explicit),
        ("analyze-off", "gray", ["--analyze-only", "--denoise", "off"], explicit),
    ]
    for name, clip_name, options, mode in variants:
        output = destination / f"{name}.mkv"
        record, completed = run(name, [sys.executable, "-m", "analog_fpv_compressor", "-i", summary["clips"][clip_name]["path"],
                                      "-o", str(output), "--no-split-flights", "--log-file", str(output) + ".session.log", *base, *mode, *options])
        report_path = Path(str(output) + ".report.json")
        record["output_exists"] = output.exists()
        record["events"] = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        measured = read_report(report_path)
        if measured is not None:
            record["report"] = measured
        if output.exists():
            record["bytes"] = output.stat().st_size
        record["checks"] = {}
        if "report" in record:
            plan = record["report"]["plan"]
            selected = plan["selected"]
            if mode:
                record["checks"]["disabled_analysis_skipped"] = (
                    plan["analysis"]["metadata"]["snow_analysis"]["enabled"] is False
                    and plan["analysis"]["interlace"]["decision"] == "not_run")
            if "--denoise" in options:
                requested = options[options.index("--denoise") + 1]
                record["checks"]["denoise_selected"] = selected["denoise_level"] == requested
            if name == "av1-scale480":
                record["checks"]["scale_selected"] = (selected["width"], selected["height"]) == (480, 360)
            if name == "hevc-medium":
                record["checks"]["hevc_selected"] = selected["encoder"] == "libx265" and selected["preset"] == "medium" and selected["crf"] == 32
            if name == "av1-bitrate500k":
                record["checks"]["bitrate_selected"] = selected["bitrate"] == "500k" and selected["crf"] is None
            if name == "gray-keep-noaudio":
                record["checks"]["noaudio_keep_fallback"] = selected["audio"] == "remove" and "No audio" in plan["reasons"]["audio"]
            if name == "gray-default":
                record["checks"]["defaults_selected"] = selected["crf"] == 48 and selected["preset"] == 6 and selected["denoise_level"] == "medium" and selected["audio"] == "remove"
        record["expected_exit_code"] = 0
        record["passed"] = completed.returncode == 0 and measured is not None and all(record["checks"].values()) and (not output.exists() if "--analyze-only" in options else output.exists())
        summary["cases"][name] = record
        (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    protected = destination / "protected-output.mkv"
    protected.write_bytes(b"DO NOT OVERWRITE THIS FIXTURE")
    fingerprint = hashlib.sha256(protected.read_bytes()).hexdigest()
    for name, source, output, options in [
        ("invalid-conflict", summary["clips"]["gray"]["path"], destination / "conflict.mkv", ["--crf", "48", "--bitrate", "500k"]),
        ("invalid-input", str(destination / "missing.AVI"), destination / "missing.mkv", []),
        ("existing-output", summary["clips"]["gray"]["path"], protected, []),
        ("invalid-min-duration", summary["clips"]["gray"]["path"], destination / "invalid-duration.mkv", ["--no-signal-min-duration", "nan"]),
    ]:
        record, completed = run(name, [sys.executable, "-m", "analog_fpv_compressor", "-i", source, "-o", str(output), *base, *options])
        record["passed"] = (completed.returncode != 0 and not Path(str(output) + ".log").exists()
                            and (hashlib.sha256(protected.read_bytes()).hexdigest() == fingerprint if output == protected else not output.exists()))
        summary["cases"][name] = record
    summary["passed"] = all(case["passed"] for case in summary["cases"].values())
    summary["research_size_reference"] = {}
    for case, research in [("av1-medium", "sun-medium-c48-p6"), ("av1-strong", "sun-strong-c48"),
                           ("av1-off", "sun-off-c48-p6"), ("hevc-medium", "sun-hevc-c32"),
                           ("av1-scale480", "sun-scale480-c48")]:
        path = ROOT / "benchmarks/results/initial" / f"{research}.run.json"
        if path.exists():
            reference = json.loads(path.read_text(encoding="utf-8"))
            summary["research_size_reference"][case] = {"research_record": str(path), "research_bytes": reference.get("bytes"),
                                                        "cli_bytes": summary["cases"][case].get("bytes")}
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"All cases passed: {summary['passed']}; evidence: {destination}", flush=True)
    return verify_existing(destination / "summary.json", ffprobe)


if __name__ == "__main__":
    raise SystemExit(main())
