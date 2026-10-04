"""Verify the extracted EXE outside the repo with no developer Python/tool PATH."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def packet_digest(path, ffmpeg):
    raw = subprocess.check_output([str(ffmpeg), "-v", "error", "-i", str(path),
                                   "-map", "0:v:0", "-c:v", "copy", "-f", "data", "-"])
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--ffmpeg-dir", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="Also compare the full oneflight recording")
    parser.add_argument("--output-directory", type=Path, default=ROOT / "outputs/release-smoke")
    args = parser.parse_args()
    evidence = args.output_directory.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    temp_parent = Path(tempfile.gettempdir()).resolve()
    extracted = Path(tempfile.mkdtemp(prefix="fpv release smoke ")).resolve()
    if extracted.parent != temp_parent:
        raise RuntimeError("Unexpected extraction directory")
    with zipfile.ZipFile(args.archive.resolve()) as archive:
        for item in archive.namelist():
            if not (extracted / item).resolve().is_relative_to(extracted):
                raise RuntimeError("Unsafe archive path")
        if any(Path(name).name.lower() in ("ffmpeg.exe", "ffprobe.exe") for name in archive.namelist()):
            raise RuntimeError("This release must not redistribute FFmpeg")
        archive.extractall(extracted)
    bundle = next(extracted.glob("analog-fpv-compressor-*"))
    for line in (bundle / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        if hashlib.sha256((bundle / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Bundle checksum mismatch: {relative}")
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    cases = []

    def run(name, options, expected_code=0):
        command = [str(bundle / "fpv-compress.exe"), *map(str, options)]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=bundle, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=600)
        (evidence / (name + ".stdout.log")).write_bytes(result.stdout)
        (evidence / (name + ".stderr.log")).write_bytes(result.stderr)
        record = {"name": name, "command": command, "exit_code": result.returncode,
                  "wall_seconds": time.perf_counter() - started}
        cases.append(record)
        if result.returncode != expected_code:
            raise RuntimeError(f"Frozen CLI failed: {name}; see {evidence}")
        print(f"{name}: PASS ({record['wall_seconds']:.2f}s)", flush=True)
        return record

    run("help", ["--help"])
    run("version", ["--version"])
    gray_source = bundle / "home short Unicode Юнікод space.AVI"
    shutil.copy2(ROOT / "outputs/cli-audio-mp4/20261004T002225Z/gray-source-with-audio.AVI", gray_source)
    run("missing-tools", ["-i", gray_source, "-o", bundle / "missing.mkv", "--analyze-only"], 1)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(args.ffmpeg_dir.resolve() / name, bundle / "tools" / name)
    sun_source = bundle / "sun clip.mkv"
    shutil.copy2(ROOT / "outputs/cli-edges/20261004T000755Z/sun-source.mkv", sun_source)
    run("minimal-sun", ["-i", sun_source, "-o", bundle / "sun.mkv"])
    actual = packet_digest(bundle / "sun.mkv", bundle / "tools/ffmpeg.exe")
    reference = packet_digest(ROOT / "outputs/cli-edges/20261004T000755Z/av1-medium.mkv", bundle / "tools/ffmpeg.exe")
    if actual != reference:
        raise RuntimeError("Frozen video differs from the previous CLI sun reference")
    cases[-1]["matches_reference_video_packets"] = True
    run("audio-mp4", ["-i", gray_source, "-o", bundle / "audio.mp4", "--audio", "keep"])
    run("manual-hevc", ["-i", gray_source, "-o", bundle / "manual.mkv", "--codec", "hevc",
                        "--preset", "fast", "--denoise", "off", "--deinterlace", "off",
                        "--cut-no-signal", "off", "--scale", "480x360"])
    if args.full:
        run("full-oneflight", ["-i", ROOT / "examples/air-school-stadion-oneflight.AVI",
                               "-o", bundle / "full.mkv"])
        actual = packet_digest(bundle / "full.mkv", bundle / "tools/ffmpeg.exe")
        reference = packet_digest(ROOT / "outputs/cli-validation/oneflight-defaults.mkv", bundle / "tools/ffmpeg.exe")
        if actual != reference:
            raise RuntimeError("Frozen full recording differs from the development CLI")
        cases[-1]["matches_reference_video_packets"] = True
    for report in bundle.glob("*.report.json"):
        shutil.copy2(report, evidence / report.name)
    (evidence / "summary.json").write_text(json.dumps({"archive": str(args.archive.resolve()),
        "isolated_bundle": str(bundle), "environment_path": environment["PATH"], "cases": cases,
        "ffmpeg_copied_only_for_local_testing": True}, indent=2), encoding="utf-8")
    print(f"Evidence: {evidence}. Temporary extracted files retained at {bundle}.")


if __name__ == "__main__":
    main()
