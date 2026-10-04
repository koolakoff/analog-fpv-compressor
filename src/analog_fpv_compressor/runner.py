"""Shared job orchestration and English evidence logs for CLI and GUI."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import platform
import importlib.metadata as metadata
from threading import Lock
import traceback
from contextlib import nullcontext

from .models import Event, to_dict


class EventLogger:
    """One UTF-8 JSON-lines diagnostic file for an application session."""

    def __init__(self, path=None, machine_stdout=False, echo=True, protected_paths=()):
        self.path = Path(path or default_log_path()).resolve()
        self.machine_stdout = machine_stdout
        self.echo = echo
        self.protected_paths = {Path(path).resolve() for path in protected_paths}
        self.text_file = None
        self.lock = Lock()
        self.had_error = False

    def __enter__(self):
        if self.path in self.protected_paths:
            raise ValueError("The session log must not replace an input, output or report")
        self.had_error = False
        self.text_file = self.path.open("w", encoding="utf-8")
        self(Event("session.started", session_metadata()))
        return self

    def __exit__(self, *args):
        self(Event("session.finished", {"failed": self.had_error or bool(args and args[0])}))
        self.text_file.close()
        self.text_file = None

    def __call__(self, event):
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        record = {"schema_version": 1, "timestamp": timestamp, "code": event.code,
                  "level": "ERROR" if "failed" in event.code or "error" in event.code else
                  "WARNING" if "warning" in event.code else "INFO", "data": to_dict(event.data)}
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        if record["level"] == "ERROR":
            self.had_error = True
        persist = event.code not in ("analysis_progress", "progress") or (
            event.code == "progress" and event.data.get("state") in ("started", "completed"))
        if persist:
            with self.lock:
                self.text_file.write(encoded + "\n")
                self.text_file.flush()
            if self.echo:
                message = f"{timestamp} {record['level']} {event.code}: {json.dumps(record['data'], ensure_ascii=False)}"
                if event.code == "plan.resolved":
                    intervals = event.data.get("removed_intervals", ())
                    readable = [f"[{format_timestamp(start)}, {format_timestamp(end)})" for start, end in intervals]
                    message += "; removed source intervals: " + (", ".join(readable) if readable else "none")
                print(message, file=sys.stderr, flush=True)
        if self.machine_stdout:
            print(encoded, flush=True)


def default_log_path():
    """Locate the current log beside the installed launcher or Python executable."""
    launcher = Path(sys.argv[0])
    directory = launcher.resolve().parent if not getattr(sys, "frozen", False) and launcher.name.startswith("fpv-compress") else Path(sys.executable).resolve().parent
    return directory / "fpv-compress.log"


def session_metadata():
    from . import __version__
    versions = {}
    for package in ("numpy", "PySide6"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            pass
    return {"application_version": __version__, "python_version": platform.python_version(),
            "platform": platform.platform(), "executable": sys.executable,
            "working_directory": str(Path.cwd()), "dependencies": versions}


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
                  "keep_intervals": part.keep_intervals, "mapping": part.mapping,
                  "status": "complete" if result else "planned"}
        if result:
            output.update(bytes=result.bytes, duration_seconds=result.duration_seconds)
        outputs.append(output)
    return {"schema_version": 1, "status": status, "split_flights": True, "plan": to_dict(plan),
            "outputs": outputs, "completed_outputs": len(results), "message": message}


def run_job(settings, args, cancel, index, count, protected_paths, on_event=None, echo=True, session_log=None):
    """Analyze one source once, then encode joined or independent output plans."""
    from . import controller, processing
    from .jobs import destination_paths, plan_outputs, validate_jobs
    plan, plans, results = None, (), []
    with nullcontext(session_log) if session_log is not None else EventLogger(machine_stdout=args.events_jsonl, echo=echo) as logger:
        def emit(event):
            tagged = Event(event.code, {"input": str(settings.input_path), "job_index": index,
                                       "job_count": count, **event.data})
            logger(tagged)
            if on_event is not None:
                on_event(tagged)
        try:
            emit(Event("job.started", {"settings": to_dict(settings), "analyze_only": args.analyze_only,
                                        "split_flights": args.split_flights}))
            analysis = controller.analyze(settings, emit=emit, cancel=cancel)
            emit(Event("input.analyzed", {"probe": analysis.probe, "tool_versions": analysis.metadata.get("tool_versions"),
                                           "interlace": analysis.interlace, "source_fingerprint": analysis.metadata.get("source_fingerprint")}))
            for diagnostic in analysis.diagnostics:
                emit(Event("warning", {"stage": "analysis", "message": diagnostic}))
            if cancel.is_cancelled():
                emit(Event("job.cancelled"))
                return 130
            plan = controller.build_plan(settings, analysis)
            plans = plan_outputs(plan, split_flights=args.split_flights)
            emit(Event("plan.resolved", {"selected": plan.selected, "reasons": plan.reasons,
                                          "removed_intervals": plan.removed_intervals, "keep_intervals": plan.keep_intervals,
                                          "removed_seconds": sum(end - start for start, end in plan.removed_intervals),
                                          "mapping": plan.mapping, "policy_version": plan.policy_version}))
            if args.split_flights:
                validate_jobs([part.settings for part in plans], protected_paths=protected_paths)
                emit(Event("flights.planned", {"count": len(plans),
                                                "outputs": [str(part.settings.output_path) for part in plans]}))
            if args.analyze_only:
                summary = split_summary(plan, plans, [], "analyzed") if args.split_flights else {
                    "schema_version": 1, "status": "analyzed", "plan": to_dict(plan)}
                emit(Event("job.analyzed", {"report": summary}))
                return 0
            for part_index, part in enumerate(plans, 1):
                if args.split_flights:
                    from .runtime import check_cancel
                    check_cancel(cancel)
                    def part_emit(event):
                        tagged = Event(event.code, {"part_index": part_index,
                                                    "part_count": len(plans), **event.data})
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
                                                        "bytes": result.bytes}))
                else:
                    result = processing.execute(part, emit=emit, cancel=cancel)
                    results.append(result)
            if args.split_flights:
                emit(Event("flights.summary", split_summary(plan, plans, results, "complete")))
                protected_paths.update(path for part in plans for path in destination_paths(part.settings))
                emit(Event("job.completed", {"outputs": [str(result.output_path) for result in results],
                                              "bytes": sum(result.bytes for result in results)}))
            else:
                result = results[0]
                emit(Event("job.completed", {"output": result.output_path,
                                              "bytes": result.bytes, "duration_seconds": result.duration_seconds}))
            return 0
        except Exception as error:
            from .runtime import CancelledError
            from .processing import ProcessingCancelled
            cancelled = cancel.is_cancelled() or isinstance(error, (CancelledError, ProcessingCancelled))
            if args.split_flights and plan is not None:
                emit(Event("flights.summary", split_summary(
                    plan, plans, results, "cancelled" if cancelled else "failed", str(error))))
            emit(Event("job.cancelled" if cancelled else "job.failed", {
                "message": str(error), "exception_type": type(error).__name__, "traceback": traceback.format_exc(), "completed_outputs": [str(result.output_path) for result in results]}))
            return 130 if cancelled else 1
