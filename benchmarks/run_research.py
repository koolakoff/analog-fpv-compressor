"""Reproducible DVR experiments; deliberately separate from the product CLI."""

import argparse
import csv
import json
import math
import platform
import subprocess
import time
from pathlib import Path
from fractions import Fraction

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks/results/initial"


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def run_logged(name, command):
    """Record arguments, diagnostics, elapsed time and exit status for every run."""
    record_path = RESULTS / f"{name}.run.json"
    if record_path.exists():
        raise FileExistsError(f"Run already exists: {name}")
    record = {"name": name, "command": command, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save_json(record_path, record)
    start = time.perf_counter()
    with (RESULTS / f"{name}.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    record.update(exit_code=process.returncode, wall_seconds=time.perf_counter() - start)
    save_json(record_path, record)
    print(f"{name}: exit={process.returncode}, elapsed={record['wall_seconds']:.2f}s", flush=True)
    if process.returncode:
        raise RuntimeError(f"Failed experiment: {name}; inspect the saved log")
    return record


def probe(path, ffprobe):
    return json.loads(subprocess.check_output(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        encoding="utf-8"))


def inspect(source, ffmpeg, ffprobe):
    """Stream low-resolution frames at native timestamps for exploratory metrics."""
    name = source.stem
    metadata = probe(source, ffprobe)
    save_json(RESULTS / f"{name}.probe.json", metadata)
    frame_data = json.loads(subprocess.check_output(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries",
         "frame=best_effort_timestamp_time", "-of", "json", str(source)], encoding="utf-8"))
    timestamps = [float(frame["best_effort_timestamp_time"]) for frame in frame_data["frames"]]
    save_json(RESULTS / f"{name}.frame-times.json", timestamps)
    command = [ffmpeg, "-hide_banner", "-nostdin", "-threads", "2", "-i", str(source),
               "-map", "0:v:0", "-an", "-vf", "scale=160:120:flags=bilinear,format=gray",
               "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    save_json(RESULTS / f"{name}.metrics-command.json", command)
    started = time.perf_counter()
    previous = None
    rows = []
    with (RESULTS / f"{name}.metrics.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                data = process.stdout.read(160 * 120)
                if not data:
                    break
                if len(data) != 160 * 120:
                    raise RuntimeError("Incomplete raw frame")
                frame = np.frombuffer(data, np.uint8).reshape(120, 160).astype(np.float32)
                # Exclude border/OSD regions only for exploratory statistics, never output crop.
                center = frame[12:108, 16:144]
                coarse = center.reshape(12, 8, 16, 8).mean(axis=(1, 3))
                mean, std = float(center.mean()), float(center.std())
                diff, corr = 0.0, 1.0
                if previous is not None:
                    diff = float(np.abs(center - previous).mean())
                    p, q = center - mean, previous - previous.mean()
                    norm = float(np.sqrt((p * p).sum() * (q * q).sum()))
                    corr = float((p * q).sum() / norm) if norm > 1e-6 else 0.0
                rows.append([len(rows), timestamps[len(rows)], mean, std,
                             float(coarse.std()), diff, corr])
                previous = center.copy()
        finally:
            process.stdout.close()
            if process.poll() is None and len(data) not in (0, 160 * 120):
                process.kill()
            exit_code = process.wait()
    if exit_code or len(rows) != len(timestamps):
        raise RuntimeError(f"Metrics decode failed for {source}")
    with (RESULTS / f"{name}.metrics.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["frame", "avi_time_s", "mean", "std", "coarse_std", "mad", "corr"])
        writer.writerows(rows)
    summary = {"decoded_frames": len(rows), "wall_seconds": time.perf_counter() - started,
               "time_basis": "ffprobe decoded frame best_effort_timestamp_time"}
    save_json(RESULTS / f"{name}.metrics-summary.json", summary)
    print(f"{name}: inspected {len(rows)} frames in {summary['wall_seconds']:.2f}s", flush=True)


def encode(spec, ffmpeg, ffprobe):
    """Run one explicit encode, then validate its streams and full decode."""
    name = spec["id"]
    output = RESULTS / f"{name}.mkv"
    source = ROOT / spec["input"]
    filters = []
    if spec.get("keep"):
        command = [ffmpeg, "-hide_banner", "-nostdin", "-n", "-benchmark", "-threads", "2",
                   "-i", str(source)]
    else:
        command = [ffmpeg, "-hide_banner", "-nostdin", "-n", "-benchmark", "-threads", "2",
                   "-ss", str(spec.get("start", 0)), "-i", str(source)]
        if "duration" in spec:
            command += ["-t", str(spec["duration"])]
    if spec.get("deinterlace"):
        filters.append("bwdif=mode=send_field:parity=auto:deint=all")
    if spec.get("denoise"):
        filters.append(f"hqdn3d={spec['denoise']}")
    if spec.get("deflicker"):
        filters.append("deflicker=size=5:mode=am")
    width, height = spec.get("scale", [640, 480])
    filters += [f"scale={width}:{height}:flags=lanczos:in_range=full:out_range=limited", "format=yuv420p"]
    if spec.get("keep"):
        # Each interval has separate denoiser state; no temporal history crosses the join.
        source_probe = probe(source, ffprobe)
        stream = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
        tick = Fraction(stream["time_base"])
        # trim's time arguments round to ticks; ceil both boundaries for [start, end).
        bounds = [(math.ceil(Fraction(str(b)) / tick), math.ceil(Fraction(str(e)) / tick))
                  for b, e in spec["keep"]]
        pieces = [f"[0:v]trim=start_pts={b}:end_pts={e},setpts=PTS-STARTPTS,{','.join(filters)}[v{i}]"
                  for i, (b, e) in enumerate(bounds)]
        pieces.append("".join(f"[v{i}]" for i in range(len(spec["keep"]))) +
                      f"concat=n={len(spec['keep'])}:v=1:a=0[out]")
        command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(pieces), "-map", "[out]",
                    "-enc_time_base", "filter"]
    else:
        command += ["-map", "0:v:0", "-filter_threads", "2", "-vf", ",".join(filters)]
    command += ["-an", "-fps_mode", "passthrough", "-color_range", "tv", "-c:v", spec.get("codec", "libsvtav1"),
                "-crf", str(spec["crf"]), "-preset", str(spec.get("preset", 6))]
    if spec.get("codec", "libsvtav1") == "libsvtav1":
        command += ["-svtav1-params", "lp=4:film-grain=0:film-grain-denoise=0"]
    else:
        command += ["-x265-params", "pools=4:frame-threads=1"]
    command.append(str(output))
    record = run_logged(name, command)
    metadata = probe(output, ffprobe)
    record.update(spec=spec, bytes=output.stat().st_size, output_probe=metadata)
    save_json(RESULTS / f"{name}.run.json", record)
    run_logged(f"{name}.decode", [ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
                                  "-xerror", "-i", str(output), "-f", "null", "-"])


def sheet(spec, ffmpeg):
    """Generate labeled contact sheets; no image-processing dependency needed."""
    output = RESULTS / f"{spec['id']}.jpg"
    selection = (f"select=between(n\\,{spec['first_frame']}\\,{spec['first_frame'] + 11})"
                 if "first_frame" in spec else f"fps={spec.get('fps', 1)}")
    filters = [selection, "scale=320:240",
               "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='%{pts\\:hms}':fontsize=18:fontcolor=white:box=1:boxcolor=black@0.7",
               f"tile={spec.get('tile', '4x3')}"]
    run_logged(spec["id"], [ffmpeg, "-hide_banner", "-nostdin", "-n", "-threads", "2", "-ss", str(spec.get("start", 0)),
                            "-i", str(ROOT / spec["input"]), "-vf", ",".join(filters),
                            "-frames:v", "1", "-update", "1", str(output)])


def comparison(spec, ffmpeg):
    """Compare four equally displayed frames in a labeled two-by-two grid."""
    command = [ffmpeg, "-hide_banner", "-nostdin", "-n"]
    filters = []
    for i, entry in enumerate(spec["inputs"]):
        command += ["-threads", "2"]
        if "frame" not in entry:
            command += ["-ss", str(entry["time"])]
        command += ["-i", str(ROOT / entry["path"])]
        selection = f"select=eq(n\\,{entry['frame']})," if "frame" in entry else ""
        filters.append(f"[{i}:v]{selection}scale=640:480,setsar=1,setpts=PTS-STARTPTS,"
                       "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
                       f"text='{entry['label']}':fontsize=20:fontcolor=white:box=1:boxcolor=black@0.7[v{i}]")
    filters.append("[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[out]")
    command += ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters), "-map", "[out]",
                "-frames:v", "1", "-update", "1", str(RESULTS / f"{spec['id']}.jpg")]
    run_logged(spec["id"], command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["inspect", "encode", "sheets", "compare"])
    parser.add_argument("--ffmpeg-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--only", nargs="+", help="Run only the listed manifest IDs")
    parser.add_argument("--input", type=Path, help="Inspect one local input instead of all AVI files")
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(args.ffmpeg_dir / "ffmpeg.exe")
    ffprobe = str(args.ffmpeg_dir / "ffprobe.exe")
    if not (RESULTS / "environment.json").exists():
        save_json(RESULTS / "environment.json", {"platform": platform.platform(), "python": platform.python_version(),
                  "numpy": np.__version__, "ffmpeg": subprocess.check_output([ffmpeg, "-version"], encoding="utf-8")})
    if args.phase == "inspect":
        sources = [args.input] if args.input else sorted((ROOT / "examples").glob("*.AVI"))
        for source in sources:
            inspect(source, ffmpeg, ffprobe)
    else:
        specs = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.only:
            if not set(args.only) <= {spec["id"] for spec in specs}:
                raise ValueError("Unknown experiment ID")
            specs = [spec for spec in specs if spec["id"] in args.only]
        for spec in specs:
            if args.phase == "encode":
                encode(spec, ffmpeg, ffprobe)
            elif args.phase == "sheets":
                sheet(spec, ffmpeg)
            else:
                comparison(spec, ffmpeg)


if __name__ == "__main__":
    main()
