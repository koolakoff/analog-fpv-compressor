"""Revalidate existing batch evidence without repeating full-file encodes."""

import argparse
import json
from pathlib import Path

from validate_batch import ROOT, packets


def read_report(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["status"] != "complete":
        raise RuntimeError(f"Incomplete report: {path}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "outputs/batch-validation")
    parser.add_argument("--home-directory", default="home-flights")
    args = parser.parse_args()
    directory = args.directory.resolve()
    defaults, splits = [], []
    for stem, short in (("air-school-stadion-oneflight", "oneflight"),
                        ("air-school-stadion-with-termination", "termination"),
                        ("home-other-helmet", "home")):
        output = directory / "defaults" / (stem + "_converted.mkv")
        report = read_report(Path(str(output) + ".report.json"))
        validation = report["validation"]
        ffmpeg = report["plan"]["analysis"]["tools"]["ffmpeg"]
        digest = packets(output, ffmpeg)
        if digest != packets(ROOT / "outputs/cli-validation" / (short + "-defaults.mkv"), ffmpeg):
            raise RuntimeError(f"Video packet regression: {stem}")
        if not validation["full_decode_passed"]:
            raise RuntimeError(f"Decode failed: {stem}")
        defaults.append({"input": stem, "bytes": report["bytes"], "validation": validation,
                         "matches_previous_video_packets": True, "packet_sha256": digest})
        if short == "oneflight":
            continue
        folder = "termination-flights" if short == "termination" else args.home_directory
        extension = "mkv" if short == "termination" else "mp4"
        summary = read_report(directory / folder / (stem + "_converted." + extension + ".report.json"))
        parts = []
        for item in summary["outputs"]:
            part = read_report(Path(item["report_path"]))
            if not part["validation"]["full_decode_passed"]:
                raise RuntimeError(f"Decode failed: {item['output_path']}")
            parts.append({"name": Path(item["output_path"]).name, "bytes": part["bytes"],
                          "keep_intervals": part["plan"]["keep_intervals"],
                          "mapping": part["plan"]["mapping"],
                          "validation": part["validation"]})
        frames = sum(part["validation"]["decoded_frames"] for part in parts)
        if frames != validation["decoded_frames"]:
            raise RuntimeError(f"Lost or duplicated frames: {stem}")
        splits.append({"input": stem, "parts": parts, "frames": frames,
                       "source_frames_preserved": True,
                       "removed_intervals": summary["plan"]["removed_intervals"]})
    result = {"defaults": defaults, "splits": splits}
    (directory / "final-summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in defaults:
        print(f"Defaults {row['input']}: {row['bytes']} bytes, {row['validation']['decoded_frames']} frames, packets match")
    for row in splits:
        print(f"Split {row['input']}: {len(row['parts'])} parts, {row['frames']} frames preserved")
        for part in row["parts"]:
            print(json.dumps(part))


if __name__ == "__main__":
    main()
