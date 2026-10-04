"""Run the shared job runner outside the GUI thread."""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal, Slot

from ..jobs import destination_paths, make_jobs
from ..models import CancelToken, Event
from ..runner import run_job
import traceback


class BatchWorker(QObject):
    event = Signal(object)
    finished = Signal(object)

    def __init__(self, inputs, options, cancel=None, session_log=None):
        super().__init__()
        self.inputs = tuple(inputs)
        self.options = dict(options)
        self.cancel = cancel or CancelToken()
        self.session_log = session_log

    def send_event(self, event):
        if self.session_log is not None:
            self.session_log(event)
        self.event.emit(event)

    @Slot()
    def run(self):
        """Keep per-source failures isolated and cancellation batch-wide."""
        succeeded = failed = 0
        cancelled = False
        try:
            options = dict(self.options)
            args = SimpleNamespace(split_flights=options.pop("split_flights", False),
                                   analyze_only=options.pop("analyze_only", False), events_jsonl=False)
            self.send_event(Event("batch.started", {"inputs": self.inputs, "options": self.options}))
            jobs = make_jobs(self.inputs, **options)
            reserved = {job.input_path.resolve() for job in jobs}
            reserved.update(path for job in jobs for path in destination_paths(job))
            if self.session_log is not None and self.session_log.path in reserved:
                raise ValueError("The session log collides with an input, output or report")
            if self.session_log is not None:
                reserved.add(self.session_log.path)
            for index, settings in enumerate(jobs, 1):
                if self.cancel.is_cancelled():
                    cancelled = True
                    break
                try:
                    code = run_job(settings, args, self.cancel, index, len(jobs), reserved,
                                   on_event=self.event.emit, echo=False, session_log=self.session_log)
                except Exception as error:
                    code = 130 if self.cancel.is_cancelled() else 1
                    self.send_event(Event("job.cancelled" if code == 130 else "job.failed",
                                          {"input": str(settings.input_path), "message": str(error),
                                           "job_index": index, "job_count": len(jobs), "traceback": traceback.format_exc()}))
                if code == 130:
                    cancelled = True
                    break
                succeeded += code == 0
                failed += code != 0
        except Exception as error:
            failed += 1
            self.send_event(Event("batch.failed", {"message": str(error), "traceback": traceback.format_exc()}))
        finally:
            summary = {"succeeded": succeeded, "failed": failed, "cancelled": cancelled, "inputs": len(self.inputs)}
            self.send_event(Event("batch.finished", summary))
            self.finished.emit(summary)
