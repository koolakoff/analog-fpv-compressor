"""Execute resolved plans with exact source-time cuts and verified publication."""

from fractions import Fraction
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import time
import uuid
import tempfile
from collections import deque

from .models import Event, Result, to_dict
from .progress import emit_progress


class ProcessingError(RuntimeError):
    """A processing or output verification failure."""


class ProcessingCancelled(ProcessingError):
    """The user cancelled an active job."""


def _emit(sink, code, **data):
    if sink:
        sink(Event(code, data))


def _cancelled(cancel):
    return bool(cancel and cancel.is_cancelled())


def _stream(plan, kind):
    return next((s for s in plan.analysis.probe["streams"] if s["codec_type"] == kind
                 and not s.get("disposition", {}).get("attached_pic")), None)


def _fraction(value):
    return Fraction(str(value))


def _source_segments(plan):
    """Recover integer source ticks before applying half-open interval bounds."""
    video = _stream(plan, "video")
    tick = Fraction(video["time_base"])
    pts = getattr(plan.analysis, "frame_pts", ()) or tuple(
        round(_fraction(t) / tick) for t in plan.analysis.timestamps)
    offset = Fraction(0)
    segments = []
    for begin, end in plan.keep_intervals:
        begin, end = _fraction(begin), _fraction(end)
        if end <= begin or segments and begin < segments[-1]["end"]:
            raise ProcessingError("Keep intervals must be ordered, disjoint half-open intervals.")
        low, high = math.ceil(begin / tick), math.ceil(end / tick)
        frames = tuple(p for p in pts if low <= p < high)
        if not frames:
            raise ProcessingError("A retained interval contains no video frames.")
        segments.append({"begin": begin, "end": end, "low": low, "high": high,
                         "frames": frames, "offset": offset, "tick": tick})
        offset += end - begin
    if not segments:
        raise ProcessingError("No useful video remains after cutting; no output was created.")
    return segments


def expected_timestamps(plan):
    """Map source frame and optional field timestamps onto fixed interval lengths."""
    result = []
    video = _stream(plan, "video")
    nominal = _fraction(plan.analysis.metadata.get("frame_step", 0))
    if nominal <= 0:
        nominal = Fraction(1, 1) / Fraction(video.get("avg_frame_rate", "30/1"))
    for segment in _source_segments(plan):
        frames, tick = segment["frames"], segment["tick"]
        for index, pts in enumerate(frames):
            value = pts * tick - segment["begin"] + segment["offset"]
            result.append(float(value))
            if plan.selected["deinterlace"]:
                # bwdif places the second field halfway towards the next frame;
                # EOF extrapolates the preceding interval.
                delta = ((frames[index + 1] - pts) * tick if index + 1 < len(frames)
                         else (pts - frames[index - 1]) * tick if index else nominal)
                result.append(float(value + delta / 2))
    return result


