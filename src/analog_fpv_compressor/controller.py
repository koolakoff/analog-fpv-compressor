"""Analyze recordings and resolve explicit, explainable processing plans."""

from dataclasses import replace
from fractions import Fraction
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

import numpy as np

from .detection import POLICY_VERSION, complement, snow_intervals
from .models import Analysis, Event, Plan
from .runtime import capture, check_cancel, discover_tools


DENOISE = {"weak": "1.5:3:1:4", "medium": "3:5:1.5:6", "strong": "6:8:2:8"}
HEVC_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
AAC_RATES = (7350, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000)


def emit_event(emit, code, **data):
    if emit is not None:
        emit(Event(code, data))


def number(value, name, low, high=None, integer=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number or auto") from None
    if not math.isfinite(result) or result < low or (high is not None and result > high):
        raise ValueError(f"{name} is outside its supported range")
    if integer and not result.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(result) if integer else result


def validate_settings(settings):
    """Reject conflicting options and unsafe destinations before analysis."""
    source, output = Path(settings.input_path).resolve(), Path(settings.output_path).resolve()
    if not source.is_file():
        raise ValueError(f"Input file does not exist: {source}")
    if source == output:
        raise ValueError("Input and output must be different files")
    if output.suffix.lower() not in (".mkv", ".mp4"):
        raise ValueError("Output extension must be .mkv or .mp4")
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    for name, choices in {"codec": ("av1", "hevc"), "audio": ("remove", "keep"),
                          "denoise": ("auto", "off", *DENOISE),
                          "deinterlace": ("auto", "off", "on"), "field_order": ("auto", "tff", "bff"),
                          "cut_no_signal": ("auto", "off")}.items():
        if getattr(settings, name) not in choices:
            raise ValueError(f"Unsupported {name}: {getattr(settings, name)}")
    if settings.crf != "auto" and settings.bitrate != "auto":
        raise ValueError("Explicit CRF and bitrate cannot be used together")
    if settings.crf != "auto":
        number(settings.crf, "CRF", 0, 63 if settings.codec == "av1" else 51, integer=True)
    if settings.bitrate != "auto" and not re.fullmatch(r"(?:\d+(?:\.\d+)?)(?:[kKmM])?", str(settings.bitrate)):
        raise ValueError("Bitrate must be positive bits/s, or a value with k/M suffix")
    if settings.bitrate != "auto" and float(str(settings.bitrate).rstrip("kKmM")) <= 0:
        raise ValueError("Bitrate must be positive")
    if settings.preset != "auto":
        if settings.codec == "av1":
            number(settings.preset, "AV1 preset", 0, 13, integer=True)
        elif str(settings.preset) not in HEVC_PRESETS:
            raise ValueError("HEVC preset must be a supported x265 preset name")
    if settings.scale not in ("auto", "original") and not re.fullmatch(r"[1-9]\d*x[1-9]\d*", str(settings.scale)):
        raise ValueError("Scale must be auto, original or WIDTHxHEIGHT")
    if settings.scale not in ("auto", "original"):
        if any(int(n) % 2 for n in settings.scale.split("x")):
            raise ValueError("Output width and height must be even for yuv420p")
    if settings.no_signal_min_duration != "auto":
        number(settings.no_signal_min_duration, "Minimum no-signal duration", .001)
    number(settings.threads, "Threads", 1, 256, integer=True)
    destinations = [output]
    for index, path in enumerate((settings.report_path, settings.log_path)):
        if path is not None:
            destination = Path(path).resolve()
            if destination == source or destination in destinations:
                raise ValueError("Output, report and log must have distinct paths and must not replace input")
            # The CLI owns an exclusively opened log throughout analysis/planning.
            if (index == 0 and destination.exists()) or not destination.parent.is_dir():
                raise ValueError(f"Report/log destination must be new and its directory must exist: {destination}")
            destinations.append(destination)


def frame_metrics(source, times, tools, cancel=None, emit=None, stream_index=0):
    """Stream low-resolution metrics; no uncompressed movie is stored on disk."""
    command = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-threads", "2",
               "-i", str(source), "-map", f"0:{stream_index}", "-an", "-filter_threads", "2",
               "-vf", "scale=160:120:flags=bilinear,format=gray", "-fps_mode", "passthrough",
               "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    chunks = queue.Queue(maxsize=8)
    diagnostics = []
    stopped = threading.Event()

    def read_frames():
        try:
            while not stopped.is_set():
                data = process.stdout.read(160 * 120)
                if not data:
                    break
                while not stopped.is_set():
                    try:
                        chunks.put(data, timeout=.1)
                        break
                    except queue.Full:
                        continue
        finally:
            while not stopped.is_set():
                try:
                    chunks.put(None, timeout=.1)
                    break
                except queue.Full:
                    continue

    def read_errors():
        diagnostics.append(process.stderr.read().decode("utf-8", errors="replace"))

    reader = threading.Thread(target=read_frames, daemon=True)
    errors = threading.Thread(target=read_errors, daemon=True)
    reader.start()
    errors.start()
    rows, previous = [], None
    try:
        while True:
            check_cancel(cancel)
            try:
                data = chunks.get(timeout=.1)
            except queue.Empty:
                continue
            if data is None:
                break
            if len(data) != 160 * 120:
                raise RuntimeError("Incomplete video frame during snow analysis")
            frame = np.frombuffer(data, np.uint8).reshape(120, 160).astype(np.float32)[12:108, 16:144]
            coarse = frame.reshape(12, 8, 16, 8).mean(axis=(1, 3))
            mean, std = float(frame.mean()), float(frame.std())
            mad, corr = 0.0, 1.0
            if previous is not None:
                mad = float(np.abs(frame - previous).mean())
                p, q = frame - mean, previous - previous.mean()
                norm = float(np.sqrt((p * p).sum() * (q * q).sum()))
                corr = float((p * q).sum() / norm) if norm > 1e-6 else 0.0
            rows.append((mean, std, float(coarse.std()), mad, corr))
            previous = frame
            if len(rows) % 500 == 0:
                emit_event(emit, "analysis_progress", frames=len(rows), total_frames=len(times))
        process.wait()
        errors.join()
        if process.returncode or len(rows) != len(times) or "Error" in "".join(diagnostics):
            raise RuntimeError(f"Snow analysis decode failed: {''.join(diagnostics)[-2000:]}")
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=3)
        errors.join(timeout=3)
        process.stdout.close()
        process.stderr.close()
    return rows, command


