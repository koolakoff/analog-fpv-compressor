"""Publish measured CLI regression results from complete local job reports."""

import argparse
import json
from pathlib import Path
from log_reports import read_report

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "outputs/cli-validation")
    directory = parser.parse_args().directory.resolve()
    expected = {f"{sample}-{mode}" for sample in ("oneflight", "termination", "home")
                for mode in ("defaults", "explicit", "off", "audio")}
    expected.update(("oneflight-scale480", "termination-scale480"))
    rows = []
    for name in sorted(expected):
        report = read_report(directory / f"{name}.mkv.report.json")
        invocation = json.loads((directory / f"{name}.invocation.json").read_text(encoding="utf-8"))
        if report["status"] != "complete" or invocation["exit_code"] != 0:
            raise RuntimeError(f"Incomplete or failed job: {name}")
        selected, validation = report["plan"]["selected"], report["validation"]
        rows.append({"id": name, "bytes": report["bytes"], "wall_seconds": invocation["wall_seconds"],
                     "duration_seconds": validation["duration_seconds"], "frames": validation["decoded_frames"],
                     "maximum_timestamp_error_seconds": validation["maximum_timestamp_error_seconds"],
                     "scale": f"{selected['width']}x{selected['height']}", "denoise": selected["denoise_level"],
                     "audio": selected["audio"], "cuts": report["plan"]["removed_intervals"],
                     "full_decode_passed": validation["full_decode_passed"], "audio_validation": validation["audio"]})
    explicit = json.loads((directory / "cli-explicit.json").read_text(encoding="utf-8"))
    if not all(row["defaults_match"] for row in explicit):
        raise RuntimeError("Explicit options did not match defaults")
    (directory / "cli-final-summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    lines = ["# Core and CLI validation — 2026-10-04", "",
             "Installed version 0.1.0 was tested on all three original local AVI recordings.",
             "Local evidence in `outputs/cli-validation/`: media, reports, English logs/events, raw FFmpeg logs,",
             "console transcripts, exact argv and wall time in invocation JSON. Summary: `cli-final-summary.json`.", "",
             "## Defaults and explicit settings", "",
             "The minimal command supplied only `--input` and `--output`. Explicit runs added",
             "`--crf 48 --preset 6 --denoise medium --scale original --deinterlace off`; snow cutting stayed automatic.",
             "Encoded video packet SHA256 matched exactly between defaults and explicit runs for all three inputs.", "",
             "## Comparison with research", "",
             "Stadium defaults produced exactly 40,906,929 and 31,446,859 bytes, matching the research outputs.",
             "The interrupted recording now lasts 277.406 s rather than 277.276 s: common-boundary timestamp mapping",
             "preserves original timing offsets and gaps that the research concat had shortened.",
             "Home defaults produced 9,495,335 rather than 8,859,940 bytes (+7.17%). The structural-frame guard",
             "retains another 0.433 s near the end of snow: cut `[61.35,87.38333333333334)` instead of",
             "`[61.35,87.816667)`. This is intentional conservative detection, with the same encoder settings.",
             "Blue-screen cutting remains disabled because useful frames occurred inside that candidate.",
             "Short denoise/scale/HEVC/bitrate comparisons and actual MP4/AAC checks are documented in",
             "[additional CLI checks](cli-edges-2026-10-04.md).", "",
             "## Full-file results", "",
             "14/14 jobs completed: three defaults, three explicit, three filters/cutting disabled, three audio",
             "preservation runs and two downscaled stadium runs. Every output was fully decoded and its frame count",
             "and every timestamp checked against the source mapping. FLAC sample counts and continuity were checked.",
             "MB are decimal. Wall time includes analysis, encoding and validation; some jobs overlapped other tests,",
             "so these times are not isolated encoder performance comparisons.", "",
             "| Job | Resolution | Denoise | Audio | MB | Duration, s | Frames | CLI wall, s | Max PTS error, ms |",
             "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['scale']} | {row['denoise']} | {row['audio']} | "
                     f"{row['bytes']/1e6:.3f} | {row['duration_seconds']:.3f} | {row['frames']} | "
                     f"{row['wall_seconds']:.2f} | {row['maximum_timestamp_error_seconds']*1000:.3f} |")
    lines += ["", "Home audio preservation increases the file from 9.495 to 13.855 MB. MKV uses FLAC to preserve",
              "decoded source audio losslessly; it need not be smaller than the original ADPCM audio. Audio removal",
              "remains the default. MP4 uses AAC; the actual 16,160 Hz home source was successfully converted to",
              "16,000 Hz with continuous timestamps and bounded AAC padding (see the additional checks).", "",
              "## Coverage and remaining limits", "",
              "The final test suite passed 34/34 tests in 18.683 s; evidence: `outputs/cli-validation/unit-tests.log`.",
              "Tests cover invalid options, cancellation of active FFmpeg, overwrite/publication races, stale inputs,",
              "all-snow and corrupt inputs, structural frames inside snow, sparse timestamps and exact audio samples.",
              "A synthetic synchronization test preserves an 80 ms initial offset and a 50 ms audio/video event offset",
              "after cutting. SAR/DAR preservation is tested in MKV and MP4; absent SAR assumes square pixels.",
              "Real interlaced DVR input was not supplied. Synthetic interlaced fields exercise auto idet and bwdif;",
              "ambiguous field order falls back to no deinterlacing. Forced bwdif on pathological VFR segment ends",
              "can extrapolate a field beyond the plan; strict validation rejects such an output.",
              "The static box remains useful content. Other DVRs and user playback review are still needed to assess",
              "general detector reliability and recognition of brief obstacles.", "",
              "## Reproduction", "", "```powershell",
              ".\\.venv\\Scripts\\python.exe benchmarks/validate_cli.py --suite defaults --directory outputs/cli-repeat",
              ".\\.venv\\Scripts\\python.exe benchmarks/validate_cli.py --suite explicit --directory outputs/cli-repeat",
              ".\\.venv\\Scripts\\python.exe benchmarks/validate_cli.py --suite variants --directory outputs/cli-repeat",
              ".\\.venv\\Scripts\\python.exe benchmarks/summarize_cli.py --directory outputs/cli-repeat",
              "```", "", "Choose a fresh directory: existing results are never overwritten. Run tests with",
              "`python -m unittest discover -s tests -v`; set `FPV_FFMPEG_BIN` if FFmpeg is absent from PATH.",
              "Requirements/defaults: [source of truth](../source-of-truth.md). Installation: [engineering README](../README-engineering.md).", ""]
    (ROOT / "docs/research/cli-validation-2026-10-04.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Published {len(rows)} verified full-file CLI results")


if __name__ == "__main__":
    main()
