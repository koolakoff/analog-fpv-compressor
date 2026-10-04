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
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git_output(*arguments):
    return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}",
                                    *arguments], cwd=ROOT, text=True).strip()


def collect_licenses(destination, source_cache):
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
    sources = destination.parent / "sources"
    sources.mkdir()
    version = metadata.version("PySide6")
    for package, url in (
            ("pyside-setup", f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{version}-src/pyside-setup-everywhere-src-{version}.tar.xz"),
            ("qtbase", f"https://download.qt.io/archive/qt/{'.'.join(version.split('.')[:2])}/{version}/submodules/qtbase-everywhere-src-{version}.tar.xz"),
            ("qtsvg", f"https://download.qt.io/archive/qt/{'.'.join(version.split('.')[:2])}/{version}/submodules/qtsvg-everywhere-src-{version}.tar.xz")):
        filename = url.rsplit("/", 1)[-1]
        archive = source_cache / filename
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=120) as response, archive.with_suffix(".partial").open("wb") as target:
                shutil.copyfileobj(response, target)
            archive.with_suffix(".partial").replace(archive)
        shutil.copy2(archive, sources / filename)
        with tarfile.open(archive) as upstream:
            for member in upstream:
                relative = Path(member.name)
                if member.isfile() and ("LICENSES" in relative.parts or "license" in relative.name.lower()
                                        or relative.name.lower().startswith(("copying", "copyright"))
                                        or relative.name == "qt_attribution.json"):
                    target = destination / package / Path(*relative.parts[1:])
                    if not target.resolve().is_relative_to(destination.resolve()):
                        raise RuntimeError("Unsafe upstream license path")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(upstream.extractfile(member).read())
    (sources / "README.txt").write_text(
        "Unmodified corresponding sources for the bundled Qt/PySide6/Shiboken libraries.\n"
        "These archives are included for license compliance, not needed to run the program.\n"
        "Shared DLLs in _internal/PySide6 and _internal/shiboken6 can be replaced with compatible builds.\n"
        "No restrictions are imposed on modifying/relinking these libraries or debugging those modifications.\n"
        "Build instructions are included in the source archives and at https://doc.qt.io/qtforpython-6/building_from_source/index.html\n",
        encoding="utf-8")


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
    build_environment = os.environ.copy()
    # Keep unrelated tools (notably Poppler's incompatible ICU) out of DLL discovery.
    build_environment["PATH"] = os.pathsep.join((str(Path(sys.executable).parent),
                                               str(Path(sys.base_prefix)),
                                               str(Path(os.environ["SystemRoot"]) / "System32")))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--workpath", str(work / "work"),
                    "--distpath", str(work / "stage"), str(ROOT / "packaging/windows.spec")],
                   cwd=ROOT, env=build_environment, check=True)
    shutil.copytree(work / "stage/fpv-compress", bundle)
    if list(bundle.rglob("icu*.dll")):
        raise RuntimeError("Qt must use Windows system ICU, not DLLs from unrelated tools")
    allowed_qt = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "Qt6OpenGL.dll", "Qt6Svg.dll"}
    shipped_qt = {path.name for path in bundle.rglob("Qt6*.dll")}
    if shipped_qt - allowed_qt:
        raise RuntimeError(f"Unreviewed Qt libraries in bundle: {shipped_qt - allowed_qt}")
    for filename in ("README.txt", "setup-ffmpeg.cmd", "setup-ffmpeg.ps1"):
        if filename == "README.txt":
            text = (ROOT / "packaging" / filename).read_text(encoding="utf-8").replace("@VERSION@", version)
            (bundle / filename).write_text(text, encoding="utf-8")
        else:
            shutil.copy2(ROOT / "packaging" / filename, bundle / filename)
    for filename in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        shutil.copy2(ROOT / filename, bundle / filename)
    cache = ROOT / "build/upstream-sources"
    cache.mkdir(parents=True, exist_ok=True)
    collect_licenses(bundle / "licenses", cache)
    shutil.copy2(ROOT / "src/analog_fpv_compressor/gui/assets/icon-big.ico", bundle / "icon-big.ico")
    for filename in ("README.md",):
        shutil.copy2(ROOT / filename, bundle / filename)
    shutil.copytree(ROOT / "docs/images", bundle / "docs/images")
    for filename in ("README_RU.md", "README_UK.md", "README_SK.md"):
        shutil.copy2(ROOT / "docs" / filename, bundle / "docs" / filename)
    for filename in ("README-engineering.md", "source-of-truth.md", "plan-v1.md", "setup.md"):
        shutil.copy2(ROOT / "docs" / filename, bundle / "docs" / filename)
    shutil.copytree(ROOT / "docs/research", bundle / "docs/research", ignore=shutil.ignore_patterns("source-chat.md"))
    (bundle / "tools").mkdir()
    (bundle / "tools/README.txt").write_text(
        "Optional: place a compatible ffmpeg.exe and ffprobe.exe here.\n"
        "FFmpeg is not included in this ZIP. See ../README.txt.\n", encoding="utf-8")
    manifest = {"application_version": version, "platform": "windows-x64",
                "python_version": platform.python_version(),
                "numpy_version": metadata.version("numpy"),
                "pyside6_version": metadata.version("PySide6"),
                "shiboken6_version": metadata.version("shiboken6"),
                "interfaces": ["cli", "gui"],
                "qt_modules": sorted(shipped_qt),
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