def build_command(plan, temporary_output):
    """Build a single encode with separate temporal state for retained segments.

Concat's guessed terminal duration is corrected at known segment frame indices.
The correction retains every internal gap and uses the same interval clock as
audio, including the offset of the first actual video frame from its boundary.
"""
    selected = plan.selected
    segments = _source_segments(plan)
    video, audio = _stream(plan, "video"), _stream(plan, "audio")
    video_input = f"[0:{video.get('index', 0)}]"
    audio_input = f"[0:{audio.get('index', 1)}]" if audio else None
    threads = int(selected.get("threads", plan.settings.threads))
    filter_threads = min(2, max(1, threads))
    command = [str(plan.analysis.tools["ffmpeg"]), "-hide_banner", "-nostdin", "-n",
               "-threads", str(filter_threads), "-copyts", "-i", str(plan.settings.input_path)]
    filters, outputs = [], []
    origins, first_index = [], 0
    for index, segment in enumerate(segments):
        chain = [f"trim=start_pts={segment['low']}:end_pts={segment['high']}", "setpts=PTS-STARTPTS"]
        if selected["deinterlace"]:
            chain.append(f"bwdif=mode=send_field:parity={selected['field_order']}:deint=all")
        if selected.get("denoise"):
            chain.append(f"hqdn3d={selected['denoise']}")
        source_range = selected.get("in_range") or ("full" if video.get("color_range") == "pc" or video.get("pix_fmt", "").startswith("yuvj") else "limited" if video.get("color_range") == "tv" else "auto")
        chain += [f"scale={selected['width']}:{selected['height']}:flags=lanczos:in_range={source_range}:out_range=limited",
                  "format=yuv420p"]
        sar = video.get("sample_aspect_ratio")
        known_sar = bool(sar and sar not in ("0:1", "N/A"))
        source_sar = Fraction(sar.replace(":", "/")) if known_sar else Fraction(1)
        # Missing SAR means square pixels, not permission to reshape the scene.
        ratio = source_sar * video["width"] * selected["height"] / (video["height"] * selected["width"])
        if known_sar or ratio != 1:
            chain.append(f"setsar={ratio.numerator}/{ratio.denominator}:max=1000000")
        filters.append(f"{video_input}{','.join(chain)}[v{index}]")
        outputs.append(f"[v{index}]")
        target = segment["frames"][0] * segment["tick"] - segment["begin"] + segment["offset"]
        origins.append((first_index, float(target)))
        first_index += len(segment["frames"]) * (2 if selected["deinterlace"] else 1)
    if len(outputs) == 1:
        filters.append(f"{outputs[0]}null[joined]")
    else:
        filters.append("".join(outputs) + f"concat=n={len(outputs)}:v=1:a=0[joined]")
    correction = "ld(0)"
    for index, target in reversed(origins):
        correction = f"if(eq(N,{index}),st(0,PTS-{target:.12f}/TB),{correction})"
    filters.append(f"[joined]settb=AVTB,setpts='PTS-({correction})'[video]")
    keep_audio = selected["audio"] == "keep" and audio is not None
    if keep_audio:
        rate = int(audio["sample_rate"])
        labels = []
        for index, segment in enumerate(segments):
            low, high = math.ceil(segment["begin"] * rate), math.ceil(segment["end"] * rate)
            # Distribute fractional rounding over the whole output sample clock.
            output_low = math.ceil(segment["offset"] * rate)
            output_high = math.ceil((segment["offset"] + segment["end"] - segment["begin"]) * rate)
            length = output_high - output_low
            filters.append(f"{audio_input}aformat=sample_rates={rate},asettb=1/{rate},atrim=start_pts={low}:end_pts={high},"
                           f"asetpts=PTS-{segment['begin'] * rate},aresample={rate}:async=1:first_pts=0,"
                           f"apad=whole_len={length},atrim=end_sample={length}[a{index}]")
            labels.append(f"[a{index}]")
        # Source-clock trim/pad lengths must not be negotiated at the AAC rate.
        # Only convert after every retained interval has its exact source length.
        filters.append("".join(labels) + (f"concat=n={len(labels)}:v=0:a=1[kept_audio]" if len(labels) > 1 else "anull[kept_audio]"))
        target_rate = selected.get("audio_sample_rate") or rate
        filters.append(f"[kept_audio]aresample={target_rate}[audio]")
    command += ["-filter_complex_threads", str(filter_threads), "-filter_complex", ";".join(filters),
                "-map", "[video]", "-fps_mode", "passthrough", "-enc_time_base:v", "filter",
                "-color_range", "tv", "-c:v", "libsvtav1" if selected["codec"] == "av1" else "libx265",
                "-preset", str(selected["preset"])]
    if selected.get("bitrate"):
        command += ["-b:v", str(selected["bitrate"])]
    else:
        command += ["-crf", str(selected["crf"])]
    if selected["codec"] == "av1":
        command += ["-svtav1-params", f"lp={threads}:film-grain=0:film-grain-denoise=0"]
    else:
        command += ["-x265-params", f"pools={threads}:frame-threads=1"]
    if keep_audio:
        command += ["-map", "[audio]", "-c:a", "aac" if plan.settings.output_path.suffix.lower() == ".mp4" else "flac"]
        if selected.get("audio_sample_rate"):
            command += ["-ar", str(selected["audio_sample_rate"])]
    else:
        command += ["-an"]
    if plan.settings.output_path.suffix.lower() == ".mp4":
        command += ["-movflags", "+faststart"]
    command += ["-avoid_negative_ts", "disabled", "-progress", "pipe:1", "-nostats", str(temporary_output)]
    return command


