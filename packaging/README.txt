analog-fpv-compressor @VERSION@ - Windows x64 GUI + CLI

Extract the WHOLE ZIP to a writable directory. Keep _internal next to the EXE.
Python, pip and NumPy do not need to be installed on the user's computer.
Double-click fpv-compress-gui.exe for the graphical interface, or run
fpv-compress.exe in PowerShell/Windows Terminal for the console interface.
Target: Windows 10/11 x64. This is a ZIP archive; no application installer.
Qt/PySide6 are bundled. See licenses/, sources/ and THIRD-PARTY-NOTICES.md.

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

Batch inputs and automatic naming:
  .\fpv-compress.exe -i "flight.avi"
  .\fpv-compress.exe -i "*.avi" --output-dir "converted" --format mp4
  .\fpv-compress.exe -i "first.avi" "second.avi" --output-suffix "_small"
  .\fpv-compress.exe -i "flight.avi" --split-flights

Without -o: INPUT_STEM_converted_1.mkv etc. beside the input, or in --output-dir.
Use --output-suffix to replace _converted, and --format mkv|mp4.
--split-flights produces INPUT_STEM_converted_1.mkv, _2.mkv, etc.
This is the default. Use --no-split-flights to join useful intervals, or
--cut-no-signal off to keep snow and disable automatic splitting.
Only confirmed snow separates parts; brief interference and blue screens do not.
Each retained interval becomes a file, including brief useful signal returns.
Quoted globs are expanded by the program. Duplicate paths are processed once.
--output-dir is created if needed. -o accepts one input.
--log-file selects the shared session log, including multi-file batches.
--split-flights conflicts with --cut-no-signal off.

Default: AV1 CRF 48/preset 6, medium HQDN3D, original resolution,
automatic deinterlace analysis, conservative snow cutting, audio removal.
Blue screens are not automatically cut. Multiple inputs are processed sequentially.
The output directory must exist unless --output-dir is used. Existing outputs are not overwritten.
Cancel with Ctrl+C. Source files are never modified.
An individual input failure does not stop the remaining batch; exit code is 1
if any input fails. Cancellation stops the entire batch, with exit code 130.
Completed outputs remain. Split mode records its summary in the session log.

No JSON reports are written next to the output video. Plans, checks and split
summaries are recorded in the session log, also in --analyze-only mode.
One English diagnostic file, fpv-compress.log, is written next to the executable.
It is overwritten on the next program launch. Send a copy for troubleshooting
before launching again. It records settings, decisions, job results and errors,
without per-frame progress or separate FFmpeg logs. The program folder must be
writable, or use --log-file to choose another writable location.

Application code: MIT, see LICENSE. Bundled runtime licenses: licenses/.
THIRD-PARTY-NOTICES.md describes the scope of the licenses.
BUILD.json records versions and hashes; SHA256SUMS.txt lists distributed files.
Source, documentation and release notes:
https://github.com/koolakoff/analog-fpv-compressor