def interlace_analysis(source, kept, tools, cancel=None, stream_index=0):
    """Sample useful regions and use conservative confidence for auto fields."""
    useful = [(a, b) for a, b in kept if b - a >= .5]
    total = sum(b - a for a, b in useful)
    samples = []
    for fraction in (.15, .5, .85):
        position = total * fraction
        for begin, end in useful:
            if position <= end - begin:
                start = min(begin + position, max(begin, end - .5))
                sample_end = end
                break
            position -= end - begin
        else:
            continue
        if any(abs(start - s["start"]) < .3 for s in samples):
            continue
        command = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-threads", "2", "-copyts", "-ss", str(start),
                   "-i", str(source), "-map", f"0:{stream_index}", "-an", "-frames:v", "150",
                   "-filter_threads", "2", "-vf", f"trim=start={start}:end={sample_end},idet", "-f", "null", "-"]
        _, diagnostic = capture(command, cancel)
        matches = re.findall(r"Multi frame detection: TFF:\s*(\d+) BFF:\s*(\d+) Progressive:\s*(\d+) Undetermined:\s*(\d+)", diagnostic)
        if not matches:
            raise RuntimeError("FFmpeg idet did not return classification counts")
        # FFmpeg may emit an empty initialization instance before the final counts.
        samples.append({"start": start, "counts": dict(zip(("tff", "bff", "progressive", "unknown"), map(int, matches[-1]))), "command": command})
    counts = {key: sum(s["counts"][key] for s in samples) for key in ("tff", "bff", "progressive", "unknown")}
    classified = counts["tff"] + counts["bff"] + counts["progressive"]
    confidence = classified / max(1, classified + counts["unknown"])
    interlaced = counts["tff"] + counts["bff"]
    parity_confidence = max(counts["tff"], counts["bff"]) / max(1, interlaced)
    if classified >= 50 and confidence >= .8 and interlaced / classified >= .8 and parity_confidence >= .8:
        decision = "interlaced"
    elif classified >= 50 and confidence >= .8 and counts["progressive"] / classified >= .8:
        decision = "progressive"
    else:
        decision = "unknown"
    field = "tff" if counts["tff"] >= counts["bff"] else "bff"
    return {"decision": decision, "field_order": field, "counts": counts, "samples": samples,
            "confidence": confidence, "parity_confidence": parity_confidence,
            "fallback": "preserve frames without deinterlace"}


