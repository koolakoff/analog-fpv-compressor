"""Exercise the real CLI on local DVRs and compare defaults with research."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "outputs/cli-validation"
SAMPLES = {
    "oneflight": ("air-school-stadion-oneflight", "full-oneflight-cut-c48-v2"),
    "termination": ("air-school-stadion-with-termination", "full-termination-cut-c48-v2"),
    "home": ("home-other-helmet", "full-home-cut-snow-c48"),
}


def run_job(name, source, options):
    """Run only through the user-facing CLI and persist its command and streams."""
    output = RESULTS / f"{name}.mkv"
    command = [sys.executable, "-m", "analog_fpv_compressor", "-i", str(source), "-o", str(output), *options]
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    started = time.perf_counter()
    with (RESULTS / f"{name}.console.log").open("x", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
    record = {"name": name, "command": command, "exit_code": result.returncode,
              "wall_seconds": time.perf_counter() - started}
    (RESULTS / f"{name}.invocation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"CLI failed: {name}; see its console log")
    report = json.loads(Path(str(output) + ".report.json").read_text(encoding="utf-8"))
    record.update(bytes=report["bytes"], selected=report["plan"]["selected"],
                  keep_intervals=report["plan"]["keep_intervals"],
                  removed_intervals=report["plan"]["removed_intervals"], validation=report["validation"])
    print(f"{name}: {record['bytes']/1e6:.3f} MB; {record['wall_seconds']:.2f}s; verified", flush=True)
    return record


def packet_digest(path, ffmpeg):
    raw = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                                   "-c:v", "copy", "-f", "data", "-"])
    return hashlib.sha256(raw).hexdigest()


def main():
    global RESULTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["defaults", "explicit", "variants"], required=True)
    parser.add_argument("--directory", type=Path, default=RESULTS,
                        help="Fresh output directory; use the same directory for all three suites")
    args = parser.parse_args()
    RESULTS = args.directory.resolve()
    RESULTS.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (stem, reference) in SAMPLES.items():
        source = ROOT / "examples" / f"{stem}.AVI"
        if args.suite == "defaults":
            record = run_job(f"{name}-defaults", source, [])
        elif args.suite == "explicit":
            record = run_job(f"{name}-explicit", source, ["--crf", "48", "--preset", "6", "--denoise", "medium",
                                                         "--scale", "original", "--deinterlace", "off"])
            baseline = json.loads((RESULTS / f"{name}-defaults.mkv.report.json").read_text(encoding="utf-8"))
            ffmpeg = baseline["plan"]["analysis"]["tools"]["ffmpeg"]
            record["defaults_video_packet_sha256"] = packet_digest(RESULTS / f"{name}-defaults.mkv", ffmpeg)
            record["explicit_video_packet_sha256"] = packet_digest(RESULTS / f"{name}-explicit.mkv", ffmpeg)
            record["defaults_match"] = record["defaults_video_packet_sha256"] == record["explicit_video_packet_sha256"]
            if not record["defaults_match"]:
                raise RuntimeError(f"Explicit defaults do not match minimal settings: {name}")
        else:
            for variant, options in [("off", ["--denoise", "off", "--cut-no-signal", "off", "--deinterlace", "off"]),
                                     ("audio", ["--audio", "keep"]),
                                     *([] if name == "home" else [("scale480", ["--scale", "480x360"])])]:
                records.append(run_job(f"{name}-{variant}", source, options))
                (RESULTS / f"cli-{args.suite}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
            continue
        research = json.loads((ROOT / "benchmarks/results/initial" / f"{reference}.run.json").read_text(encoding="utf-8"))
        record["research_reference"] = reference
        record["research_bytes"] = research["bytes"]
        record["research_size_change_percent"] = (record["bytes"] / research["bytes"] - 1) * 100
        record["research_keep_intervals"] = research["spec"]["keep"]
        records.append(record)
        (RESULTS / f"cli-{args.suite}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (RESULTS / f"cli-{args.suite}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
