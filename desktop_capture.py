"""Local-only desktop ROI capture, enabled by default at normal app startup.

The user can disable it in the menu. No recording, network or cursor capture.
"""
from __future__ import annotations

import ctypes
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QImage


@dataclass(frozen=True)
class DesktopFrame:
    image: QImage
    rect: QRect  # Global Qt logical coordinates; image may have a different DPR.
    serial: int
    timestamp: float


def capture_rect(widget_rect: QRect, screen_rect: QRect) -> QRect:
    # The bounded optical map needs at most 1.5 critical radii of extra pixels.
    margin = math.ceil(widget_rect.height() * 0.30) + 4
    return widget_rect.adjusted(-margin, -margin, margin, margin).intersected(screen_rect)


def desktop_transform(widget_rect: QRect, frame_rect: QRect) -> tuple[float, ...]:
    if frame_rect.width() <= 0 or frame_rect.height() <= 0:
        raise ValueError("Empty desktop frame rectangle")
    return (
        (widget_rect.x() - frame_rect.x()) / frame_rect.width(),
        (widget_rect.y() - frame_rect.y()) / frame_rect.height(),
        widget_rect.width() / frame_rect.width(),
        widget_rect.height() / frame_rect.height(),
    )


def physical_capture_rect(origin, widget_rect, frame_rect, dpr):
    """Convert relative Qt DIPs using the HWND's physical client origin."""
    return (
        origin[0] + round((frame_rect.x() - widget_rect.x()) * dpr),
        origin[1] + round((frame_rect.y() - widget_rect.y()) * dpr),
        max(1, round(frame_rect.width() * dpr)),
        max(1, round(frame_rect.height() * dpr)),
    )


