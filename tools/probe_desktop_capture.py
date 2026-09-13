"""Probe self-exclusion using only a controlled test-window crop."""
import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtMultimedia import QScreenCapture, QMediaCaptureSession, QVideoSink


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    background = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    background.setStyleSheet("background: #204878;")
    background.setGeometry(100, 150, 420, 260)
    background.show()
    overlay = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    overlay.setStyleSheet("background: #ff3090;")
    overlay.setGeometry(200, 220, 120, 100)
    overlay.show()
    capture = QScreenCapture()
    session = QMediaCaptureSession()
    sink = QVideoSink()
    session.setScreenCapture(capture)
    session.setVideoSink(sink)
    screen = background.screen()
    capture.setScreen(screen)
    state = {"frames": 0}

    def begin():
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        api.SetWindowDisplayAffinity.restype = ctypes.c_int
        ok = api.SetWindowDisplayAffinity(int(overlay.winId()), 0x11)
        print("exclusion", ok, ctypes.get_last_error(), flush=True)
        QTimer.singleShot(400, take_gdi)

    def take_gdi():
        p = background.pos() - screen.geometry().topLeft()
        image = screen.grabWindow(0, p.x(), p.y(), 420, 260).toImage()
        image.save(str(output / "gdi_excluded.png"))
        print("gdi", image.size(), image.pixelColor(150, 100).name(), flush=True)
        capture.start()

    def frame(frame):
        state["frames"] += 1
        if state["frames"] < 8:
            return
        image = frame.toImage()
        dpr_x = image.width() / screen.geometry().width()
        dpr_y = image.height() / screen.geometry().height()
        p = background.pos() - screen.geometry().topLeft()
        crop = image.copy(round(p.x() * dpr_x), round(p.y() * dpr_y),
                          round(420 * dpr_x), round(260 * dpr_y))
        crop.save(str(output / "qt_excluded.png"))
        print("qt", image.size(), crop.pixelColor(round(150*dpr_x), round(100*dpr_y)).name(), flush=True)
        capture.stop()
        app.quit()

    sink.videoFrameChanged.connect(frame)
    capture.errorOccurred.connect(lambda *args: print("capture error", args, flush=True))
    QTimer.singleShot(600, begin)
    QTimer.singleShot(7000, app.quit)
    result = app.exec()
    capture.stop()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
