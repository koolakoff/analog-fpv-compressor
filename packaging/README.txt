analog-fpv-compressor 0.1.0 - Windows x64 console application

Extract the WHOLE ZIP to a writable directory. Keep _internal next to the EXE.
Python, pip and NumPy do not need to be installed on the user's computer.
This is a console application; run it in PowerShell or Windows Terminal.
Target: Windows 10/11 x64. GUI and installer are not included in this release.

FFmpeg is a separate prerequisite and is NOT redistributed in this ZIP.
If FFmpeg is not installed, run setup-ffmpeg.cmd once (internet required).
It installs the full Gyan.FFmpeg build with WinGet. Its license is separate.
Alternatively download the FULL build from https://www.gyan.dev/ffmpeg/builds/
and place ffmpeg.exe and ffprobe.exe in the tools directory next to this EXE.
The essentials build lacks libsvtav1 and is not suitable for the default codec.
An existing full WinGet installation is detected automatically.
You can also use --ffmpeg-dir "C:\path\to\ffmpeg\bin".

Examples, from the extracted directory:
  .\fpv-compress.exe -i "C:\Videos\flight.avi" -o "C:\Videos\flight-small.mkv"
  .\fpv-compress.exe -i "flight.avi" -o "compact.mkv" --scale 480x360
  .\fpv-compress.exe -i "flight.avi" -o "with-audio.mp4" --audio keep
  .\fpv-compress.exe -i "flight.avi" -o "plan.mkv" --analyze-only
  .\fpv-compress.exe --help

Default: AV1 CRF 48/preset 6, medium HQDN3D, original resolution,
automatic deinterlace analysis, conservative snow cutting, audio removal.
Blue screens are not automatically cut. Each invocation handles one input.
The output directory must exist. Existing outputs/reports/logs are not overwritten.
Cancel with Ctrl+C. Source files are never modified.

Reports and English logs are written next to the output video:
  OUTPUT.report.json, OUTPUT.log, OUTPUT.log.jsonl and an FFmpeg diagnostic log.

Application code: MIT, see LICENSE. Bundled runtime licenses: licenses/.
THIRD-PARTY-NOTICES.md describes the scope of the licenses.
BUILD.json records versions and hashes; SHA256SUMS.txt lists distributed files.
Source, documentation and release notes:
https://github.com/koolakoff/analog-fpv-compressor
