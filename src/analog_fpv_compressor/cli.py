"""English CLI over the shared analysis, planning, and execution contracts."""

import argparse
from pathlib import Path
import signal
import sys
import traceback

from . import __version__
from .models import CancelToken, Event, Settings
from .jobs import expand_inputs
from .runner import EventLogger, format_timestamp, run_job


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
    split = parser.add_mutually_exclusive_group()
    split.add_argument("--split-flights", action="store_true", default=None, help="Remove snow and write numbered flights (default)")
    split.add_argument("--no-split-flights", dest="split_flights", action="store_false", help="Join retained intervals into one output")
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
    parser.add_argument("--log-file", type=Path, help="Session log path; overwritten on launch, default beside the program")
    parser.add_argument("--events-jsonl", action="store_true", help="Write versioned machine events to stdout")
    parser.add_argument("--analyze-only", action="store_true", help="Record the resolved plan in the session log without encoding")
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
                    report_path=None,
                    log_path=None)



def main(argv=None):
    args = create_parser().parse_args(argv)
    if args.split_flights is None:
        args.split_flights = args.cut_no_signal != "off"
    cancel, previous_handler = CancelToken(), None
    logger = None
    protected = {Path(value).resolve() for value in args.input}
    protected.update(Path(path).resolve() for path in (args.output,) if path is not None)
    try:
        from .jobs import destination_paths, make_jobs
        inputs, input_error = [], None
        for value in args.input:
            try:
                matches = expand_inputs([value])
                inputs.extend(matches)
                protected.update(matches)
            except Exception as error:
                input_error = input_error or error
        for source in inputs:
            output = args.output or (args.output_dir or source.parent) / (
                source.stem + (args.output_suffix if args.output_suffix is not None else "_converted") + "." + (args.format or "mkv"))
            protected.update((Path(output).resolve(), Path(str(output) + ".report.json").resolve()))
        if input_error is not None:
            raise input_error
        if args.split_flights and args.cut_no_signal == "off":
            raise ValueError("--split-flights requires automatic snow detection; remove --cut-no-signal off")
        jobs = make_jobs(args.input, output_path=args.output, output_dir=args.output_dir,
                         output_suffix=args.output_suffix, output_format=args.format,
                         **processing_options(args))
        protected = {Path(job.input_path).resolve() for job in jobs}
        protected.update(path for job in jobs for path in destination_paths(job))
        logger = EventLogger(args.log_file, args.events_jsonl, protected_paths=protected)
        logger.__enter__()
        protected.add(logger.path)
        previous_handler = signal.signal(signal.SIGINT, lambda signum, frame: cancel.cancel())
        outcomes = []
        for index, settings in enumerate(jobs, 1):
            if cancel.is_cancelled():
                return 130
            try:
                code = run_job(settings, args, cancel, index, len(jobs), protected, session_log=logger)
            except Exception as error:
                logger(Event("job.failed", {"input": str(settings.input_path), "message": str(error),
                                            "traceback": traceback.format_exc()}))
                print(f"Error processing {settings.input_path}: {error}", file=sys.stderr)
                code = 130 if cancel.is_cancelled() else 1
            outcomes.append(code)
            if code == 130:
                return 130
        if len(jobs) > 1:
            summary = {"inputs": len(jobs), "succeeded": outcomes.count(0), "failed": outcomes.count(1)}
            print(f"Batch completed: {summary['succeeded']}/{len(jobs)} inputs succeeded.", file=sys.stderr)
            logger(Event("batch.completed", summary))
        return 1 if any(outcomes) else 0
    except KeyboardInterrupt:
        cancel.cancel()
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        if logger is None:
            try:
                logger = EventLogger(args.log_file, args.events_jsonl, protected_paths=protected)
                logger.__enter__()
            except Exception:
                logger = None
        if logger is not None and logger.text_file is not None:
            logger(Event("application.failed", {"message": str(error), "traceback": traceback.format_exc()}))
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        if logger is not None and logger.text_file is not None:
            logger.__exit__(None, None, None)
