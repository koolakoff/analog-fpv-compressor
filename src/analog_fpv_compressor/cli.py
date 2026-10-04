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
    parser.add_argument("-i", "--input", required=True, type=Path, help="Input DVR video")
    parser.add_argument("-o", "--output", required=True, type=Path, help="New output video; existing files are never overwritten")
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


def settings_from_args(args):
    return Settings(input_path=args.input, output_path=args.output, crf=args.crf, bitrate=args.bitrate,
                    preset=args.preset, scale=args.scale, denoise=args.denoise, deinterlace=args.deinterlace,
                    field_order=args.field_order, cut_no_signal=args.cut_no_signal,
                    no_signal_min_duration=args.no_signal_min_duration, audio=args.audio, codec=args.codec,
                    ffmpeg_dir=args.ffmpeg_dir, threads=args.threads,
                    report_path=args.report or Path(str(args.output) + ".report.json"),
                    log_path=args.log_file or Path(str(args.output) + ".log"))


class EventLogger:
    """Persist the same events as English text and versioned JSON Lines."""

    def __init__(self, path, machine_stdout=False):
        self.path = Path(path)
        self.json_path = Path(str(path) + ".jsonl")
        self.machine_stdout = machine_stdout
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


def main(argv=None):
    args = create_parser().parse_args(argv)
    settings = settings_from_args(args)
    cancel = CancelToken()
    previous_handler = None
    try:
        from . import controller, processing
        controller.validate_settings(settings)
        paths = [settings.input_path, settings.output_path, settings.report_path,
                 settings.log_path, Path(str(settings.log_path) + ".jsonl")]
        resolved = [path.resolve() for path in paths]
        if len(set(resolved)) != len(resolved):
            raise ValueError("Input, output, report, and log paths must be distinct")
        for path in paths[1:]:
            if path.exists():
                raise FileExistsError(f"Refusing to overwrite existing file: {path}")
            if not path.parent.is_dir():
                raise ValueError(f"Destination directory does not exist: {path.parent}")
        previous_handler = signal.signal(signal.SIGINT, lambda signum, frame: cancel.cancel())
        with EventLogger(settings.log_path, args.events_jsonl) as emit:
            try:
                emit(Event("job.started", {"settings": to_dict(settings), "analyze_only": args.analyze_only}))
                analysis = controller.analyze(settings, emit=emit, cancel=cancel)
                for diagnostic in analysis.diagnostics:
                    emit(Event("warning", {"stage": "analysis", "message": diagnostic}))
                if cancel.is_cancelled():
                    emit(Event("job.cancelled"))
                    return 130
                plan = controller.build_plan(settings, analysis)
                emit(Event("plan.resolved", {"selected": plan.selected, "reasons": plan.reasons,
                                              "removed_intervals": plan.removed_intervals}))
                if args.analyze_only:
                    with settings.report_path.open("x", encoding="utf-8") as destination:
                        json.dump({"schema_version": 1, "status": "analyzed", "plan": to_dict(plan)},
                                  destination, indent=2, ensure_ascii=False, allow_nan=False)
                    emit(Event("job.analyzed", {"report": settings.report_path}))
                else:
                    result = processing.execute(plan, emit=emit, cancel=cancel)
                    emit(Event("job.completed", {"output": result.output_path, "report": result.report_path,
                                                 "bytes": result.bytes, "duration_seconds": result.duration_seconds}))
                return 0
            except Exception as error:
                from .runtime import CancelledError
                from .processing import ProcessingCancelled
                if cancel.is_cancelled() or isinstance(error, (CancelledError, ProcessingCancelled)):
                    emit(Event("job.cancelled", {"message": str(error)}))
                    return 130
                emit(Event("job.failed", {"message": str(error)}))
                return 1
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
