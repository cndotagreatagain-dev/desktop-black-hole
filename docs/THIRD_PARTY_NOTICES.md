# Third-party notices / 第三方许可

This distribution includes unmodified Python, PySide6/shiboken6/Qt, PyOpenGL,
and the PyInstaller bootloader. Exact versions are recorded in build-info.json.
License texts and notices are included in licenses/. No commercial Qt license is claimed.

- Python 3.12: Python Software Foundation license; https://www.python.org/.
- PySide6 / shiboken6 / Qt 6.11.1: used under the applicable LGPLv3 terms for the included modules.
  Qt DLLs and bindings remain separate, dynamically loaded files in _internal/PySide6 and
  _internal/shiboken6. Users may replace them with compatible modified builds and debug those modifications.
  No restriction on reverse engineering for debugging modifications to these libraries is imposed.
  Upstream sources: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/
  and https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/.
  Source and rebuilding instructions for this application are supplied in source.zip.
- PyOpenGL 3.1.10: BSD-style licenses, including upstream third-party notices;
  https://github.com/mcfletch/pyopengl.
- PyInstaller 6.22.0 bootloader: GPL with the bootloader distribution exception;
  https://pyinstaller.org/en/stable/license.html.

The package does not include Codex, DeepSeek Harness, model weights, accounts,
API keys or audio. DSH integration code in this project has no additional runtime dependency
beyond the user's DSH/Node host. Separate software retains its own license and usage terms.

## Rebuild

Use Windows x64 Python 3.12, create a virtual environment, install requirements-build.txt,
then run `python tools/build_release.py --output dist/my-release` in the extracted source directory.
Choose a new directory each time; earlier releases are never removed automatically.
The build is windowed, onedir, without UPX; it is not code-signed.
