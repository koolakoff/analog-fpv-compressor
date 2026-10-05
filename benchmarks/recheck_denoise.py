"""Isolate denoise value at identical codec settings on aligned DVR excerpts."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time

from analog_fpv_compressor.runtime import discover_tools

ROOT = Path(__file__).resolve().parents[1]
SCENES = (("weak-signal", "home-other-helmet.AVI", 95, 11),
          ("gray-box", "home-other-helmet.AVI", 120, 4),
          ("stadion-flight", "air-school-stadion-oneflight.AVI", 18, 8),
          ("frantisek-motion", "Frantisek.AVI", 8, 6))


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def run(directory, name, command):
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True)
    record = {"command": command, "wall_seconds": time.perf_counter() - started,
              "exit_code": result.returncode}
    save(directory / (name + ".run.json"), record)
    (directory / (name + ".log")).write_bytes(result.stderr)
    result.check_returncode()
    return record


def frame_times(path, tools):
    data = json.loads(subprocess.check_output([tools["ffprobe"], "-v", "error", "-select_streams", "v:0",
        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)]))
    return [float(f["best_effort_timestamp_time"]) for f in data["frames"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    tools = discover_tools()
    save(directory / "environment.json", {"tools": tools, "version": subprocess.check_output(
        [tools["ffmpeg"], "-version"], text=True).splitlines()[0], "warmup_seconds": 2})
    results = []
    for scene, filename, start, duration in SCENES:
        original = ROOT / "examples" / filename
        source = directory / (scene + "-source.mkv")
        run(directory, scene + "-extract", [tools["ffmpeg"], "-v", "error", "-nostdin", "-n", "-threads", "4",
            "-ss", str(start - 2), "-i", str(original), "-t", str(duration + 2), "-an", "-filter_threads", "4",
            "-vf", "scale=in_range=full:out_range=full,format=yuv422p,setpts=PTS-STARTPTS",
            "-fps_mode", "passthrough", "-color_range", "pc", "-c:v", "ffv1", str(source)])
        times = frame_times(source, tools)
        retained = [t - 2 for t in times if t >= 2]
        configurations = [("av1", 48, 2), ("hevc", 32, 1)]
        if scene == "weak-signal":
            configurations.append(("av1", 36, 1))
        if scene == "gray-box":
            configurations.append(("av1", 34, 2))
        for codec, crf, repetitions in configurations:
            for repeat in range(repetitions):
                for mode in (("off", "medium") if repeat == 0 else ("medium", "off")):
                    name = f"{scene}-{codec}-c{crf}-{mode}-r{repeat + 1}"
                    output = directory / (name + ".mkv")
                    filters = (["hqdn3d=3:5:1.5:6"] if mode == "medium" else []) + [
                        "trim=start=2", "setpts=PTS-2/TB", "scale=in_range=full:out_range=limited", "format=yuv420p"]
                    command = [tools["ffmpeg"], "-v", "error", "-nostdin", "-n", "-threads", "4", "-i", str(source),
                        "-an", "-filter_threads", "4", "-vf", ",".join(filters), "-fps_mode", "passthrough",
                        "-enc_time_base:v", "filter", "-color_range", "tv", "-c:v", "libsvtav1" if codec == "av1" else "libx265",
                        "-preset", "6", "-crf", str(crf)]
                    command += (["-svtav1-params", "lp=4:film-grain=0:film-grain-denoise=0"] if codec == "av1" else
                                ["-x265-params", "pools=4:frame-threads=1"])
                    record = run(directory, name, command + [str(output)])
                    actual = frame_times(output, tools)
                    if len(actual) != len(retained) or any(abs(a - b) > .0011 for a,b in zip(actual,retained)):
                        raise RuntimeError(f"Frame alignment failed: {name}")
                    run(directory, name + "-decode", [tools["ffmpeg"], "-v", "error", "-xerror", "-threads", "4",
                        "-i", str(output), "-f", "null", "-"])
                    entry = {"scene": scene, "source": filename, "start_s": start, "duration_s": duration,
                             "codec": codec, "crf": crf, "mode": mode, "repeat": repeat + 1,
                             "bytes": output.stat().st_size, "wall_seconds": record["wall_seconds"],
                             "frames": len(actual), "alignment_passed": True, "decode_passed": True,
                             "path": str(output)}
                    results.append(entry)
                    save(directory / "runs.json", results)
                    print(f"{name}: {entry['bytes']} bytes, {entry['wall_seconds']:.3f}s, {len(actual)} frames", flush=True)
    summary = []
    for scene, _, _, _ in SCENES:
        for codec, crf in sorted({(r["codec"], r["crf"]) for r in results if r["scene"] == scene}):
            pair = {}
            for mode in ("off", "medium"):
                rows = [r for r in results if (r["scene"],r["codec"],r["crf"],r["mode"]) == (scene,codec,crf,mode)]
                pair[mode] = {"bytes": statistics.mean(r["bytes"] for r in rows),
                              "seconds": statistics.mean(r["wall_seconds"] for r in rows)}
            summary.append({"scene":scene,"codec":codec,"crf":crf, **pair,
                "size_saved_percent":100 * (1-pair["medium"]["bytes"]/pair["off"]["bytes"]),
                "time_change_percent":100 * (pair["medium"]["seconds"]/pair["off"]["seconds"]-1)})
    save(directory / "summary.json", summary)
    fingerprints = {}
    for filename in {scene[1] for scene in SCENES}:
        with (ROOT / "examples" / filename).open("rb") as stream:
            fingerprints[filename] = hashlib.file_digest(stream,"sha256").hexdigest()
    save(directory / "source-sha256.json", fingerprints)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