def analyze(settings, emit=None, cancel=None):
    """Probe native timestamps and run only requested auto-analysis stages."""
    started = time.perf_counter()
    validate_settings(settings)
    tools = discover_tools(settings.ffmpeg_dir)
    encoder = "libsvtav1" if settings.codec == "av1" else "libx265"
    available_encoders = capture([tools["ffmpeg"], "-hide_banner", "-encoders"], cancel)[0].decode("utf-8", errors="replace")
    available_filters = capture([tools["ffmpeg"], "-hide_banner", "-filters"], cancel)[0].decode("utf-8", errors="replace")
    if encoder not in available_encoders:
        raise ValueError(f"FFmpeg does not provide required encoder {encoder}")
    required_filters = ["scale", "trim", "setpts", "concat"]
    if settings.deinterlace == "auto":
        required_filters += ["idet", "bwdif"]
    elif settings.deinterlace == "on":
        required_filters += ["bwdif"]
    if settings.denoise != "off":
        required_filters += ["hqdn3d"]
    if any(not re.search(rf"\b{f}\b", available_filters) for f in required_filters):
        raise ValueError("FFmpeg is missing required analysis/processing filters")
    source = Path(settings.input_path).resolve()
    emit_event(emit, "analysis_started", input=str(source))
    command = [tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]
    raw, _ = capture(command, cancel)
    probe = json.loads(raw)
    video = next((s for s in probe.get("streams", []) if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ValueError("Input does not contain a usable video stream")
    if not int(video.get("width", 0)) or not int(video.get("height", 0)):
        raise ValueError("Input video has invalid dimensions")
    if settings.audio == "keep" and any(s["codec_type"] == "audio" for s in probe["streams"]):
        audio_encoder = "aac" if Path(settings.output_path).suffix.lower() == ".mp4" else "flac"
        if not re.search(rf"\b{audio_encoder}\b", available_encoders):
            raise ValueError(f"FFmpeg is missing required audio encoder {audio_encoder}")
        audio_filters = ("aformat", "asettb", "atrim", "asetpts", "aresample", "apad", "anull")
        if any(not re.search(rf"\b{f}\b", available_filters) for f in audio_filters):
            raise ValueError("FFmpeg is missing required audio filters")
    frame_command = [tools["ffprobe"], "-v", "error", "-threads", "2", "-select_streams", str(video["index"]),
                     "-show_frames", "-show_entries", "frame=best_effort_timestamp,best_effort_timestamp_time", "-of", "json", str(source)]
    raw, diagnostic = capture(frame_command, cancel)
    if diagnostic.strip():
        raise ValueError(f"Input frame decode reported errors: {diagnostic[-2000:]}")
    frames = json.loads(raw)["frames"]
    if not frames or any("best_effort_timestamp" not in f for f in frames):
        raise ValueError("Input video has no reliable decoded frame timestamps")
    pts = tuple(int(f["best_effort_timestamp"]) for f in frames)
    tick = Fraction(video["time_base"])
    timestamps = tuple(float(p * tick) for p in pts)
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError("Input video timestamps must strictly increase; repair the source before processing")
    try:
        cadence = Fraction(video.get("avg_frame_rate", "0/1"))
    except (ZeroDivisionError, ValueError):
        cadence = Fraction(0)
    step = float(1 / cadence) if cadence > 0 else float(np.median(np.diff(timestamps))) if len(timestamps) > 1 else float(tick)
    duration = max(float(video.get("duration", 0)), timestamps[-1] + step)
    commands = [command, frame_command]
    versions = {name: capture([path, "-version"], cancel)[0].decode("utf-8", errors="replace").splitlines()[0] for name, path in tools.items()}
    metadata = {"commands": commands, "tool_versions": versions, "frame_step": step,
                "available_encoders": available_encoders, "available_filters": available_filters,
                "source_start": min(0.0, timestamps[0]), "source_end": duration,
                "source_fingerprint": {"bytes": source.stat().st_size, "mtime_ns": source.stat().st_mtime_ns},
                "snow_analysis": {"enabled": False}, "policy": POLICY_VERSION}
    cuts, warnings = (), []
    if settings.cut_no_signal == "auto":
        rows, metric_command = frame_metrics(source, timestamps, tools, cancel, emit, video["index"])
        commands.append(metric_command)
        minimum = 1.0 if settings.no_signal_min_duration == "auto" else float(settings.no_signal_min_duration)
        cuts, snow = snow_intervals(rows, timestamps, step, minimum=minimum)
        metadata["snow_analysis"] = {"enabled": True, **snow}
        only_snow = snow["candidate_frames"] / len(rows) >= .98 and snow["protected_structured_frames"] == 0
        metadata["snow_analysis"]["only_snow"] = only_snow
        warnings.append("Snow detection is calibrated on three samples; inspect the removal plan for other DVRs")
    kept = complement(cuts, metadata["source_start"], duration)
    fields = {"decision": "not_run", "samples": [], "field_order": "auto"}
    if settings.deinterlace == "auto":
        fields = interlace_analysis(source, kept, tools, cancel, video["index"])
        if fields["decision"] == "unknown":
            warnings.append("Interlace classification is uncertain; auto preserves frames without deinterlace")
    emit_event(emit, "analysis_completed", frames=len(pts), removed_intervals=cuts, interlace=fields["decision"])
    metadata["elapsed_seconds"] = time.perf_counter() - started
    return Analysis(input_path=source, probe=probe, timestamps=timestamps, frame_pts=pts,
                    cut_intervals=cuts, interlace=fields, diagnostics=tuple(warnings), tools=tools, metadata=metadata)


def build_plan(settings, analysis):
    """Resolve auto values once; execution and both interfaces use this plan."""
    validate_settings(settings)
    settings = replace(settings, input_path=Path(settings.input_path).resolve(), output_path=Path(settings.output_path).resolve())
    if settings.input_path != analysis.input_path:
        raise ValueError("Analysis belongs to a different input file")
    if analysis.metadata.get("snow_analysis", {}).get("only_snow") and settings.cut_no_signal == "auto":
        raise ValueError("No confidently useful scene found: input appears to contain only snow. Use --cut-no-signal off to retain it")
    video = next(s for s in analysis.probe["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic"))
    width, height = int(video["width"]), int(video["height"])
    reasons = {"scale": "Preserve source dimensions; automatic downscale has not been established for all DVRs"}
    if video.get("sample_aspect_ratio", "0:1") in ("0:1", "N/A"):
        reasons["aspect_ratio"] = "Source SAR is unspecified; use its raster aspect with square pixels, without inferring a helmet display mode"
    if settings.scale not in ("auto", "original"):
        width, height = map(int, settings.scale.split("x"))
        reasons["scale"] = "User selected explicit dimensions; preserve display aspect through sample aspect ratio"
    if width % 2 or height % 2:
        raise ValueError("Source dimensions are odd; supply an even --scale WIDTHxHEIGHT for yuv420p")
    denoise = "medium" if settings.denoise == "auto" else settings.denoise
    reasons["denoise"] = "Initial research policy: modest HQDN3D medium; no separate strength analysis" if settings.denoise == "auto" else "Explicit user selection"
    bitrate = None if settings.bitrate == "auto" else str(settings.bitrate)
    crf = None if bitrate else (48 if settings.codec == "av1" else 32) if settings.crf == "auto" else int(settings.crf)
    reasons["rate_control"] = "Explicit target bitrate" if bitrate else "Initial research CRF candidate" if settings.crf == "auto" else "Explicit CRF"
    preset = (6 if settings.codec == "av1" else "medium") if settings.preset == "auto" else int(settings.preset) if settings.codec == "av1" else settings.preset
    deinterlace = settings.deinterlace == "on" or (settings.deinterlace == "auto" and analysis.interlace["decision"] == "interlaced")
    field = settings.field_order if settings.field_order != "auto" else analysis.interlace.get("field_order", "auto") if deinterlace else "auto"
    reasons["deinterlace"] = f"Auto idet decision: {analysis.interlace['decision']}" if settings.deinterlace == "auto" else "Explicit user selection; idet skipped"
    cuts = analysis.cut_intervals if settings.cut_no_signal == "auto" else ()
    start, end = analysis.metadata["source_start"], analysis.metadata["source_end"]
    kept = complement(cuts, start, end)
    if not kept:
        raise ValueError("No video remains after no-signal removal")
    encoder = "libsvtav1" if settings.codec == "av1" else "libx265"
    encoders = analysis.metadata.get("available_encoders", "")
    if encoder not in encoders:
        raise ValueError(f"FFmpeg does not provide required encoder {encoder}")
    filters = analysis.metadata.get("available_filters", "")
    required = ["scale", "trim", "setpts", "concat", "settb", "null"] + (["hqdn3d"] if denoise != "off" else []) + (["bwdif"] if deinterlace else [])
    if any(not re.search(rf"\b{f}\b", filters) for f in required):
        raise ValueError("FFmpeg is missing required video filters")
    audio = settings.audio
    if audio == "keep" and not any(s["codec_type"] == "audio" for s in analysis.probe["streams"]):
        audio = "remove"
        reasons["audio"] = "No audio stream in input; output will contain video only"
    else:
        reasons["audio"] = "Remove audio by default" if audio == "remove" else "Keep audio with the same source interval mapping"
    audio_rate = None
    if audio == "keep":
        audio_stream = next(stream for stream in analysis.probe["streams"] if stream["codec_type"] == "audio")
        source_rate = int(audio_stream["sample_rate"])
        audio_rate = min(AAC_RATES, key=lambda rate: abs(rate - source_rate)) if settings.output_path.suffix.lower() == ".mp4" else source_rate
        if audio_rate != source_rate:
            reasons["audio"] += f"; resample from {source_rate} Hz to AAC-supported {audio_rate} Hz after source-clock trimming"
    reasons["cut_no_signal"] = "Conservative native-timestamp snow policy; blue-screen removal disabled" if settings.cut_no_signal == "auto" else "Explicit off; snow analysis skipped"
    mapping, position = [], 0.0
    for begin, finish in kept:
        mapping.append({"source_start": begin, "source_end": finish, "output_start": position, "output_end": position + finish - begin})
        position += finish - begin
    selected = {"codec": settings.codec, "encoder": encoder, "crf": crf, "bitrate": bitrate, "preset": preset,
                "width": width, "height": height, "denoise": DENOISE.get(denoise), "denoise_level": denoise,
                "deinterlace": deinterlace, "field_order": field, "audio": audio, "audio_sample_rate": audio_rate,
                "threads": int(settings.threads),
                "in_range": "full" if video.get("color_range") == "pc" or video.get("pix_fmt", "").startswith("yuvj") else "limited" if video.get("color_range") == "tv" else "auto",
                "out_range": "limited", "pixel_format": "yuv420p"}
    return Plan(settings=settings, analysis=analysis, selected=selected, keep_intervals=kept,
                removed_intervals=cuts, mapping=tuple(mapping), reasons=reasons, policy_version="v1-research-defaults-1.0")
