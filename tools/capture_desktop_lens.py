"""Real desktop-capture QA against a controlled live Qt window, not a texture mock."""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QWidget
from desktop_black_hole import DesktopBlackHole, configure_surface_format


class Backdrop(QWidget):
    def __init__(self, light):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.light = light
        self.offset = 0

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#eceef2" if self.light else "#172437"))
        p.setPen(QPen(QColor("#96a8b8" if self.light else "#657a96"), 1))
        for x in range(-40, self.width() + 40, 40):
            p.drawLine(x + self.offset, 0, x + self.offset, self.height())
        for y in range(0, self.height(), 40):
            p.drawLine(0, y, self.width(), y)
        p.setPen(QPen(QColor("#387eb9" if self.light else "#79bcdc"), 3))
        p.drawRect(155 + self.offset, 130, 640, 390)
        p.setPen(QColor("#314252" if self.light else "#dae5ef"))
        p.setFont(QFont("Segoe UI", 15))
        for y in range(180, 560, 80):
            p.drawText(190 + self.offset, y, "DESKTOP   |   Real window behind the black hole")
        p.end()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--light", action="store_true")
    parser.add_argument("--width", type=int, default=600)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--move-window", action="store_true")
    parser.add_argument("--companion", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])
    backdrop = Backdrop(args.light)
    backdrop.setGeometry(80, 100, 1100, 760)
    backdrop.show()
    temporary = tempfile.TemporaryDirectory(prefix="desktop-lens-qa-")
    window = DesktopBlackHole(settings=QSettings(
        str(Path(temporary.name) / "settings.ini"), QSettings.IniFormat))
    width, height = args.width, round(args.width * 2 / 3)
    window.setGeometry(330, 260, width, height)
    window._elapsed_timer = type("Clock", (), {"elapsed": lambda self: 300000})()
    window._set_cursor_lens_enabled(False)
    if args.companion:
        window.set_companion_enabled(True)
        window._activity_timer.stop()  # Explicit QA state, not a live status claim.
        window.set_companion_activity("busy")
    window.set_desktop_lens_enabled(True)
    window.show()
    motion_timer = QTimer(window)
    motion_state = [0]
    if args.move_window:
        def move_window():
            motion_state[0] += 1
            import math
            window.move(330 + round(55 * math.sin(motion_state[0] * .045)), 260)
        motion_timer.timeout.connect(move_window)
        QTimer.singleShot(2700, lambda: motion_timer.start(16))
    state = {"frames": [], "error": "", "started": False}

    def fail(message):
        state["error"] = str(message)
        app.quit()

    window.fatal_error.connect(fail)
    window._desktop_capture.failed.connect(fail)

    def capture():
        width, height = window.width(), window.height()
        frame = window._desktop_frame
        if frame is None:
            state["waiting"] = state.get("waiting", 0) + 1
            if state["waiting"] > 20:
                fail("No live desktop frame after transition")
            else:
                QTimer.singleShot(50, capture)
            return
        state["waiting"] = 0
        source = frame.image
        ratio_x = source.width() / frame.rect.width()
        ratio_y = source.height() / frame.rect.height()
        origin = window.mapToGlobal(QPoint(0, 0))
        background = source.copy(
            round((origin.x() - frame.rect.x()) * ratio_x),
            round((origin.y() - frame.rect.y()) * ratio_y),
            round(width * ratio_x), round(height * ratio_y))
        warped = window.grabFramebuffer()
        if warped.isNull():
            fail("Empty OpenGL output")
            return
        background = background.scaled(warped.size())
        background.setDevicePixelRatio(1.0)

        def composite(layer):
            # Compare physical pixels. QPainter otherwise shrinks a high-DPI
            # framebuffer according to its Qt DPR metadata on this 1x canvas.
            layer = layer.copy()
            layer.setDevicePixelRatio(1.0)
            result = background.copy()
            p = QPainter(result)
            p.drawImage(0, 0, layer)
            p.end()
            return result

        index = len(state["frames"])
        final = composite(warped)
        final.save(str(args.output / f"frame_{index:03d}.png"))
        state["frames"].append({
            "serial": frame.serial, "capture_ms": window._desktop_capture.last_capture_ms,
            "source_center": background.pixelColor(background.width()//2, background.height()//2).name(),
            "size": [width, height],
        })
        if index == 0:
            window.set_desktop_frame(None)
            unwarped = window.grabFramebuffer()
            window.set_desktop_frame(frame)
            original = composite(unwarped)
            original.save(str(args.output / "before.png"))
            warped.save(str(args.output / "foreground.png"))
            background.save(str(args.output / "captured_background.png"))
            board = QImage(final.width()*2, final.height()+30, QImage.Format_RGB32)
            board.fill(QColor("#101720"))
            p = QPainter(board)
            p.setPen(Qt.white)
            p.drawText(12, 21, "BEFORE - ordinary transparency")
            p.drawText(final.width()+12, 21, "LIVE DESKTOP LENS - actual captured test window")
            p.drawImage(0, 30, original)
            p.drawImage(final.width(), 30, final)
            p.end()
            board.save(str(args.output / "comparison.png"))
        if index + 1 >= args.frames:
            app.quit()
        else:
            backdrop.offset += 4
            backdrop.update()
            if args.exercise:
                if index == 0:
                    window.move(window.pos() + QPoint(60, 30))
                elif index == 1:
                    window.resize(360, 240)
                elif index == 2:
                    window._set_always_on_top(False)
                elif index == 3:
                    window._set_always_on_top(True)
                elif index == 4:
                    window.set_desktop_lens_enabled(False)
                    assert window._desktop_frame is None
                    assert not window._desktop_capture.timer.isActive()
                    assert window._desktop_capture.handle == 0
                    QTimer.singleShot(60, lambda: window.set_desktop_lens_enabled(True))
                elif index == 5:
                    window.resize(240, 160)
            QTimer.singleShot(250, capture)

    QTimer.singleShot(2400, capture)
    QTimer.singleShot(18000, lambda: fail("Capture timed out"))
    app.exec()
    window.close()
    backdrop.close()
    temporary.cleanup()
    if state["error"]:
        raise RuntimeError(state["error"])
    (args.output / "capture.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
