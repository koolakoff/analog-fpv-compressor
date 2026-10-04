"""Validate real DVR batch defaults and independently numbered flight outputs."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def packets(path, ffmpeg):
    raw = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                                   "-c:v", "copy", "-f", "data", "-"])
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "outputs/batch-validation")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    cases = []

    def checkpoint():
        (directory / "summary.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")

    def run(name, arguments):
        command = [sys.executable, "-m", "analog_fpv_compressor", *map(str, arguments)]
        started = time.perf_counter()
        with (directory / (name + ".console.log")).open("x", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        record = {"name": name, "command": command, "exit_code": result.returncode,
                  "wall_seconds": time.perf_counter() - started}
        (directory / (name + ".invocation.json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
        cases.append(record)
        checkpoint()
        if result.returncode:
            raise RuntimeError(f"CLI failed: {name}; inspect its console log")
        print(f"{name}: PASS ({record['wall_seconds']:.2f}s)", flush=True)
        return record

    defaults = directory / "defaults"
    record = run("batch-defaults", ["-i", ROOT / "examples/*.AVI", "--output-dir", defaults])
    comparisons = []
    for stem, previous in (("air-school-stadion-oneflight", "oneflight"),
                           ("air-school-stadion-with-termination", "termination"),
                           ("home-other-helmet", "home")):
        output = defaults / (stem + "_converted.mkv")
        report = json.loads(Path(str(output) + ".report.json").read_text(encoding="utf-8"))
        ffmpeg = report["plan"]["analysis"]["tools"]["ffmpeg"]
        actual = packets(output, ffmpeg)
        reference = packets(ROOT / "outputs/cli-validation" / (previous + "-defaults.mkv"), ffmpeg)
        if actual != reference:
            raise RuntimeError(f"Batch defaults differ from the previous single-input CLI: {stem}")
        comparisons.append({"input": stem, "bytes": report["bytes"], "validation": report["validation"],
                            "matches_previous_video_packets": True, "packet_sha256": actual})
    record["outputs"] = comparisons
    checkpoint()
    for stem, short, extra in (("air-school-stadion-with-termination", "termination", ["--audio", "keep"]),
                               ("home-other-helmet", "home", ["--audio", "keep", "--format", "mp4"])):
        split = directory / (short + "-flights")
        record = run(short + "-split", ["-i", ROOT / "examples" / (stem + ".AVI"),
                                        "--split-flights", "--output-dir", split, *extra])
        suffix = "mp4" if short == "home" else "mkv"
        summary = json.loads((split / (stem + "_converted." + suffix + ".report.json")).read_text(encoding="utf-8"))
        if summary["status"] != "complete":
            raise RuntimeError("Split summary is not complete")
        parts = []
        for item in summary["outputs"]:
            report = json.loads(Path(item["report_path"]).read_text(encoding="utf-8"))
            parts.append({"name": Path(item["output_path"]).name, "bytes": report["bytes"],
                          "keep_intervals": report["plan"]["keep_intervals"],
                          "validation": report["validation"]})
        baseline = json.loads((defaults / (stem + "_converted.mkv.report.json")).read_text(encoding="utf-8"))
        if sum(part["validation"]["decoded_frames"] for part in parts) != baseline["validation"]["decoded_frames"]:
            raise RuntimeError("Split outputs lost or duplicated frames")
        record.update(parts=parts, removed_intervals=summary["plan"]["removed_intervals"],
                      source_frames_preserved=True)
        checkpoint()
    print(f"All cases verified; evidence: {directory}")


if __name__ == "__main__":
    main()