def _run(command, diagnostics, emit=None, cancel=None, total=None, total_frames=None):
    """Drain stdout while polling cancellation; keep raw stderr out of events."""
    if _cancelled(cancel):
        raise ProcessingCancelled("Processing cancelled.")
    diagnostics.write(json.dumps({"argv": command}) + "\n")
    diagnostics.flush()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=diagnostics,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    queue = Queue()

    def read():
        try:
            for line in process.stdout:
                queue.put(line)
        finally:
            queue.put(None)

    reader = Thread(target=read, daemon=True)
    reader.start()
    lines, last_progress, encoded_frames = [], 0.0, 0
    try:
        while True:
            if _cancelled(cancel):
                raise ProcessingCancelled("Processing cancelled.")
            try:
                line = queue.get(timeout=.1)
            except Empty:
                continue
            if line is None:
                break
            lines.append(line)
            if line.startswith("frame="):
                try:
                    encoded_frames = int(line.split("=", 1)[1])
                except ValueError:
                    pass
            if line.startswith("out_time_us=") and time.monotonic() - last_progress >= .5:
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    # Some muxers report a sentinel timestamp while flushing.
                    if seconds < 0 or (total is not None and seconds > total + 1):
                        seconds = None
                    emit_progress(emit, "encode", encoded_frames if total_frames else seconds,
                                  total_frames or total, unit="frames" if total_frames else "seconds",
                                  seconds=seconds, frames=encoded_frames, total_frames=total_frames)
                    last_progress = time.monotonic()
                except ValueError:
                    pass
        while True:
            if _cancelled(cancel):
                raise ProcessingCancelled("Processing cancelled.")
            try:
                code = process.wait(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if code:
            raise ProcessingError(f"FFmpeg tool exited with status {code}.\n{diagnostic_tail(diagnostics)}")
        return "".join(lines)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join(timeout=3)
        process.stdout.close()


def diagnostic_tail(stream):
    """Keep a bounded native diagnostic tail rather than a separate persistent file."""
    stream.flush()
    position = stream.tell()
    stream.seek(0)
    tail = "".join(deque(stream, maxlen=80))[-65536:]
    stream.seek(position)
    return tail


def validate_output(plan, path, diagnostics, cancel=None):
    """Decode all streams and verify counts, source mapping, range and audio clock."""
    ffprobe = str(plan.analysis.tools["ffprobe"])
    raw = _run([ffprobe, "-v", "error", "-threads", str(plan.settings.threads), "-show_streams",
                "-show_format", "-show_frames", "-show_entries",
                "frame=media_type,best_effort_timestamp_time,nb_samples", "-of", "json", str(path)], diagnostics, cancel=cancel)
    probe = json.loads(raw)
    frames = [f for f in probe["frames"] if f["media_type"] == "video"]
    actual = [float(f["best_effort_timestamp_time"]) for f in frames]
    expected = expected_timestamps(plan)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    # MKV stores milliseconds; MP4 generally stores a finer video time base.
    tolerance = max(.0011, float(Fraction(video["time_base"])) * 1.1)
    if len(actual) != len(expected):
        raise ProcessingError(f"Output frame count mismatch: expected {len(expected)}, got {len(actual)}.")
    if any(b <= a for a, b in zip(actual, actual[1:])):
        raise ProcessingError("Output video timestamps are not strictly increasing.")
    max_error = max((abs(a - b) for a, b in zip(actual, expected)), default=0)
    if max_error > tolerance:
        raise ProcessingError(f"Output timing differs from the resolved source mapping by {max_error:.6f} seconds.")
    if [video["width"], video["height"]] != [plan.selected["width"], plan.selected["height"]] or video.get("color_range") != "tv":
        raise ProcessingError("Output dimensions or color range do not match the plan.")
    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    should_have_audio = plan.selected["audio"] == "keep" and _stream(plan, "audio") is not None
    if bool(audio) != should_have_audio:
        raise ProcessingError("Output audio presence does not match the plan.")
    audio_report = None
    if audio:
        decoded = [f for f in probe["frames"] if f["media_type"] == "audio"]
        rate = int(audio["sample_rate"])
        if plan.selected.get("audio_sample_rate") and rate != plan.selected["audio_sample_rate"]:
            raise ProcessingError("Output audio sample rate does not match the resolved plan.")
        total = sum(int(f["nb_samples"]) for f in decoded)
        desired = sum((_fraction(e) - _fraction(b) for b, e in plan.keep_intervals), Fraction())
        target_samples = math.ceil(desired * rate)
        # AAC decoders may expose encoder padding; lossless FLAC is exact.
        padding = 2048 if audio["codec_name"] == "aac" else 0
        if not decoded or abs(total - target_samples) > padding:
            raise ProcessingError("Output audio sample count does not match the common interval clock.")
        first = float(decoded[0]["best_effort_timestamp_time"])
        end = float(decoded[-1]["best_effort_timestamp_time"]) + int(decoded[-1]["nb_samples"]) / rate
        consumed, clock_error = 0, 0.0
        for frame in decoded:
            clock_error = max(clock_error, abs(float(frame["best_effort_timestamp_time"]) - first - consumed / rate))
            consumed += int(frame["nb_samples"])
        if clock_error > tolerance:
            raise ProcessingError("Output audio timestamps depart from the continuous sample clock.")
        if abs(first) > max(tolerance, 1024 / rate if padding else 0) or abs(end - float(desired)) > max(tolerance, padding / rate):
            raise ProcessingError("Output audio endpoints do not match the common interval clock.")
        audio_report = {"codec": audio["codec_name"], "sample_rate": rate, "decoded_samples": total,
                        "target_samples": target_samples, "padding_tolerance_samples": padding,
                        "first_seconds": first, "end_seconds": end,
                        "maximum_sample_clock_error_seconds": clock_error}
    desired_duration = sum(float(_fraction(e) - _fraction(b)) for b, e in plan.keep_intervals)
    video_source = _stream(plan, "video")
    frame_tolerance = float(Fraction(1, 1) / Fraction(video_source.get("avg_frame_rate", "30/1"))) + tolerance
    duration = float(probe["format"]["duration"])
    duration_tolerance = max(frame_tolerance, (audio_report["padding_tolerance_samples"] / audio_report["sample_rate"]) if audio_report else 0)
    # Silent VFR output ends at the last retained frame, not at a cut boundary
    # inside a missing-frame gap. Audio, when present, spans the interval clock.
    nominal_step = float(plan.analysis.metadata.get("frame_step") or
                         (Fraction(1, 1) / Fraction(video_source.get("avg_frame_rate", "30/1"))))
    video_end = expected[-1] + nominal_step / (2 if plan.selected["deinterlace"] else 1)
    expected_duration = max(video_end, desired_duration) if audio_report else video_end
    if abs(duration - expected_duration) > duration_tolerance:
        raise ProcessingError(f"Output duration differs from the expected stream endpoint: "
                              f"expected {expected_duration:.6f}s, got {duration:.6f}s "
                              f"(tolerance {duration_tolerance:.6f}s).")
    _run([str(plan.analysis.tools["ffmpeg"]), "-hide_banner", "-v", "error", "-xerror",
          "-threads", str(plan.settings.threads), "-i", str(path), "-f", "null", "-"], diagnostics, cancel=cancel)
    return {"decoded_frames": len(actual), "expected_frames": len(expected), "maximum_timestamp_error_seconds": max_error,
            "timestamp_tolerance_seconds": tolerance, "full_decode_passed": True,
            "audio": audio_report, "duration_seconds": duration,
            "planned_duration_seconds": desired_duration, "expected_stream_duration_seconds": expected_duration,
            "duration_tolerance_seconds": duration_tolerance}


def _publish(source, destination):
    """Publish atomically without replacing a destination created by another job."""
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise ProcessingError(f"Destination already exists: {destination}") from exc
    except OSError as exc:
        raise ProcessingError(f"Could not atomically publish {destination}: {exc}") from exc
    source.unlink()


def _check_source(plan):
    """Reject a stale plan before consuming or publishing any input-derived data."""
    source = Path(plan.settings.input_path).resolve()
    if source != Path(plan.analysis.input_path).resolve():
        raise ProcessingError("The plan input differs from the analyzed input.")
    expected = plan.analysis.metadata.get("source_fingerprint")
    if expected is None:
        raise ProcessingError("The plan lacks a source fingerprint; analyze the input again.")
    stat = source.stat()
    if stat.st_size != expected["bytes"] or stat.st_mtime_ns != expected["mtime_ns"]:
        raise ProcessingError("The input changed after analysis; analyze it again.")


def execute(plan, emit=None, cancel=None):
    """Encode and publish verified media; return diagnostics in memory/events."""
    output = Path(plan.settings.output_path).resolve()
    source = Path(plan.settings.input_path).resolve()
    if output == source:
        raise ProcessingError("Input and output paths must be distinct.")
    if output.suffix.lower() not in (".mkv", ".mp4"):
        raise ProcessingError("Output container must be MKV or MP4.")
    if output.exists():
        raise ProcessingError("Output already exists; select a new destination.")
    _check_source(plan)
    segments = _source_segments(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    temporary = output.with_name(f".{output.stem}.{job_id}.partial{output.suffix}")
    started = time.perf_counter()
    warnings = []
    if plan.selected["audio"] == "keep" and _stream(plan, "audio") is None:
        warnings.append("Input has no audio; the output contains video only.")
        _emit(emit, "warning", message=warnings[-1])
    try:
        command = build_command(plan, temporary)
        _emit(emit, "processing_started", output_path=str(output), command=command, selected=plan.selected)
        frame_count = len(expected_timestamps(plan))
        emit_progress(emit, "encode", 0, frame_count, state="started", unit="frames",
                      frames=0, total_frames=frame_count, seconds=0.)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as diagnostic:
            _run(command, diagnostic, emit, cancel, total=float(sum(s["end"] - s["begin"] for s in segments)),
                 total_frames=frame_count)
            emit_progress(emit, "encode", frame_count, frame_count, state="completed", unit="frames",
                          frames=frame_count, total_frames=frame_count, seconds=None)
            _emit(emit, "validation_started")
            emit_progress(emit, "validate", state="started")
            try:
                validation = validate_output(plan, temporary, diagnostic, cancel)
            except Exception:
                _emit(emit, "diagnostic.error", native_output=diagnostic_tail(diagnostic))
                raise
            emit_progress(emit, "validate", state="completed")
        if _cancelled(cancel):
            raise ProcessingCancelled("Processing cancelled before publication.")
        _check_source(plan)
        emit_progress(emit, "publish", state="started")
        report = {"schema_version": 1, "status": "complete", "plan": to_dict(plan),
                  "command": command, "validation": validation, "warnings": warnings,
                  "output_path": str(output),
                  "elapsed_seconds": time.perf_counter() - started,
                  "analysis_elapsed_seconds": plan.analysis.metadata.get("elapsed_seconds"),
                  "bytes": temporary.stat().st_size}
        _publish(temporary, output)
        result = Result(output, None, report["bytes"], validation["duration_seconds"], report)
        emit_progress(emit, "publish", state="completed")
        _emit(emit, "processing_completed", output_path=str(output), report=report,
              bytes=result.bytes, duration_seconds=result.duration_seconds,
              validation=validation, elapsed_seconds=report["elapsed_seconds"])
        return result
    finally:
        temporary.unlink(missing_ok=True)


def process(plan, on_event=None, cancel=None):
    """Expose the controller-facing callback spelling without duplicating logic."""
    return execute(plan, emit=on_event, cancel=cancel)
