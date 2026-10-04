# DVR research tools

These scripts run research and regression checks around the product CLI. Project requirements live
in [source-of-truth](../docs/source-of-truth.md). The current experiment context
and conclusions live in the [research journal](../docs/research/experiments-2026-10-04.md).

Use the project virtual environment with NumPy and an explicit FFmpeg directory.
No additional dependencies are required. Run commands from the repository root.

```powershell
$taskFfBin = 'C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin'
.\.venv\Scripts\python.exe benchmarks/analyze_timing.py --ffmpeg-bin $taskFfBin
.\.venv\Scripts\python.exe benchmarks/run_research.py inspect --ffmpeg-dir $taskFfBin
.\.venv\Scripts\python.exe benchmarks/run_research.py sheets --ffmpeg-dir $taskFfBin --manifest benchmarks/initial-sheets.json
.\.venv\Scripts\python.exe benchmarks/analyze_noise.py
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/baseline.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/quality.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/extra-quality.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/strong-denoise.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/confirmed-flicker.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/full-uncut.json
.\.venv\Scripts\python.exe benchmarks/run_research.py encode --ffmpeg-dir $taskFfBin --manifest benchmarks/full-cut-v2.json
.\.venv\Scripts\python.exe benchmarks/validate_full.py --ffprobe "$taskFfBin\ffprobe.exe" --manifest benchmarks/full-uncut.json benchmarks/full-cut-v2.json
.\.venv\Scripts\python.exe benchmarks/make_comparisons.py
.\.venv\Scripts\python.exe benchmarks/run_research.py compare --ffmpeg-dir $taskFfBin --manifest benchmarks/aligned-comparisons.json
.\.venv\Scripts\python.exe benchmarks/run_research.py compare --ffmpeg-dir $taskFfBin --manifest benchmarks/extra-comparisons.json
.\.venv\Scripts\python.exe benchmarks/summarize.py
```

The added home recording uses `home-quality.json` (720x480, static scene,
useful flight and mixed no-signal) and `home-full.json`. Inspect it separately:
`python benchmarks/run_research.py inspect --ffmpeg-dir $taskFfBin --input examples/home-other-helmet.AVI`.
`home-cut.json` compares snow-only and a REJECTED snow-plus-blue deletion:
native frames revealed useful scene flashes within the blue candidate.
Do not use that second run as a safe result. `home-blue.json` investigates
the apparent blue interval, which also contains those flashes.
Validate all nine technical full outputs by adding
`benchmarks/home-full.json benchmarks/home-cut.json` to the validation manifests.
Run `python benchmarks/analyze_home.py --ffmpeg-bin $taskFfBin` for its metrics.
Its mixed blue/snow labels are not snow-only annotations; `analyze_noise.py`
skips unknown references, and the separate `analyze_home.py` evaluates this case.

The supplied AVI inputs and `benchmarks/results/` remain local and ignored by
Git. Output IDs identify experiments and cannot be overwritten: use fresh IDs
in a copied manifest for repeated runs. Keep failed attempts for diagnosis.
The metrics inspection can be rerun to recalculate CSV and frame timestamps;
the timing tool has its own result directory.

Encode manifests explicitly specify input, source interval, CRF and optional
denoise, preset, scale or kept intervals. Kept intervals are half-open source
times, manually reviewed for these samples. They are not a production auto policy.
Temporal filters have independent state for each kept segment before concat.
Full-file runs use `full-uncut.json` and the separately reviewed `full-cut-v2.json`.
The original `full-cut.json` is a retained failed attempt: its rounded trim
boundaries dropped one frame. Do not use it for current results. Current trim
uses integer PTS bounds with ceiling; the encoder uses the filter time base.

Every successful encode has a run JSON, raw log, output passport and separate
full-decode validation. Summary tables only establish measured size and time;
they do not establish quality equivalence between codecs or settings.
Contact sheets and frame comparisons support limited visual inspection;
watch the videos to assess real-time movement and transient obstacles.

## Product CLI regression

The installed CLI is tested on all three original inputs, with full output
decode and source-to-output timestamp validation. Use a fresh output directory:

```powershell
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite defaults --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite explicit --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite variants --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/summarize_cli.py --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/validate_cli_edges.py --help
.\.venv\Scripts\python.exe benchmarks/validate_cli_audio_mp4.py --help
```

Defaults supply only input/output; explicit runs compare encoded video packet
hashes against defaults. Variants disable processing stages, preserve audio,
or downscale. Each invocation records exact arguments, console output and wall
time. Outputs, logs and JSON remain local. `summarize_cli.py` regenerates the
tracked [CLI validation report](../docs/research/cli-validation-2026-10-04.md).
Additional short comparisons and real ADPCM-to-AAC conversion are recorded in
[CLI edge checks](../docs/research/cli-edges-2026-10-04.md).
