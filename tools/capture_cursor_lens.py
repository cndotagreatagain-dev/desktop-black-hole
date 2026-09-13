"""Capture actual scene/cursor GL passes without moving or hiding the OS cursor."""
import argparse
import ctypes
import json
import math
import re
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--width", type=int, default=600)
    parser.add_argument("--frames", type=int, default=32)
    parser.add_argument("--path", choices=("orbit", "crossing", "center", "hold"), default="orbit")
    parser.add_argument("--time-step", type=float, default=0.0)
    parser.add_argument("--time", type=float, default=300.0)
    parser.add_argument("--companion", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from desktop_black_hole import BlackHoleGLWidget, LensGeometry, configure_surface_format
    from gargantua_scene_shader import SCENE_FRAGMENT_SHADER_SOURCE
    from windows_cursor import WindowsCursorProvider, _CtypesWin32Api

    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])

    class ArrowApi(_CtypesWin32Api):
        def current_visible_cursor(self):
            load = self._user32.LoadCursorW
            load.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            load.restype = ctypes.c_void_p
            return load(None, ctypes.c_void_p(32512))  # Shared IDC_ARROW, never replaced.

    snapshot = WindowsCursorProvider(api=ArrowApi()).capture()
    if snapshot is None:
        raise RuntimeError("Could not capture the native arrow resource")
    window = BlackHoleGLWidget()
    window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
    window.setAttribute(Qt.WA_TranslucentBackground)
    window.set_scene_quality(1)
    window.resize(args.width, round(args.width * 2 / 3))
    if args.companion:
        window.set_companion_enabled(True)
        window.set_companion_activity("busy")

    class FixedClock:
        def elapsed(self):
            return round((args.time + state["index"] * args.time_step) * 1000)

    window._elapsed_timer = FixedClock()
    state = {"index": 0, "error": None, "ready": False, "poses": []}

    def constant(name):
        return float(re.search(rf"const float {name} = ([0-9.]+);", SCENE_FRAGMENT_SHADER_SOURCE).group(1))

    observer = math.hypot(constant("CAMERA_DISTANCE"), constant("CAMERA_HEIGHT"))
    sine = constant("CRITICAL_IMPACT") * math.sqrt(1 - constant("SCHWARZSCHILD_RADIUS") / observer) / observer
    critical_projection = constant("FOCAL_LENGTH") * sine / math.sqrt(1 - sine * sine)

    def geometry():
        return LensGeometry.from_logical_size(window.width(), window.height(), window.devicePixelRatioF())

    def pose(index):
        g = geometry()
        radius = critical_projection * window.height() * window.devicePixelRatioF() * 0.5
        if args.path == "hold":
            angle, distance = math.pi * 1.08, radius * 1.18
        elif args.path == "orbit":
            angle, distance = math.tau * index / max(1, args.frames), radius * 1.08
        elif args.path == "crossing":
            angle = math.pi * 0.12
            distance = radius * (2.75 - 2.55 * index / max(1, args.frames - 1))
        else:
            angle = math.pi * 0.12
            distance = radius * (0.40 - 0.80 * index / max(1, args.frames - 1))
        return g.center_x + math.cos(angle) * distance, g.center_y + math.sin(angle) * distance

    def failed(message):
        state["error"] = str(message)
        window.close()
        app.quit()

    def capture():
        index = state["index"]
        pointer = pose(index)
        window.update_cursor_proxy_pose(1, pointer, geometry())
        frame = window.grabFramebuffer()
        args.output.mkdir(parents=True, exist_ok=True)
        if not frame.save(str(args.output / f"frame_{index:03d}.png"), "PNG"):
            failed("Could not save production cursor capture")
            return
        state["poses"].append(pointer)
        state["index"] += 1
        if state["index"] < max(1, args.frames):
            QTimer.singleShot(40, capture)
        else:
            metadata = dict(source_root=str(args.source_root.resolve()), path=args.path,
                            size=[frame.width(), frame.height()], time=args.time,
                            time_step=args.time_step, companion=args.companion,
                            pixel_ratio=window.devicePixelRatioF(),
                            cursor_size=[snapshot.width, snapshot.height],
                            hotspot=[snapshot.hotspot_x, snapshot.hotspot_y], poses=state["poses"])
            (args.output / "capture.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            window.close()
            app.quit()

    def ready(request_id):
        if request_id != 1 or state["ready"]:
            return
        state["ready"] = True
        if not window.activate_cursor_proxy(1):
            failed("Cursor activation failed")
            return
        QTimer.singleShot(100, capture)

    window.fatal_error.connect(failed)
    window.cursor_pipeline_failed.connect(failed)
    window.cursor_proxy_failed.connect(lambda request_id, message: failed(message))
    window.cursor_texture_ready.connect(ready)
    window.show()
    QTimer.singleShot(500, lambda: window.arm_cursor_proxy(1, snapshot, pose(0), geometry()))
    QTimer.singleShot(20000, lambda: failed("Cursor capture timeout"))
    app.exec()
    if state["error"]:
        raise RuntimeError(state["error"])
    if state["index"] != max(1, args.frames):
        raise RuntimeError("Incomplete cursor capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
