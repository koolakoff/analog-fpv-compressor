# Core and CLI validation — 2026-10-04

Installed version 0.1.0 was tested on all three original local AVI recordings.
Local evidence in `outputs/cli-validation/`: media, reports, English logs/events, raw FFmpeg logs,
console transcripts, exact argv and wall time in invocation JSON. Summary: `cli-final-summary.json`.

## Defaults and explicit settings

The minimal command supplied only `--input` and `--output`. Explicit runs added
`--crf 48 --preset 6 --denoise medium --scale original --deinterlace off`; snow cutting stayed automatic.
Encoded video packet SHA256 matched exactly between defaults and explicit runs for all three inputs.

## Comparison with research

Stadium defaults produced exactly 40,906,929 and 31,446,859 bytes, matching the research outputs.
The interrupted recording now lasts 277.406 s rather than 277.276 s: common-boundary timestamp mapping
preserves original timing offsets and gaps that the research concat had shortened.
Home defaults produced 9,495,335 rather than 8,859,940 bytes (+7.17%). The structural-frame guard
retains another 0.433 s near the end of snow: cut `[61.35,87.38333333333334)` instead of
`[61.35,87.816667)`. This is intentional conservative detection, with the same encoder settings.
Blue-screen cutting remains disabled because useful frames occurred inside that candidate.
Short denoise/scale/HEVC/bitrate comparisons and actual MP4/AAC checks are documented in
[additional CLI checks](cli-edges-2026-10-04.md).

## Full-file results

14/14 jobs completed: three defaults, three explicit, three filters/cutting disabled, three audio
preservation runs and two downscaled stadium runs. Every output was fully decoded and its frame count
and every timestamp checked against the source mapping. FLAC sample counts and continuity were checked.
MB are decimal. Wall time includes analysis, encoding and validation; some jobs overlapped other tests,
so these times are not isolated encoder performance comparisons.

| Job | Resolution | Denoise | Audio | MB | Duration, s | Frames | CLI wall, s | Max PTS error, ms |
|---|---|---|---|---:|---:|---:|---:|---:|
| home-audio | 720x480 | medium | keep | 13.855 | 136.267 | 4080 | 48.80 | 0.333 |
| home-defaults | 720x480 | medium | remove | 9.495 | 136.233 | 4080 | 55.95 | 0.333 |
| home-explicit | 720x480 | medium | remove | 9.495 | 136.233 | 4080 | 75.39 | 0.333 |
| home-off | 720x480 | off | remove | 53.169 | 162.266 | 4829 | 63.49 | 0.333 |
| oneflight-audio | 640x480 | medium | keep | 40.946 | 279.466 | 8364 | 151.99 | 0.500 |
| oneflight-defaults | 640x480 | medium | remove | 40.907 | 279.466 | 8364 | 136.67 | 0.500 |
| oneflight-explicit | 640x480 | medium | remove | 40.907 | 279.466 | 8364 | 144.54 | 0.500 |
| oneflight-off | 640x480 | off | remove | 47.743 | 284.676 | 8517 | 185.23 | 0.500 |
| oneflight-scale480 | 480x360 | medium | remove | 25.793 | 279.466 | 8364 | 88.31 | 0.500 |
| termination-audio | 640x480 | medium | keep | 31.486 | 277.406 | 7896 | 114.64 | 0.500 |
| termination-defaults | 640x480 | medium | remove | 31.447 | 277.406 | 7896 | 178.58 | 0.500 |
| termination-explicit | 640x480 | medium | remove | 31.447 | 277.406 | 7896 | 172.49 | 0.500 |
| termination-off | 640x480 | off | remove | 99.682 | 344.485 | 9409 | 137.92 | 0.500 |
| termination-scale480 | 480x360 | medium | remove | 19.407 | 277.406 | 7896 | 81.07 | 0.500 |

Home audio preservation increases the file from 9.495 to 13.855 MB. MKV uses FLAC to preserve
decoded source audio losslessly; it need not be smaller than the original ADPCM audio. Audio removal
remains the default. MP4 uses AAC; the actual 16,160 Hz home source was successfully converted to
16,000 Hz with continuous timestamps and bounded AAC padding (see the additional checks).

## Coverage and remaining limits

The final test suite passed 34/34 tests in 18.683 s; evidence: `outputs/cli-validation/unit-tests.log`.
Tests cover invalid options, cancellation of active FFmpeg, overwrite/publication races, stale inputs,
all-snow and corrupt inputs, structural frames inside snow, sparse timestamps and exact audio samples.
A synthetic synchronization test preserves an 80 ms initial offset and a 50 ms audio/video event offset
after cutting. SAR/DAR preservation is tested in MKV and MP4; absent SAR assumes square pixels.
Real interlaced DVR input was not supplied. Synthetic interlaced fields exercise auto idet and bwdif;
ambiguous field order falls back to no deinterlacing. Forced bwdif on pathological VFR segment ends
can extrapolate a field beyond the plan; strict validation rejects such an output.
The static box remains useful content. Other DVRs and user playback review are still needed to assess
general detector reliability and recognition of brief obstacles.

## Reproduction

```powershell
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite defaults --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite explicit --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/validate_cli.py --suite variants --directory outputs/cli-repeat
.\.venv\Scripts\python.exe benchmarks/summarize_cli.py --directory outputs/cli-repeat
```

Choose a fresh directory: existing results are never overwritten. Run tests with
`python -m unittest discover -s tests -v`; set `FPV_FFMPEG_BIN` if FFmpeg is absent from PATH.
Requirements/defaults: [source of truth](../source-of-truth.md). Installation: [README](../../README.md).
