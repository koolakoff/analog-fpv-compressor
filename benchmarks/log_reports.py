"""Read current session evidence while retaining compatibility with archived reports."""

import json
from pathlib import Path


def read_report(path, log_path=None):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    output = str(path).removesuffix(".report.json")
    log_path = Path(log_path or output + ".session.log")
    if not log_path.exists():
        return None
    reports = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        data = event.get("data", {})
        if event["code"] in ("processing_completed", "job.analyzed"):
            report = data.get("report")
        elif event["code"] == "flights.summary":
            report = data
        else:
            continue
        if report and str(report["plan"]["settings"]["output_path"]) == output:
            reports.append(report)
    return reports[-1] if reports else None
