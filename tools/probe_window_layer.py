"""Render the real widget while checking Windows stacking against an owned test window.

Uses isolated settings. Does not drive other apps, change the shell, or send Win+D.
"""
import argparse
import ctypes
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from desktop_black_hole import DesktopBlackHole, configure_surface_format


class FixedClock:
    def elapsed(self):
        return 2400


def is_above(first, second):
    """Read native z-order; only return the relative order of our two HWNDs."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetTopWindow.argtypes = [ctypes.c_void_p]
    api.GetTopWindow.restype = ctypes.c_void_p
    api.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api.GetWindow.restype = ctypes.c_void_p
    handle = api.GetTopWindow(None)
    visited = set()
    while handle and handle not in visited:
        if handle == first:
            return True
        if handle == second:
            return False
        visited.add(handle)
        handle = api.GetWindow(handle, 2)  # GW_HWNDNEXT, read-only.
    raise RuntimeError("Test windows missing from native z-order")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows desktop required")
    args.output.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])
    report = {"errors": [], "desktop_mode": "bottom-most top-level Tool",
              "win_d_tested": False, "uses_isolated_settings": True}
    with tempfile.TemporaryDirectory(prefix="black-hole-layer-") as temp:
        settings = QSettings(str(Path(temp) / "settings.ini"), QSettings.IniFormat)
        window = DesktopBlackHole(settings=settings, companion_default=True)
        cover = QWidget()
        cover.setWindowTitle("Black hole window-layer verification")
        cover.setStyleSheet("background-color: #34516b;")
        try:
            window.fatal_error.connect(report["errors"].append)
            window._set_cursor_lens_enabled(False)
            window._frame_timer.stop()
            window._elapsed_timer = FixedClock()
            window._last_frame_elapsed_ms = 2400
            window.setGeometry(180, 180, 450, 300)
            window.show()
            QTest.qWait(700)
            geometry = window.geometry()
            before = window.grabFramebuffer()
            assert before.save(str(args.output / "normal.png"))
            cover.setGeometry(240, 220, 450, 300)
            cover.show()
            cover.raise_()
            window._set_always_on_top(True)
            QTest.qWait(250)
            cover.raise_()
            QTest.qWait(100)
            report["topmost_above_test_app"] = is_above(int(window.winId()), int(cover.winId()))
            assert report["topmost_above_test_app"]

            window._set_desktop_layer(True)
            QTest.qWait(250)
            window.raise_()  # Even an explicit request must not lift desktop mode.
            window.activateWindow()
            QTest.qWait(250)
            report["desktop_below_test_app_after_raise"] = not is_above(
                int(window.winId()), int(cover.winId()))
            assert report["desktop_below_test_app_after_raise"]
            report["geometry_preserved"] = window.geometry() == geometry
            assert report["geometry_preserved"]
            after = window.grabFramebuffer()
            assert after.save(str(args.output / "desktop.png"))
            report["same_frozen_render"] = before == after
            assert report["same_frozen_render"]

            menu_result = {}

            def inspect_menu():
                menu = app.activePopupWidget()
                if menu is None:
                    menu_result["error"] = "Menu did not open"
                    return
                menu_result["saved"] = menu.grab().save(str(args.output / "menu.png"))
                menu_result["actions"] = [{"text": a.text(), "checked": a.isChecked()}
                                          for a in menu.actions()]
                menu.close()

            QTimer.singleShot(100, inspect_menu)
            point = QPoint(20, 20)
            app.sendEvent(window, QContextMenuEvent(
                QContextMenuEvent.Mouse, point, window.mapToGlobal(point)))
            assert menu_result.get("saved"), menu_result
            report["menu"] = menu_result["actions"]
            # Recreate flags repeatedly with real background capture enabled.
            window.set_desktop_lens_enabled(True)
            for mode in ("desktop", "top", "normal", "desktop"):
                if mode == "top":
                    window._set_always_on_top(True)
                elif mode == "normal":
                    window._set_always_on_top(False)
                else:
                    window._set_desktop_layer(True)
                previous = window._desktop_capture.serial
                for _ in range(40):
                    QTest.qWait(50)
                    window.grabFramebuffer()
                    if window._desktop_capture.serial > previous:
                        break
                capture = window._desktop_capture
                assert not capture.error, capture.error
                assert capture.serial > previous, "Capture did not resume after layer switch"
                capture.exclusion.verify(int(window.winId()))
            assert window.grabFramebuffer().save(str(args.output / "desktop_lens.png"))
            report["capture_resumed_in_all_modes"] = True
            report["capture_exclusion_verified"] = True
            assert not report["errors"], report["errors"]
        finally:
            cover.close()
            window.close()
            app.processEvents()
            report["closed_cleanly"] = (
                not window._frame_timer.isActive()
                and not window._desktop_capture.timer.isActive()
                and window._activity_monitor.closed)
            (args.output / "probe.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
