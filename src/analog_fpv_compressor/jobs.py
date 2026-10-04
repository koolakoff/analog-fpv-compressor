"""Resolve batch inputs, output names and independently encoded flight plans."""

from dataclasses import replace
from fractions import Fraction
import glob
import json
import math
import os
from pathlib import Path
import uuid

from .models import Settings


def expand_inputs(specifications):
    """Expand quoted globs while preserving explicit order and deduplicating paths."""
    if isinstance(specifications, (str, Path)):
        specifications = (specifications,)
    resolved, seen = [], set()
    for specification in specifications:
        literal = Path(specification).expanduser()
        if literal.is_file():
            matches = [literal]
        elif glob.has_magic(str(literal)):
            matches = sorted((Path(path) for path in glob.glob(str(literal), recursive=True)
                              if Path(path).is_file()), key=lambda path: str(path).casefold())
            if not matches:
                raise ValueError(f"No input files match: {specification}")
        else:
            raise ValueError(f"Input file does not exist: {specification}")
        for match in matches:
            source = Path(os.path.abspath(match))
            identity = source.resolve()
            if identity not in seen:
                resolved.append(source)
                seen.add(identity)
    if not resolved:
        raise ValueError("At least one input file is required")
    return tuple(resolved)


def destination_paths(settings):
    """Return media paths; the application session owns all diagnostics."""
    output = Path(settings.output_path).resolve()
    return (output,)


def validate_jobs(jobs, protected_paths=()):
    """Reject collisions across sources, outputs and sidecars before encoding."""
    from . import controller
    jobs = tuple(jobs)
    forbidden = {Path(path).resolve() for path in protected_paths}
    forbidden.update(Path(job.input_path).resolve() for job in jobs)
    reserved = set()
    for job in jobs:
        controller.validate_settings(job)
        for path in destination_paths(job):
            if path in forbidden or path in reserved:
                raise ValueError(f"Destination collides with an input or another job: {path}")
            if path.exists():
                raise FileExistsError(f"Refusing to overwrite existing file: {path}")
            if not path.parent.is_dir():
                raise ValueError(f"Destination directory does not exist: {path.parent}")
            reserved.add(path)


def make_jobs(specifications, *, output_path=None, output_dir=None, output_suffix=None,
              output_format=None, **options):
    """Create shared per-input settings with safe automatic output naming."""
    inputs = expand_inputs(specifications)
    if output_path is not None:
        if len(inputs) != 1:
            raise ValueError("--output accepts one input only; use --output-dir for multiple inputs")
        if output_dir is not None or output_suffix is not None:
            raise ValueError("--output cannot be combined with --output-dir or --output-suffix")
        if output_format is not None and Path(output_path).suffix.lower() != "." + output_format:
            raise ValueError("--format conflicts with the explicit output extension")
    if len(inputs) > 1 and (options.get("report_path") is not None or options.get("log_path") is not None):
        raise ValueError("--report and --log-file accept one input only; batch sidecars are named per output")
    suffix = "_converted" if output_suffix is None else output_suffix
    if not isinstance(suffix, str) or any(character in '/\\:*?"<>|' or ord(character) < 32 for character in suffix):
        raise ValueError("Output suffix must be a filename suffix without path separators or reserved characters")
    extension = output_format or "mkv"
    if extension not in ("mkv", "mp4"):
        raise ValueError("Output format must be mkv or mp4")
    directory = Path(output_dir).expanduser().resolve() if output_dir is not None else None
    if directory is not None and directory.exists() and not directory.is_dir():
        raise ValueError(f"Output directory is not a directory: {directory}")
    jobs = []
    for source in inputs:
        output = Path(output_path).expanduser().resolve() if output_path is not None else (
            (directory or source.parent) / (source.stem + suffix + "." + extension))
        requested = dict(options)
        jobs.append(Settings(source, output, **requested))
    # Explicit --output-dir authorizes creation; explicit --output retains the
    # existing single-file rule that its parent must already exist.
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    validate_jobs(jobs)
    return tuple(jobs)


def plan_outputs(plan, split_flights=False):
    """Split the existing snow-removal plan without running detection again."""
    if not split_flights:
        return (plan,)
    if plan.settings.cut_no_signal == "off":
        raise ValueError("--split-flights requires automatic snow detection; remove --cut-no-signal off")
    video = next(stream for stream in plan.analysis.probe["streams"]
                 if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic"))
    tick = Fraction(video["time_base"])
    pts = plan.analysis.frame_pts or tuple(round(Fraction(str(time)) / tick) for time in plan.analysis.timestamps)
    intervals = tuple((start, end) for start, end in plan.keep_intervals
                      if any(math.ceil(Fraction(str(start)) / tick) <= point < math.ceil(Fraction(str(end)) / tick)
                             for point in pts))
    if not intervals:
        raise ValueError("No flight interval contains video frames")
    plans = []
    for index, (start, end) in enumerate(intervals, 1):
        base = Path(plan.settings.output_path)
        output = base.with_name(f"{base.stem}_{index}{base.suffix}")
        settings = replace(plan.settings, output_path=output,
                           report_path=None, log_path=None)
        reasons = {**plan.reasons, "split_flights": {
            "index": index, "count": len(intervals), "source_interval": [start, end],
            "other_useful_intervals": "Written to separate numbered outputs, not classified as noise",
            "empty_intervals_skipped": len(plan.keep_intervals) - len(intervals)}}
        plans.append(replace(plan, settings=settings, keep_intervals=((start, end),),
                             mapping=({"source_start": start, "source_end": end,
                                       "output_start": 0., "output_end": end - start},), reasons=reasons))
    return tuple(plans)


def write_manifest(path, content):
    """Atomically publish a new plan/summary without overwriting an existing file."""
    path = Path(path)
    encoded = json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
