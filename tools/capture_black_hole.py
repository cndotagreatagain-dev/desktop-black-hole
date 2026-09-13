from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_black_hole import (  # noqa: E402
    DesktopBlackHole,
    configure_surface_format,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture the production black-hole widget.")
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=600)
    parser.add_argument("--delay-ms", type=int, default=900)
    parser.add_argument("--quality", choices=("standard", "high", "cinematic"), default="high")
    parser.add_argument("--debug-view", type=int, choices=range(10), default=0)
    parser.add_argument("--time", type=float, help="Freeze simulation seconds for a reproducible capture.")
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--step", type=float, default=0.12)
    parser.add_argument("--interval-ms", type=int, default=30,
                        help="Wall-clock spacing between captures; omit --time for a live soak.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])

    with tempfile.TemporaryDirectory(prefix="black-hole-capture-") as temporary_dir:
        settings = QSettings(
            str(Path(temporary_dir) / "capture.ini"),
            QSettings.IniFormat,
        )
        window = DesktopBlackHole(settings=settings)
        window.set_scene_quality(("standard", "high", "cinematic").index(args.quality))
        window.set_debug_view(args.debug_view)
        if args.time is not None:
            class FixedClock:
                seconds = max(0.0, args.time)

                def elapsed(self):
                    return round(self.seconds * 1000.0)

            window._elapsed_timer = FixedClock()
        width = max(1, args.width)
        window.resize(width, round(width * 2.0 / 3.0))
        window.show()

        result = {"ok": False, "index": 0, "error": None}

        def failed(message):
            result["error"] = message
            window.close()
            app.quit()

        window.fatal_error.connect(failed)

        def capture() -> None:
            frame = window.grabFramebuffer()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            output = args.output
            if args.frames > 1:
                output = output.with_name(f"{output.stem}_{result['index']:03d}{output.suffix}")
            result["ok"] = frame.save(str(output), "PNG")
            result["index"] += 1
            if result["ok"] and result["index"] < max(1, args.frames):
                if args.time is not None:
                    window._elapsed_timer.seconds += args.step
                window.update()
                QTimer.singleShot(max(1, args.interval_ms), capture)
            else:
                window.close()
                app.quit()

        QTimer.singleShot(max(1, args.delay_ms), capture)
        app.exec()

    if result["error"]:
        raise RuntimeError(result["error"])
    if not result["ok"]:
        raise RuntimeError(f"Could not save capture to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
