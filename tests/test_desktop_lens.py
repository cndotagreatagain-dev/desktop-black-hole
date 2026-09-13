import queue
import threading
from functools import lru_cache

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, QSize, QEvent, QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import desktop_capture as capture
from desktop_capture import (
    CaptureWorker, DesktopCapture, capture_rect, desktop_transform, physical_capture_rect,
)
from gl_test_support import SceneRenderOptions, render_scene


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("dpr", (1, 1.25, 1.5, 2))
def test_roi_transform_handles_negative_monitor_origin_and_dpr(dpr):
    widget = QRect(-1500, 200, 600, 400)
    monitor = QRect(-1920, 0, 1920, 1080)
    roi = capture_rect(widget, monitor)
    assert monitor.contains(roi)
    ox, oy, sx, sy = desktop_transform(widget, roi)
    assert ox * roi.width() + roi.x() == widget.x()
    assert oy * roi.height() + roi.y() == widget.y()
    assert sx * roi.width() == widget.width()
    assert sy * roi.height() == widget.height()
    physical = physical_capture_rect((-2100, 300), widget, roi, dpr)
    assert physical[:2] == (
        -2100 + round((roi.x() - widget.x()) * dpr),
        300 + round((roi.y() - widget.y()) * dpr))
    assert physical[2:] == (round(roi.width()*dpr), round(roi.height()*dpr))


def test_roi_is_clipped_to_current_monitor_without_zero_division():
    roi = capture_rect(QRect(1800, 900, 360, 240), QRect(0, 0, 1920, 1080))
    assert roi.right() == 1919 and roi.bottom() == 1079
    with pytest.raises(ValueError):
        desktop_transform(QRect(0, 0, 360, 240), QRect())


class FakeWindow(QObject):
    def __init__(self):
        super().__init__()
        self._menu_open = False
        self._closing = False
        self._debug_view = 0
        self.position = QPoint(100, 100)

    def isVisible(self): return True
    def isMinimized(self): return False
    def winId(self): return 123
    def screen(self): return self
    def geometry(self): return QRect(-1000, 0, 2000, 1000)
    def mapToGlobal(self, point): return self.position + point
    def size(self): return QSize(360, 240)
    def devicePixelRatioF(self): return 1


class FakeExclusion:
    def __init__(self):
        self.calls = []
        self.valid = True
    def set(self, handle, enabled): self.calls.append((handle, enabled))
    def client_origin(self, handle): return (100, 100)
    def verify(self, handle):
        if not self.valid:
            raise RuntimeError("exclusion lost")


class FakeWorker:
    def __init__(self):
        self.requests = queue.Queue()
        self.results = queue.Queue()
        self.closed = False
    def close(self): self.closed = True


@pytest.fixture
def controller(app, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: now[0])
    window, exclusion, worker = FakeWindow(), FakeExclusion(), FakeWorker()
    provider = DesktopCapture(window, exclusion_factory=lambda: exclusion,
                              worker_factory=lambda: worker)
    delivered, failures = [], []
    provider.frame_ready.connect(delivered.append)
    provider.failed.connect(failures.append)
    provider.set_enabled(True)
    provider.poll()  # Exclusion must precede any readback.
    assert worker.requests.empty()
    now[0] = 1.0
    yield provider, window, exclusion, worker, delivered, failures, now
    provider.stop()


def test_capture_stops_and_restores_exclusion_without_a_frame_backlog(controller):
    provider, _, exclusion, worker, delivered, _, _ = controller
    provider.poll()
    assert worker.requests.qsize() == 1
    provider.poll()
    assert worker.requests.qsize() == 1
    provider.stop()
    assert delivered[-1] is None
    assert worker.closed
    assert exclusion.calls == [(123, True), (123, False)]
    assert not provider.timer.isActive()


def test_inflight_capture_after_window_recreation_is_discarded(controller):
    provider, window, _, worker, delivered, _, _ = controller
    provider.poll()
    token, _, rect = worker.requests.get_nowait()
    provider.eventFilter(window, QEvent(QEvent.WinIdChange))
    worker.results.put((token, rect, QImage(1, 1, QImage.Format_RGBA8888), 20, ""))
    provider.poll()
    assert provider.serial == 0
    assert delivered[-1] is None


def test_move_keeps_global_roi_valid_while_requesting_new_background(controller):
    provider, window, _, worker, delivered, _, _ = controller
    provider.poll()
    token, _, rect = worker.requests.get_nowait()
    window.position += QPoint(20, 15)
    provider.eventFilter(window, QEvent(QEvent.Move))
    worker.results.put((token, rect, QImage(1, 1, QImage.Format_RGBA8888), 20, ""))
    provider.poll()
    assert provider.serial == 1
    assert delivered[-1].rect == rect
    _, _, new_rect = worker.requests.get_nowait()
    assert new_rect.topLeft() == rect.topLeft() + QPoint(20, 15)


