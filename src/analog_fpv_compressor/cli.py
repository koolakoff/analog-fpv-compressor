"""English CLI over the shared analysis, planning, and execution contracts."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys

from . import __version__
from .models import CancelToken, Event, Settings, to_dict


def auto_integer(value):
    """Parse an integer or the explicit auto token; validate ranges in core."""
    if value == "auto":
        return value
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected an integer or auto") from error


def auto_duration(value):
    if value == "auto":
        return value
    try:
        return float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected seconds or auto") from error


def create_parser():
    parser = argparse.ArgumentParser(description="Compress analog FPV DVR video while preserving flight information.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-i", "--input", required=True, nargs="+", action="extend",
                        help="Input DVR files or quoted glob patterns; repeated -i is supported")
    parser.add_argument("-o", "--output", type=Path, help="Explicit output filename for one input only")
    parser.add_argument("--output-dir", type=Path, help="Output directory, created if needed; default beside each input")
    parser.add_argument("--output-suffix", help="Filename suffix; default _converted")
    parser.add_argument("--format", choices=["mkv", "mp4"], help="Automatic output container; default mkv")
    parser.add_argument("--split-flights", action="store_true", help="Write numbered outputs separated by confirmed snow")
    parser.add_argument("--audio", choices=["remove", "keep"], default="remove", help="Remove audio by default, or preserve it")
    parser.add_argument("--codec", choices=["av1", "hevc"], default="av1")
    parser.add_argument("--crf", type=auto_integer, default="auto")
    parser.add_argument("--bitrate", default="auto", help="Video target bitrate, for example 500k, or auto")
    parser.add_argument("--preset", default="auto", help="auto, numeric AV1 preset, or named HEVC preset")
    parser.add_argument("--scale", default="auto", help="auto, original, or WIDTHxHEIGHT")
    parser.add_argument("--denoise", choices=["auto", "off", "weak", "medium", "strong"], default="auto")
    parser.add_argument("--deinterlace", choices=["auto", "off", "on"], default="auto")
    parser.add_argument("--field-order", choices=["auto", "tff", "bff"], default="auto")
    parser.add_argument("--cut-no-signal", choices=["auto", "off"], default="auto")
    parser.add_argument("--no-signal-min-duration", type=auto_duration, default="auto")
    parser.add_argument("--ffmpeg-dir", type=Path, help="Directory containing ffmpeg and ffprobe; otherwise use PATH")
    parser.add_argument("--threads", type=int, default=4, help="Requested processing thread budget")
    parser.add_argument("--report", type=Path, help="New JSON report path; default OUTPUT.report.json")
    parser.add_argument("--log-file", type=Path, help="New English text log; default OUTPUT.log")
    parser.add_argument("--events-jsonl", action="store_true", help="Write versioned machine events to stdout")
    parser.add_argument("--analyze-only", action="store_true", help="Save the resolved plan without encoding")
    return parser


def processing_options(args):
    """Use the same option names for CLI settings and shared batch resolution."""
    return {"crf": args.crf, "bitrate": args.bitrate, "preset": args.preset, "scale": args.scale,
            "denoise": args.denoise, "deinterlace": args.deinterlace, "field_order": args.field_order,
            "cut_no_signal": args.cut_no_signal, "no_signal_min_duration": args.no_signal_min_duration,
            "audio": args.audio, "codec": args.codec, "ffmpeg_dir": args.ffmpeg_dir, "threads": args.threads}


def settings_from_args(args):
    """Convert one literal input without touching the filesystem; batch uses make_jobs."""
    if len(args.input) != 1:
        raise ValueError("Use make_jobs for multiple inputs")
    source = Path(args.input[0])
    output = args.output or (args.output_dir or source.parent) / (
        source.stem + (args.output_suffix if args.output_suffix is not None else "_converted") + "." + (args.format or "mkv"))
    return Settings(source, output, **processing_options(args),
                    report_path=args.report or Path(str(output) + ".report.json"),
                    log_path=args.log_file or Path(str(output) + ".log"))


class EventLogger:
    """Persist the same events as English text and versioned JSON Lines."""

    def __init__(self, path, machine_stdout=False, echo=True):
        self.path = Path(path)
        self.json_path = Path(str(path) + ".jsonl")
        self.machine_stdout = machine_stdout
        self.echo = echo
        self.text_file = None
        self.json_file = None

    def __enter__(self):
        self.text_file = self.path.open("x", encoding="utf-8")
        try:
            self.json_file = self.json_path.open("x", encoding="utf-8")
        except BaseException:
            self.text_file.close()
            self.path.unlink()
            raise
        return self

    def __exit__(self, *args):
        self.text_file.close()
        self.json_file.close()

    def __call__(self, event):
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        record = {"schema_version": 1, "timestamp": timestamp, "code": event.code, "data": to_dict(event.data)}
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        level = "ERROR" if "failed" in event.code else "WARNING" if "warning" in event.code else "INFO"
        message = f"{timestamp} {level} {event.code}: {json.dumps(record['data'], ensure_ascii=False)}"
        if event.code == "plan.resolved":
            intervals = event.data.get("removed_intervals", ())
            readable = [f"[{format_timestamp(start)}, {format_timestamp(end)})" for start, end in intervals]
            message += "; removed source intervals: " + (", ".join(readable) if readable else "none")
        self.text_file.write(message + "\n")
        self.text_file.flush()
        self.json_file.write(encoded + "\n")
        self.json_file.flush()
        if self.echo:
            print(message, file=sys.stderr, flush=True)
        if self.machine_stdout:
            print(encoded, flush=True)


def format_timestamp(seconds):
    """Format source timestamps with millisecond precision."""
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{fraction:03d}"


def split_summary(plan, plans, results, status, message=None):
    """Describe numbered outputs without calling other useful scenes removed noise."""
    completed = {result.output_path: result for result in results}
    outputs = []
    for index, part in enumerate(plans, 1):
        result = completed.get(part.settings.output_path)
        output = {"index": index, "output_path": str(part.settings.output_path),
                  "report_path": str(part.settings.report_path), "log_path": str(part.settings.log_path),
                  "keep_intervals": part.keep_intervals, "mapping": part.mapping,
                  "status": "complete" if result else "planned"}
        if result:
            output.update(bytes=result.bytes, duration_seconds=result.duration_seconds)
        outputs.append(output)
    return {"schema_version": 1, "status": status, "split_flights": True, "plan": to_dict(plan),
            "outputs": outputs, "completed_outputs": len(results), "message": message}


def run_job(settings, args, cancel, index, count, protected_paths):
    """Analyze one source once, then encode joined or independent output plans."""
    from . import controller, processing
    from .jobs import destination_paths, plan_outputs, validate_jobs, write_manifest
    plan, plans, results = None, (), []
    with EventLogger(settings.log_path, args.events_jsonl) as logger:
        def emit(event):
            logger(Event(event.code, {"input": str(settings.input_path), "job_index": index,
                                      "job_count": count, **event.data}))
        try:
            emit(Event("job.started", {"settings": to_dict(settings), "analyze_only": args.analyze_only,
                                        "split_flights": args.split_flights}))
            analysis = controller.analyze(settings, emit=emit, cancel=cancel)
            for diagnostic in analysis.diagnostics:
                emit(Event("warning", {"stage": "analysis", "message": diagnostic}))
            if cancel.is_cancelled():
                emit(Event("job.cancelled"))
                return 130
            plan = controller.build_plan(settings, analysis)
            plans = plan_outputs(plan, split_flights=args.split_flights)
            emit(Event("plan.resolved", {"selected": plan.selected, "reasons": plan.reasons,
                                          "removed_intervals": plan.removed_intervals}))
            if args.split_flights:
                validate_jobs([part.settings for part in plans], protected_paths=protected_paths)
                emit(Event("flights.planned", {"count": len(plans),
                                                "outputs": [str(part.settings.output_path) for part in plans]}))
            if args.analyze_only:
                summary = split_summary(plan, plans, [], "analyzed") if args.split_flights else {
                    "schema_version": 1, "status": "analyzed", "plan": to_dict(plan)}
                write_manifest(settings.report_path, summary)
                emit(Event("job.analyzed", {"report": settings.report_path}))
                return 0
            for part_index, part in enumerate(plans, 1):
                if args.split_flights:
                    from .runtime import check_cancel
                    check_cancel(cancel)
                    with EventLogger(part.settings.log_path, echo=False) as part_logger:
                        def part_emit(event):
                            tagged = Event(event.code, {"part_index": part_index,
                                                        "part_count": len(plans), **event.data})
                            part_logger(tagged)
                            emit(tagged)
                        part_emit(Event("part.started", {"settings": to_dict(part.settings)}))
                        try:
                            result = processing.execute(part, emit=part_emit, cancel=cancel)
                        except Exception as error:
                            from .runtime import CancelledError
                            from .processing import ProcessingCancelled
                            cancelled = cancel.is_cancelled() or isinstance(error, (CancelledError, ProcessingCancelled))
                            part_emit(Event("part.cancelled" if cancelled else "part.failed", {"message": str(error)}))
                            raise
                        results.append(result)
                        part_emit(Event("part.completed", {"output": result.output_path,
                                                            "report": result.report_path, "bytes": result.bytes}))
                else:
                    result = processing.execute(part, emit=emit, cancel=cancel)
                    results.append(result)
            if args.split_flights:
                write_manifest(settings.report_path, split_summary(plan, plans, results, "complete"))
                protected_paths.update(path for part in plans for path in destination_paths(part.settings))
                emit(Event("job.completed", {"outputs": [str(result.output_path) for result in results],
                                              "report": settings.report_path,
                                              "bytes": sum(result.bytes for result in results)}))
            else:
                result = results[0]
                emit(Event("job.completed", {"output": result.output_path, "report": result.report_path,
                                              "bytes": result.bytes, "duration_seconds": result.duration_seconds}))
            return 0
        except Exception as error:
            from .runtime import CancelledError
            from .processing import ProcessingCancelled
            cancelled = cancel.is_cancelled() or isinstance(error, (CancelledError, ProcessingCancelled))
            if args.split_flights and plan is not None:
                try:
                    write_manifest(settings.report_path, split_summary(
                        plan, plans, results, "cancelled" if cancelled else "failed", str(error)))
                except Exception as report_error:
                    print(f"Could not save split summary: {report_error}", file=sys.stderr)
            emit(Event("job.cancelled" if cancelled else "job.failed", {
                "message": str(error), "completed_outputs": [str(result.output_path) for result in results]}))
            return 130 if cancelled else 1


def main(argv=None):
    args = create_parser().parse_args(argv)
    cancel, previous_handler = CancelToken(), None
    try:
        from .jobs import destination_paths, make_jobs
        if args.split_flights and args.cut_no_signal == "off":
            raise ValueError("--split-flights requires automatic snow detection; remove --cut-no-signal off")
        jobs = make_jobs(args.input, output_path=args.output, output_dir=args.output_dir,
                         output_suffix=args.output_suffix, output_format=args.format,
                         report_path=args.report, log_path=args.log_file, **processing_options(args))
        protected = {Path(job.input_path).resolve() for job in jobs}
        protected.update(path for job in jobs for path in destination_paths(job))
        previous_handler = signal.signal(signal.SIGINT, lambda signum, frame: cancel.cancel())
        outcomes = []
        for index, settings in enumerate(jobs, 1):
            if cancel.is_cancelled():
                return 130
            try:
                code = run_job(settings, args, cancel, index, len(jobs), protected)
            except Exception as error:
                print(f"Error processing {settings.input_path}: {error}", file=sys.stderr)
                code = 130 if cancel.is_cancelled() else 1
            outcomes.append(code)
            if code == 130:
                return 130
        if len(jobs) > 1:
            summary = {"inputs": len(jobs), "succeeded": outcomes.count(0), "failed": outcomes.count(1)}
            print(f"Batch completed: {summary['succeeded']}/{len(jobs)} inputs succeeded.", file=sys.stderr)
            if args.events_jsonl:
                print(json.dumps({"schema_version": 1, "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                  "code": "batch.completed", "data": summary}), flush=True)
        return 1 if any(outcomes) else 0
    except KeyboardInterrupt:
        cancel.cancel()
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