class NativeScreenGrab:
    """GDI readback runs only in a worker, never on the Qt/GL thread."""
    class Header(ctypes.Structure):
        _fields_ = [
            ("size", ctypes.c_uint32), ("width", ctypes.c_int32),
            ("height", ctypes.c_int32), ("planes", ctypes.c_uint16),
            ("bits", ctypes.c_uint16), ("compression", ctypes.c_uint32),
            ("image_size", ctypes.c_uint32), ("xppm", ctypes.c_int32),
            ("yppm", ctypes.c_int32), ("used", ctypes.c_uint32),
            ("important", ctypes.c_uint32),
        ]

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        pointer, integer = ctypes.c_void_p, ctypes.c_int
        signatures = (
            (self.user.GetDC, [pointer], pointer),
            (self.user.ReleaseDC, [pointer, pointer], integer),
            (self.gdi.CreateCompatibleDC, [pointer], pointer),
            (self.gdi.DeleteDC, [pointer], integer),
            (self.gdi.DeleteObject, [pointer], integer),
            (self.gdi.SelectObject, [pointer, pointer], pointer),
            (self.gdi.CreateDIBSection,
             [pointer, pointer, ctypes.c_uint32, ctypes.POINTER(pointer), pointer, ctypes.c_uint32],
             pointer),
            (self.gdi.BitBlt,
             [pointer, integer, integer, integer, integer, pointer, integer, integer, ctypes.c_uint32],
             integer),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def grab(self, rect):
        x, y, width, height = rect
        screen_dc = memory_dc = bitmap = previous = None
        try:
            screen_dc = self.user.GetDC(None)
            memory_dc = self.gdi.CreateCompatibleDC(screen_dc)
            header = self.Header()
            header.size = ctypes.sizeof(header)
            header.width, header.height = width, -height
            header.planes, header.bits = 1, 32
            bits = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(
                screen_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            if not screen_dc or not memory_dc or not bitmap or not bits:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = self.gdi.SelectObject(memory_dc, bitmap)
            if not previous:
                raise ctypes.WinError(ctypes.get_last_error())
            # Include ordinary layered windows, but not our WDA-excluded HWND.
            # Hardware cursor is not drawn by BitBlt.
            if not self.gdi.BitBlt(memory_dc, 0, 0, width, height,
                                   screen_dc, x, y, 0x00CC0020 | 0x40000000):
                raise ctypes.WinError(ctypes.get_last_error())
            raw = ctypes.string_at(bits, width * height * 4)
            return QImage(raw, width, height, width * 4, QImage.Format_RGB32).convertToFormat(
                QImage.Format_RGBA8888)
        finally:
            if previous and memory_dc:
                self.gdi.SelectObject(memory_dc, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory_dc:
                self.gdi.DeleteDC(memory_dc)
            if screen_dc:
                self.user.ReleaseDC(None, screen_dc)


class CaptureWorker:
    def __init__(self, grabber_factory=NativeScreenGrab):
        self.requests = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.factory = grabber_factory
        self.thread = threading.Thread(target=self._run, name="desktop-lens-capture", daemon=True)
        self.thread.start()

    def _run(self):
        grabber = None
        while not self.stopped.is_set():
            try:
                token, pixels, logical_rect = self.requests.get(timeout=0.1)
            except queue.Empty:
                continue
            started = time.perf_counter()
            try:
                if grabber is None:
                    grabber = self.factory()
                image = grabber.grab(pixels)
                result = (token, logical_rect, image,
                          (time.perf_counter() - started) * 1000, "")
            except Exception as error:
                result = (token, logical_rect, None, 0, str(error))
            if not self.stopped.is_set():
                try:
                    self.results.put_nowait(result)
                except queue.Full:
                    pass  # Never build a backlog of desktop frames.

    def close(self):
        self.stopped.set()
        # No GUI-thread join: an in-flight OS readback may take a DWM frame.
        for channel in (self.requests, self.results):
            try:
                channel.get_nowait()
            except queue.Empty:
                pass


class WindowCaptureExclusion:
    """Fail closed on Windows versions without real exclusion (not black-out)."""
    def __init__(self):
        if sys.platform != "win32" or sys.getwindowsversion().build < 19041:
            raise RuntimeError("Desktop lens requires Windows 10 2004 or newer")
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.api.SetWindowDisplayAffinity.restype = ctypes.c_int
        self.api.GetWindowDisplayAffinity.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
        ]
        self.api.GetWindowDisplayAffinity.restype = ctypes.c_int
        self.api.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.api.ClientToScreen.restype = ctypes.c_int

    def client_origin(self, handle):
        point = (ctypes.c_int32 * 2)(0, 0)
        if not self.api.ClientToScreen(handle, point):
            raise ctypes.WinError(ctypes.get_last_error())
        return point[0], point[1]

    def set(self, handle: int, enabled: bool) -> None:
        affinity = 0x11 if enabled else 0
        if not self.api.SetWindowDisplayAffinity(handle, affinity):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = ctypes.c_uint32()
        if (not self.api.GetWindowDisplayAffinity(handle, ctypes.byref(actual))
                or actual.value != affinity):
            raise RuntimeError("Windows did not confirm capture exclusion")

    def verify(self, handle: int) -> None:
        actual = ctypes.c_uint32()
        if (not self.api.GetWindowDisplayAffinity(handle, ctypes.byref(actual))
                or actual.value != 0x11):
            raise RuntimeError("Capture exclusion was lost; refusing recursive capture")


class DesktopCapture(QObject):
    frame_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, window, *, exclusion_factory=WindowCaptureExclusion,
                 worker_factory=CaptureWorker):
        super().__init__(window)
        self.window = window
        self.exclusion_factory = exclusion_factory
        self.worker_factory = worker_factory
        self.worker = None
        self.pending = False
        self.generation = 0
        self.last_frame_time = 0
        self.request_started = 0
        self.exclusion = None
        self.handle = 0
        self.enabled = False
        self.error = ""
        self.serial = 0
        self.last_capture_ms = 0.0
        self._ready_after = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.poll)
        window.installEventFilter(self)

    def set_enabled(self, enabled: bool) -> None:
        self.stop()
        self.error = ""
        self.enabled = bool(enabled)
        if self.enabled:
            self.timer.start()

    def stop(self) -> None:
        self.generation += 1
        if self.worker is not None:
            self.worker.close()
            self.worker = None
        self.pending = False
        self.last_frame_time = 0
        self.enabled = False
        self.timer.stop()
        self.frame_ready.emit(None)
        if self.handle and self.exclusion is not None:
            try:
                self.exclusion.set(self.handle, False)
            except (OSError, RuntimeError):
                pass  # A window recreation may already have destroyed this HWND.
        self.handle = 0
        self.exclusion = None

    def fail(self, message: str) -> None:
        if self.error:
            return
        self.stop()
        self.error = str(message)
        self.failed.emit(self.error)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in (
            QEvent.Hide, QEvent.WinIdChange, QEvent.ScreenChangeInternal,
        ):
            self.generation += 1
            self.frame_ready.emit(None)
        # Move/resize do not invalidate a global-coordinate ROI. Its padding
        # remains usable while the next ROI is in flight, avoiding flicker
        # during continuous dragging. The shader rejects out-of-ROI samples.
        return False

    def poll(self) -> None:
        if not self.enabled:
            return
        window = self.window
        if (not window.isVisible() or window.isMinimized()
                or getattr(window, "_menu_open", False)
                or getattr(window, "_closing", False)
                or getattr(window, "_debug_view", 0)):
            self.generation += 1
            self.frame_ready.emit(None)
            return
        try:
            if self.worker is not None:
                try:
                    token, rect, image, elapsed, error = self.worker.results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    self.pending = False
                    if token == self.generation:
                        if error:
                            raise RuntimeError(error)
                        self.serial += 1
                        self.last_capture_ms = elapsed
                        self.last_frame_time = time.monotonic()
                        self.frame_ready.emit(DesktopFrame(image, rect, self.serial, self.last_frame_time))
            if self.last_frame_time and time.monotonic() - self.last_frame_time > 0.25:
                self.frame_ready.emit(None)
            if self.pending:
                if time.monotonic() - self.request_started > 2.0:
                    raise RuntimeError("Desktop readback timed out")
                return
            handle = int(window.winId())
            if handle != self.handle:
                if self.exclusion is None:
                    self.exclusion = self.exclusion_factory()
                self.exclusion.set(handle, True)
                self.handle = handle
                # Let DWM publish an excluded frame before reading the ROI.
                self._ready_after = time.monotonic() + 0.12
                self.frame_ready.emit(None)
                return
            if time.monotonic() < self._ready_after:
                return
            self.exclusion.verify(handle)
            screen = window.screen()
            if screen is None:
                raise RuntimeError("The desktop screen is unavailable")
            geometry = QRect(window.mapToGlobal(QPoint(0, 0)), window.size())
            rect = capture_rect(geometry, screen.geometry())
            if rect.isEmpty():
                self.frame_ready.emit(None)
                return
            pixels = physical_capture_rect(
                self.exclusion.client_origin(handle), geometry, rect, window.devicePixelRatioF())
            if self.worker is None:
                self.worker = self.worker_factory()
            self.worker.requests.put_nowait((self.generation, pixels, QRect(rect)))
            self.pending = True
            self.request_started = time.monotonic()
        except Exception as error:
            self.fail(f"Desktop lens capture unavailable: {error}")
