"""Build a Windows x64 folder bundle and ZIP without copying the developer venv."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git_output(*arguments):
    return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}",
                                    *arguments], cwd=ROOT, text=True).strip()


def collect_licenses(destination):
    """Keep full upstream notices rather than a list of license names."""
    destination.mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", destination / "Python-LICENSE.txt")
    for package in ("numpy", "pyinstaller"):
        distribution = metadata.distribution(package)
        count = 0
        for relative in distribution.files or ():
            if "license" not in relative.name.lower() and relative.name != "COPYING.txt":
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                name = package + "-" + str(relative).replace("/", "_").replace("\\", "_")
                shutil.copy2(source, destination / name)
                count += 1
        if not count:
            raise RuntimeError(f"Complete license texts not found for {package}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("This release target must be built on Windows x64")
    from analog_fpv_compressor import __version__
    version = __version__
    name = f"analog-fpv-compressor-{version}-windows-x64"
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle, archive = output / name, output / (name + ".zip")
    if bundle.exists() or archive.exists():
        raise FileExistsError("Release already exists; choose a fresh --output-directory")
    work = ROOT / "build" / ("release-" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--onedir", "--console", "--noupx",
                    "--name", "fpv-compress", "--paths", str(ROOT / "src"),
                    "--specpath", str(work), "--workpath", str(work / "work"),
                    "--distpath", str(work / "stage"), str(ROOT / "packaging/entrypoint.py")],
                   cwd=ROOT, check=True)
    shutil.copytree(work / "stage/fpv-compress", bundle)
    for filename in ("README.txt", "setup-ffmpeg.cmd", "setup-ffmpeg.ps1"):
        if filename == "README.txt":
            text = (ROOT / "packaging" / filename).read_text(encoding="utf-8").replace("@VERSION@", version)
            (bundle / filename).write_text(text, encoding="utf-8")
        else:
            shutil.copy2(ROOT / "packaging" / filename, bundle / filename)
    for filename in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        shutil.copy2(ROOT / filename, bundle / filename)
    collect_licenses(bundle / "licenses")
    (bundle / "tools").mkdir()
    (bundle / "tools/README.txt").write_text(
        "Optional: place a compatible ffmpeg.exe and ffprobe.exe here.\n"
        "FFmpeg is not included in this ZIP. See ../README.txt.\n", encoding="utf-8")
    manifest = {"application_version": version, "platform": "windows-x64",
                "python_version": platform.python_version(),
                "numpy_version": metadata.version("numpy"),
                "pyinstaller_version": metadata.version("pyinstaller"),
                "hooks_version": metadata.version("pyinstaller-hooks-contrib"),
                "source_revision": git_output("rev-parse", "HEAD"),
                "source_has_local_changes": bool(git_output("status", "--porcelain")),
                "ffmpeg_included": False}
    (bundle / "BUILD.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    checksums = [f"{sha256(path)}  {path.relative_to(bundle).as_posix()}"
                 for path in sorted(bundle.rglob("*")) if path.is_file()]
    (bundle / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(output).as_posix())
    archive.with_suffix(".zip.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": sha256(archive), "build": manifest}, indent=2))


if __name__ == "__main__":
    main()
