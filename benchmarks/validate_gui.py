"""Drive the actual Qt window and worker on the three original local DVR inputs."""

import argparse
import json
from pathlib import Path
import time

from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from analog_fpv_compressor.gui.window import MainWindow
from validate_batch import ROOT, packets
from log_reports import read_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "outputs/gui-validation")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    preferences = QSettings(str(directory / "preferences.ini"), QSettings.Format.IniFormat)
    preferences.setValue("language", "ru")
    rows = []
    for name, inputs, split in (
            ("defaults", sorted((ROOT / "examples").glob("*.AVI")), False),
            ("home-split", [ROOT / "examples/home-other-helmet.AVI"], True)):
        window = MainWindow(preferences)
        window.snow.setCurrentIndex(window.snow.findData("split" if split else "remove"))
        window.add_paths(inputs)
        window.output_location.setCurrentIndex(window.output_location.findData("folder"))
        window.output_dir.setText(str(directory / name))
        if split:
            window.snow.setCurrentIndex(window.snow.findData("split"))
            window.audio.setChecked(True)
            window.container.setCurrentIndex(window.container.findData("mp4"))
        loop = QEventLoop()
        timer = QTimer()
        heartbeat, progress_events = [], []
        started = time.perf_counter()
        timed_out = []
        def tick():
            heartbeat.append(time.perf_counter())
            if window.thread is None:
                loop.quit()
            elif time.perf_counter() - started > 900 and not timed_out:
                timed_out.append(True)
                window.stop()
        timer.timeout.connect(tick)
        timer.start(100)
        window.start_button.click()
        if window.thread is None:
            raise RuntimeError("GUI did not start its worker")
        def observe(event):
            if event.code == "progress":
                progress_events.append(event.data)
        window.worker.event.connect(observe)
        QTimer.singleShot(2000, lambda: window.language.setCurrentIndex(window.language.findData("uk")))
        QTimer.singleShot(4000, lambda: window.language.setCurrentIndex(window.language.findData("sk")))
        loop.exec()
        timer.stop()
        case = {"name": name, "summary": window.summary, "wall_seconds": time.perf_counter() - started,
                "requested": window.options(), "heartbeat_ticks": len(heartbeat),
                "maximum_heartbeat_gap_seconds": max((b-a for a,b in zip(heartbeat, heartbeat[1:])), default=0),
                "language": window.language.currentData(), "progress_events": progress_events, "outputs": []}
        for source, record in window.records.items():
            if record["state"] != "Ready":
                raise RuntimeError(f"GUI processing failed: {source}; inspect output logs")
            events = [json.loads(line) for line in window.session_log.path.read_text(encoding="utf-8").splitlines()]
            reports = [event["data"]["report"] for event in events if event["code"] == "processing_completed" and event["data"]["input"] == source]
            report = reports[0]
            item = {"input": source, "bytes": record["bytes"], "session_log": str(window.session_log.path)}
            if split:
                parts = reports
                item["parts"] = [{"output": part["output_path"], "validation": part["validation"], "bytes": part["bytes"]} for part in parts]
                baseline = read_report(ROOT / "outputs/cli-validation/home-defaults.mkv.report.json")
                if sum(part["validation"]["decoded_frames"] for part in parts) != baseline["validation"]["decoded_frames"]:
                    raise RuntimeError("GUI split lost or duplicated frames")
                item["frames_preserved"] = True
            else:
                short = {"air-school-stadion-oneflight": "oneflight", "air-school-stadion-with-termination": "termination", "home-other-helmet": "home"}[Path(source).stem]
                ffmpeg = report["plan"]["analysis"]["tools"]["ffmpeg"]
                digest = packets(record["output"], ffmpeg)
                if digest != packets(ROOT / "outputs/cli-validation" / (short + "-defaults.mkv"), ffmpeg):
                    raise RuntimeError("GUI video packets differ from CLI defaults")
                item.update(validation=report["validation"], matches_cli_packets=True, packet_sha256=digest)
            case["outputs"].append(item)
        rows.append(case)
        (directory / "summary.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        window.language.setCurrentIndex(window.language.findData("ru"))
        window.grab().save(str(directory / (name + ".png")))
        window.close()
        app.processEvents()
        if timed_out or window.summary["failed"] or window.summary["cancelled"]:
            raise RuntimeError(f"GUI case failed: {name}")
        print(f"GUI {name}: PASS ({case['wall_seconds']:.2f}s), heartbeat ticks: {len(heartbeat)}", flush=True)


if __name__ == "__main__":
    main()
