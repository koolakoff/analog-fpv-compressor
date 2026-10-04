"""Discover tools and run cancellable subprocesses without invoking a shell."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


class CancelledError(RuntimeError):
    """The user cancelled the active job."""


def check_cancel(cancel):
    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("Processing was cancelled")


def capture(command, cancel=None):
    """Capture both pipes with bounded waits so analysis can be cancelled."""
    check_cancel(cancel)
    process = subprocess.Popen([str(arg) for arg in command], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        while True:
            check_cancel(cancel)
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        process.kill()
        process.communicate()
        raise
    diagnostic = stderr.decode("utf-8", errors="replace")
    if process.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed ({process.returncode}): {diagnostic[-4000:]}")
    return stdout, diagnostic


def discover_tools(directory=None):
    """Prefer explicit paths, environment, portable tools, PATH and WinGet."""
    suffix = ".exe" if os.name == "nt" else ""
    if directory is None and os.environ.get("FFMPEG_DIR"):
        directory = Path(os.environ["FFMPEG_DIR"])
    if directory is not None:
        pair = {name: str(Path(directory) / (name + suffix)) for name in ("ffmpeg", "ffprobe")}
        if all(Path(path).is_file() for path in pair.values()):
            return pair
        raise ValueError("The FFmpeg directory must contain both ffmpeg and ffprobe")
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / "tools"
        pair = {name: str(portable / (name + suffix)) for name in ("ffmpeg", "ffprobe")}
        if all(Path(path).is_file() for path in pair.values()):
            return pair
    pair = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    if all(pair.values()):
        return pair
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        packages = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Packages"
        for binary in sorted(packages.glob("Gyan.FFmpeg*/ffmpeg*/bin/ffmpeg.exe"), reverse=True):
            if binary.with_name("ffprobe.exe").is_file():
                return {"ffmpeg": str(binary), "ffprobe": str(binary.with_name("ffprobe.exe"))}
    raise ValueError("FFmpeg and ffprobe were not found. Install FFmpeg or use --ffmpeg-dir PATH")
