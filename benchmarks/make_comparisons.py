"""Build frame-index-aligned visual comparisons from decoded source timestamps."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/results/initial"
SCENES = [("sun", "oneflight", 20, 27), ("motion", "oneflight", 164, 173),
          ("interference", "with-termination", 230, 242)]
specs = []
extra_specs = []
strong_specs = []
origins = {}


def first_frame(stem, start, example_id):
    """Match the actual seeked decoded frame, avoiding sub-frame seek rounding."""
    record = json.loads((OUT / f"{example_id}.run.json").read_text(encoding="utf-8"))
    command = [record["command"][0], "-v", "error", "-threads", "2", "-ss", str(start),
               "-i", str(ROOT / f"examples/air-school-stadion-{stem}.AVI"), "-an",
               "-frames:v", "1", "-fps_mode", "passthrough", "-f", "framemd5", "-"]
    result = subprocess.check_output(command, encoding="utf-8")
    digest = next(line.split(",")[-1].strip() for line in result.splitlines() if line and not line.startswith("#"))
    source = ROOT / f"benchmarks/results/timing/air-school-stadion-{stem}/frames.framemd5"
    hashes = [line.split(",")[-1].strip() for line in source.read_text().splitlines()
              if line and not line.startswith("#")]
    index = hashes.index(digest)
    origins[f"{stem}:{start}"] = {"first_frame": index, "hash": digest, "command": command}
    return index


for scene, stem, start, target in SCENES:
    times = json.loads((OUT / f"air-school-stadion-{stem}.frame-times.json").read_text())
    first = first_frame(stem, start, f"{scene}-medium-c48-p6")
    chosen = next(i for i, t in enumerate(times) if t >= target)
    source = {"path": f"examples/air-school-stadion-{stem}.AVI", "frame": chosen,
              "label": f"Source {times[chosen]:.3f}s frame {chosen}"}
    variants = [("quality", [f"{scene}-medium-c{crf}-p6" for crf in (34, 42, 48)]),
                ("denoise", [f"{scene}-off-c34-p6", f"{scene}-weak-c34-p6", f"{scene}-medium-c34-p6"])]
    if scene != "motion":
        variants.append(("codec", [f"{scene}-medium-c48-p6", f"{scene}-hevc-c32", f"{scene}-hevc-c36"]))
    variants.append(("scale", [f"{scene}-medium-c42-p6", f"{scene}-medium-c48-p6", f"{scene}-scale480-c48"]))
    for kind, names in variants:
        inputs = [source] + [{"path": f"benchmarks/results/initial/{name}.mkv",
                              "frame": chosen - first, "label": name} for name in names]
        specs.append({"id": f"aligned-{scene}-{kind}", "inputs": inputs})
    names = [f"{scene}-medium-c48-p6", f"{scene}-medium-c54-p6", f"{scene}-scale480-c48"]
    extra_specs.append({"id": f"aligned-{scene}-aggressive", "inputs": [source] +
        [{"path": f"benchmarks/results/initial/{name}.mkv", "frame": chosen-first, "label": name} for name in names]})
    names = [f"{scene}-off-c48-p6", f"{scene}-medium-c48-p6", f"{scene}-strong-c48"]
    strong_specs.append({"id": f"aligned-{scene}-strong", "inputs": [source] +
        [{"path": f"benchmarks/results/initial/{name}.mkv", "frame": chosen-first, "label": name} for name in names]})
    if scene != "motion":
        chosen = next(i for i, t in enumerate(times) if t >= (26 if scene == "sun" else 242))
        source = {"path": f"examples/air-school-stadion-{stem}.AVI", "frame": chosen,
                  "label": f"Source {times[chosen]:.3f}s frame {chosen}"}
        names = [f"{scene}-off-c48-p6", f"{scene}-medium-c48-p6", f"{scene}-deflicker-c48"]
        extra_specs.append({"id": f"aligned-{scene}-deflicker", "inputs": [source] +
            [{"path": f"benchmarks/results/initial/{name}.mkv", "frame": chosen-first, "label": name} for name in names]})
(ROOT / "benchmarks/aligned-comparisons.json").write_text(json.dumps(specs, indent=2), encoding="utf-8")
(ROOT / "benchmarks/extra-comparisons.json").write_text(json.dumps(extra_specs, indent=2), encoding="utf-8")
(ROOT / "benchmarks/strong-comparisons.json").write_text(json.dumps(strong_specs, indent=2), encoding="utf-8")
times = json.loads((OUT / "air-school-stadion-with-termination.frame-times.json").read_text())
first = first_frame("with-termination", 146, "confirmed-flicker-medium-c48")
chosen = 3907
source = {"path": "examples/air-school-stadion-with-termination.AVI", "frame": chosen,
          "label": f"Source {times[chosen]:.3f}s frame {chosen}"}
names = ["confirmed-flicker-off-c48", "confirmed-flicker-medium-c48", "confirmed-flicker-deflicker-c48"]
confirmed = [{"id": "aligned-confirmed-flicker-v2", "inputs": [source] +
    [{"path": f"benchmarks/results/initial/{name}.mkv", "frame": chosen-first, "label": name} for name in names]}]
(ROOT / "benchmarks/confirmed-comparisons.json").write_text(json.dumps(confirmed, indent=2), encoding="utf-8")
(OUT / "seek-origins.json").write_text(json.dumps(origins, indent=2), encoding="utf-8")
print(f"Created {len(specs)} frame-index comparison specifications")
