"""Compare denoise strength and selected alternatives on aligned lossless clips."""

import argparse
import json
from pathlib import Path
import subprocess

import run_research as research
from analog_fpv_compressor.runtime import discover_tools


SCENES = (("motion", 8, 6, .8), ("forest", 125, 6, 2),
          ("rf", 304, 6, 3.4), ("flash", 324, 4, 1.2))


def timestamps(path, ffprobe):
    result = subprocess.check_output([ffprobe, "-v", "error", "-select_streams", "v:0",
                                     "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
                                     "-of", "json", str(path)])
    return [float(frame["best_effort_timestamp_time"]) for frame in json.loads(result)["frames"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--extra-scales", action="store_true", help="Add downscale comparisons to an existing run")
    args = parser.parse_args()
    research.RESULTS = args.directory.resolve()
    if args.extra_scales:
        tools = discover_tools()
        summary = json.loads((research.RESULTS / "summary.json").read_text())
        for scene, start, _, target in SCENES:
            if scene == "forest":
                continue
            source = research.RESULTS / (scene + "-source.mkv")
            source_times = timestamps(source, tools["ffprobe"])
            identifier = scene + "-scale480"
            spec = {"id": identifier, "input": source.relative_to(research.ROOT).as_posix(),
                    "crf": 48, "preset": 6, "denoise": "3:5:1.5:6", "scale": [480, 360]}
            research.encode(spec, tools["ffmpeg"], tools["ffprobe"])
            output_times = timestamps(research.RESULTS / (identifier + ".mkv"), tools["ffprobe"])
            if len(source_times) != len(output_times) or any(abs(a - b) > .0011 for a, b in zip(source_times, output_times)):
                raise RuntimeError(f"Frame alignment failed: {identifier}")
            record = json.loads((research.RESULTS / (identifier + ".run.json")).read_text())
            summary.append({"id": identifier, "source_start_s": start, "frames": len(output_times),
                            "bytes": record["bytes"], "wall_seconds": record["wall_seconds"],
                            "spec": spec, "alignment_passed": True, "decode_passed": True})
            chosen = min(range(len(source_times)), key=lambda index: abs(source_times[index] - target))
            inputs = [{"path": path.relative_to(research.ROOT).as_posix(), "frame": chosen, "label": label}
                      for path, label in [(source, "Source"),
                        (research.RESULTS / (scene + "-medium.mkv"), "640x480 medium"),
                        (research.RESULTS / (scene + "-scale480.mkv"), "480x360 medium"),
                        (research.RESULTS / (scene + "-strong.mkv"), "640x480 strong")]]
            research.comparison({"id": scene + "-extra-scales", "inputs": inputs}, tools["ffmpeg"])
            research.save_json(research.RESULTS / "summary.json", summary)
        return
    research.RESULTS.mkdir(parents=True, exist_ok=False)
    tools = discover_tools()
    summary = []
    for scene, start, duration, target in SCENES:
        source = research.RESULTS / (scene + "-source.mkv")
        research.run_logged(scene + "-extract", [tools["ffmpeg"], "-hide_banner", "-nostdin", "-n",
            "-threads", "2", "-ss", str(start), "-i", str(research.ROOT / "examples/Frantisek.AVI"),
            "-t", str(duration), "-an", "-filter_threads", "2", "-vf",
            "scale=in_range=full:out_range=full,setpts=PTS-STARTPTS,format=yuv422p",
            "-fps_mode", "passthrough", "-color_range", "pc", "-c:v", "ffv1", str(source)])
        source_times = timestamps(source, tools["ffprobe"])
        variants = [("off", None, {}), ("medium", "3:5:1.5:6", {}), ("strong", "6:8:2:8", {})]
        if scene == "forest":
            variants += [("scale480", "3:5:1.5:6", {"scale": [480, 360]}),
                         ("crf42", "3:5:1.5:6", {"crf": 42})]
        if scene in ("flash", "motion"):
            variants += [("deflicker", "3:5:1.5:6", {"deflicker": True})]
        for name, denoise, overrides in variants:
            identifier = scene + "-" + name
            spec = {"id": identifier, "input": source.relative_to(research.ROOT).as_posix(),
                    "crf": 48, "preset": 6, "denoise": denoise, **overrides}
            research.encode(spec, tools["ffmpeg"], tools["ffprobe"])
            output = research.RESULTS / (identifier + ".mkv")
            output_times = timestamps(output, tools["ffprobe"])
            if len(source_times) != len(output_times) or any(
                    abs(a - b) > .0011 for a, b in zip(source_times, output_times)):
                raise RuntimeError(f"Frame alignment failed: {identifier}")
            record = json.loads((research.RESULTS / (identifier + ".run.json")).read_text())
            summary.append({"id": identifier, "source_start_s": start, "frames": len(output_times),
                            "bytes": record["bytes"], "wall_seconds": record["wall_seconds"],
                            "spec": spec, "alignment_passed": True, "decode_passed": True})
            research.save_json(research.RESULTS / "summary.json", summary)
        chosen = min(range(len(source_times)), key=lambda index: abs(source_times[index] - target))
        inputs = [{"path": path.relative_to(research.ROOT).as_posix(), "frame": chosen, "label": label}
                  for path, label in [(source, "Source"),
                    (research.RESULTS / (scene + "-off.mkv"), "AV1 CRF48 denoise off"),
                    (research.RESULTS / (scene + "-medium.mkv"), "AV1 CRF48 medium"),
                    (research.RESULTS / (scene + "-strong.mkv"), "AV1 CRF48 strong")]]
        research.comparison({"id": scene + "-comparison", "inputs": inputs}, tools["ffmpeg"])
        if len(variants) > 3:
            extra = [inputs[0], inputs[2]] + [
                {"path": (research.RESULTS / (scene + "-" + name + ".mkv")).relative_to(research.ROOT).as_posix(),
                 "frame": chosen, "label": name} for name, _, _ in variants[3:]]
            if len(extra) == 3:
                extra.append(inputs[1])
            research.comparison({"id": scene + "-alternatives", "inputs": extra}, tools["ffmpeg"])
    print(f"Completed {len(summary)} aligned comparisons", flush=True)


if __name__ == "__main__":
    main()
