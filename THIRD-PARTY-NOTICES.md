# Third-party components

The project's own source code is licensed under MIT; see `LICENSE`.
This license does not replace the licenses of dependencies.

The Windows application bundle contains:

- CPython, under the Python Software Foundation license and the notices in
  the Python distribution's complete `LICENSE.txt`.
- NumPy, under its BSD license and bundled-library notices, including OpenBLAS.
- The PyInstaller bootloader, under GPL with its application distribution
  exception. PyInstaller does not require the application to use GPL.
- PySide6/Shiboken6 and Qt Core, Gui, Widgets, Network, OpenGL and Svg,
  distributed under LGPLv3, with upstream third-party notices. The Qt DLLs
  remain separate shared libraries in `_internal/PySide6`.

Complete corresponding unmodified sources for Qt Base, Qt Svg and
PySide6/Shiboken6 are included in `sources/`, with license texts and attribution
files copied into `licenses/`. Version-matched source archives include upstream
build instructions. Users may replace the shared libraries with compatible
modified builds; modification, relinking, and reverse engineering to debug
library modifications are permitted. No signature check or DRM restricts this.
The application's own MIT license remains separate from these LGPL libraries.

The build explicitly excludes optional Qt Virtual Keyboard (GPL-only), PDF and
QML/Quick libraries/plugins; these features are not used by this application.
Qt's licensing explanation: https://www.qt.io/development/open-source-lgpl-obligations

The release build copies the complete license texts into `licenses/` and records
the exact versions in `BUILD.json`. Build tools are not runtime dependencies.

FFmpeg and ffprobe are external executables, not included in this release ZIP.
The optional setup script invokes WinGet to obtain Gyan's full build from its
upstream distribution. Gyan identifies its builds as GPLv3. Installing or using
that separate distribution does not replace this project's MIT license.
Users can provide a compatible FFmpeg installation with `--ffmpeg-dir` or
place both executables in `tools/` beside the application.

Before a future release redistributes FFmpeg binaries, prepare the corresponding
source distribution, build details and notices for FFmpeg and its bundled
libraries. A link to the FFmpeg repository alone is not a complete source bundle.

References:

- https://docs.python.org/3/license.html
- https://numpy.org/doc/stable/license.html
- https://pyinstaller.org/en/stable/license.html
- https://www.gyan.dev/ffmpeg/builds/
- https://ffmpeg.org/legal.html