def test_capture_failure_is_nonfatal_and_clears_background(controller):
    provider, _, exclusion, worker, delivered, failures, _ = controller
    provider.poll()
    token, _, rect = worker.requests.get_nowait()
    worker.results.put((token, rect, None, 0, "readback failed"))
    provider.poll()
    assert len(failures) == 1 and "readback failed" in failures[0]
    assert not provider.enabled and delivered[-1] is None
    assert exclusion.calls[-1] == (123, False)


def test_lost_exclusion_refuses_recursive_capture(controller):
    provider, _, exclusion, worker, _, failures, _ = controller
    exclusion.valid = False
    provider.poll()
    assert worker.requests.empty()
    assert len(failures) == 1


def test_blocked_capture_times_out_without_stalling_gui(controller):
    provider, _, _, _, delivered, failures, now = controller
    provider.poll()
    now[0] = 4.0
    provider.poll()
    assert delivered[-1] is None
    assert "timed out" in failures[0]


def test_menu_discards_inflight_frame_and_does_not_capture_it(controller):
    provider, window, _, worker, _, _, _ = controller
    provider.poll()
    token, _, rect = worker.requests.get_nowait()
    window._menu_open = True
    provider.poll()
    window._menu_open = False
    worker.results.put((token, rect, QImage(1, 1, QImage.Format_RGBA8888), 20, ""))
    provider.poll()
    assert provider.serial == 0


def test_worker_reads_on_another_thread_and_discards_cancelled_result():
    started, release = threading.Event(), threading.Event()
    thread_ids = []
    class Grabber:
        def grab(self, rect):
            thread_ids.append(threading.get_ident())
            started.set()
            release.wait(1)
            return QImage(1, 1, QImage.Format_RGBA8888)
    worker = CaptureWorker(Grabber)
    worker.requests.put((1, (0, 0, 1, 1), QRect(0, 0, 1, 1)))
    assert started.wait(1)
    worker.close()
    release.set()
    worker.thread.join(1)
    assert not worker.thread.is_alive()
    assert thread_ids != [threading.get_ident()]
    assert worker.results.empty()


def test_desktop_setting_persists_and_is_opt_in_for_embedded_widgets(app, tmp_path):
    from desktop_black_hole import DesktopBlackHole
    settings = QSettings(str(tmp_path / "lens.ini"), QSettings.IniFormat)
    window = DesktopBlackHole(settings=settings)
    assert not window.desktop_lens_enabled
    window.set_desktop_lens_enabled(True)
    assert settings.value("effects/desktop_lens_enabled", type=bool)
    window.close()
    assert not window._desktop_capture.timer.isActive()
    restored = DesktopBlackHole(settings=settings)
    assert restored.desktop_lens_enabled
    restored.close()


def pixel(frame, x, y):
    index = (y * frame.width + x) * 4
    return tuple(frame.pixels[index:index+4])


@lru_cache(maxsize=8)
def rendered(transparent=False, gradient=False, quality=1):
    source = "#version 330 core\nout vec4 fragColor; void main(){fragColor=vec4(0);}"
    return render_scene(SceneRenderOptions(render_size=(240, 160), quality=quality),
                        production_resolve=True,
                        fragment_source_override=source if transparent else None,
                        desktop_color=(83, 151, 213), desktop_gradient=gradient)


def test_display_desktop_color_is_not_tonemapped_or_warm_graded():
    frame = rendered(transparent=True)
    for x, y in ((158, 85), (82, 85), (155, 64)):
        assert pixel(frame, x, y) == (83, 151, 213, 255)


def test_desktop_coordinate_orientation_is_top_down_and_not_mirrored():
    frame = rendered(transparent=True, gradient=True)
    assert pixel(frame, 165, 80)[0] > pixel(frame, 75, 80)[0]
    assert pixel(frame, 120, 125)[1] < pixel(frame, 120, 35)[1]


@pytest.mark.parametrize("quality", (0, 1, 2))
def test_desktop_cannot_leak_through_black_core_or_create_a_square_edge(quality):
    frame = rendered(quality=quality)
    for y in (68, 92):
        assert pixel(frame, 120, y) == (0, 0, 0, 255)
    for x, y in ((0, 0), (239, 159), (0, 80), (120, 0)):
        assert pixel(frame, x, y) == (0, 0, 0, 0)
    assert all(max(frame.pixels[i:i+3]) <= frame.pixels[i+3]
               for i in range(0, len(frame.pixels), 4))
