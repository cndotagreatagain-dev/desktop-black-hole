"""Small, ownership-safe Win32 cursor snapshot provider.

The module deliberately owns only cursor capture and pixel conversion.  Shared
cursor handles are copied before inspection, and every native object created or
returned for the copy is released on all paths.
"""

from __future__ import annotations

import ctypes
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol


MAX_CURSOR_SIDE = 256
MAX_CURSOR_TEXTURE_BYTES = MAX_CURSOR_SIDE * MAX_CURSOR_SIDE * 4

_CURSOR_SHOWING = 0x00000001
_DI_NORMAL = 0x0003
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_HGDI_ERROR = ctypes.c_void_p(-1).value


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", _POINT),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_int32),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_int32),
        ("bmWidth", ctypes.c_int32),
        ("bmHeight", ctypes.c_int32),
        ("bmWidthBytes", ctypes.c_int32),
        ("bmPlanes", ctypes.c_uint16),
        ("bmBitsPixel", ctypes.c_uint16),
        ("bmBits", ctypes.c_void_p),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", _RGBQUAD * 1),
    ]


def _handle_value(handle: object) -> int:
    """Return a stable integer for an integer or ctypes handle value."""

    if handle is None:
        return 0
    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", handle)
    if value is None:
        return 0
    return int(value)


def _void_handle(handle: object) -> ctypes.c_void_p:
    return ctypes.c_void_p(_handle_value(handle))


def _native_failure(operation: str) -> OSError:
    get_last_error = getattr(ctypes, "get_last_error", None)
    error_code = int(get_last_error()) if get_last_error is not None else 0
    return OSError(error_code, f"{operation} failed")


@dataclass(frozen=True, slots=True)
class CursorSnapshot:
    source_handle: int
    width: int
    height: int
    hotspot_x: int
    hotspot_y: int
    pixels_bgra: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.source_handle, int) or self.source_handle == 0:
            raise ValueError("source_handle must be a nonzero integer")
        if not isinstance(self.width, int) or not 1 <= self.width <= MAX_CURSOR_SIDE:
            raise ValueError(f"width must be in 1..{MAX_CURSOR_SIDE}")
        if not isinstance(self.height, int) or not 1 <= self.height <= MAX_CURSOR_SIDE:
            raise ValueError(f"height must be in 1..{MAX_CURSOR_SIDE}")
        if not isinstance(self.hotspot_x, int) or not 0 <= self.hotspot_x < self.width:
            raise ValueError("hotspot_x must lie inside the cursor image")
        if not isinstance(self.hotspot_y, int) or not 0 <= self.hotspot_y < self.height:
            raise ValueError("hotspot_y must lie inside the cursor image")
        if not isinstance(self.pixels_bgra, bytes):
            raise TypeError("pixels_bgra must be immutable bytes")
        expected_length = self.width * self.height * 4
        if len(self.pixels_bgra) != expected_length:
            raise ValueError(
                f"pixels_bgra has length {len(self.pixels_bgra)}, expected {expected_length}"
            )

    @property
    def stride(self) -> int:
        return self.width * 4


def cursor_body_geometry(snapshot: CursorSnapshot) -> tuple[float, float, float]:
    """Find the actual cursor body's direction/extent relative to its hotspot.

    Alpha-weighted centroid avoids stretching the transparent 32/64px canvas.
    Coordinates are GL-oriented (positive Y upwards); centered cursors use a
    stable downward direction. Computed only when a new snapshot is uploaded.
    """
    points = []
    weighted_x = weighted_y = weight = 0.0
    for index, alpha in enumerate(snapshot.pixels_bgra[3::4]):
        if not alpha:
            continue
        x = index % snapshot.width + 0.5 - snapshot.hotspot_x
        y = snapshot.hotspot_y - (index // snapshot.width + 0.5)
        points.append((x, y))
        weighted_x += x * alpha
        weighted_y += y * alpha
        weight += alpha
    x, y = (weighted_x / weight, weighted_y / weight) if weight else (0.0, 0.0)
    if math.hypot(x, y) < 0.75:
        x, y = 0.35, -1.0
    magnitude = math.hypot(x, y)
    axis_x, axis_y = x / magnitude, y / magnitude
    extent = max((px*axis_x + py*axis_y for px, py in points), default=4.0)
    return axis_x, axis_y, max(4.0, extent + 0.75)


class CursorProvider(ABC):
    @abstractmethod
    def capture(self) -> CursorSnapshot | None:
        """Capture the currently visible cursor, or return ``None`` safely."""

        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _NativeIconInfo:
    is_icon: bool
    hotspot_x: int
    hotspot_y: int
    hbm_mask: int
    hbm_color: int


class _CursorCaptureApi(Protocol):
    def current_visible_cursor(self) -> int | None: ...

    def copy_icon(self, shared_cursor: int) -> int: ...

    def get_icon_info(self, copied_icon: int) -> _NativeIconInfo: ...

    def bitmap_size(self, bitmap: int) -> tuple[int, int]: ...

    def render_bgra(
        self,
        copied_icon: int,
        width: int,
        height: int,
        background: tuple[int, int, int],
    ) -> bytes: ...

    def delete_object(self, handle: int) -> None: ...

    def destroy_icon(self, handle: int) -> None: ...


def _reconstruct_premultiplied_bgra(
    black_bgra: bytes,
    white_bgra: bytes,
    width: int,
    height: int,
) -> bytes | None:
    """Recover a premultiplied BGRA image from black/white composites.

    A two-level tolerance absorbs the integer rounding used by the native draw
    path.  Pixels needing an XOR/invert operation cannot be represented by RGBA
    and therefore reject the whole snapshot.
    """

    if width < 1 or height < 1:
        return None
    expected_length = width * height * 4
    if len(black_bgra) != expected_length or len(white_bgra) != expected_length:
        return None

    output = bytearray(expected_length)
    has_visible_pixel = False
    for offset in range(0, expected_length, 4):
        black_channels = black_bgra[offset : offset + 3]
        white_channels = white_bgra[offset : offset + 3]
        inferred_alpha = tuple(
            255 - (white_channel - black_channel)
            for black_channel, white_channel in zip(black_channels, white_channels)
        )
        if any(alpha < 0 or alpha > 255 for alpha in inferred_alpha):
            return None
        if max(inferred_alpha) - min(inferred_alpha) > 2:
            return None

        alpha = int(sum(inferred_alpha) / 3.0 + 0.5)
        if any(channel > alpha + 2 for channel in black_channels):
            return None

        output[offset] = min(black_channels[0], alpha)
        output[offset + 1] = min(black_channels[1], alpha)
        output[offset + 2] = min(black_channels[2], alpha)
        output[offset + 3] = alpha
        has_visible_pixel = has_visible_pixel or alpha != 0

    return bytes(output) if has_visible_pixel else None


class WindowsCursorProvider(CursorProvider):
    """Capture the current Windows cursor through an injectable native API."""

    def __init__(self, api: _CursorCaptureApi | None = None) -> None:
        self._api = api
        self._default_api_unavailable = False

    def _capture_api(self) -> _CursorCaptureApi | None:
        if self._api is not None:
            return self._api
        if self._default_api_unavailable:
            return None
        try:
            self._api = _CtypesWin32Api()
        except Exception:
            self._default_api_unavailable = True
            return None
        return self._api

    def capture(self) -> CursorSnapshot | None:
        api = self._capture_api()
        if api is None:
            return None

        copied_icon = 0
        icon_info: _NativeIconInfo | None = None
        try:
            shared_cursor = _handle_value(api.current_visible_cursor())
            if shared_cursor == 0:
                return None

            copied_icon = _handle_value(api.copy_icon(shared_cursor))
            if copied_icon == 0:
                return None
            icon_info = api.get_icon_info(copied_icon)

            if icon_info.hbm_color:
                width, height = api.bitmap_size(icon_info.hbm_color)
            else:
                width, mask_height = api.bitmap_size(icon_info.hbm_mask)
                if mask_height <= 0 or mask_height % 2 != 0:
                    return None
                height = mask_height // 2

            if not 1 <= width <= MAX_CURSOR_SIDE:
                return None
            if not 1 <= height <= MAX_CURSOR_SIDE:
                return None
            if not 0 <= icon_info.hotspot_x < width:
                return None
            if not 0 <= icon_info.hotspot_y < height:
                return None

            black_bgra = api.render_bgra(
                copied_icon, width, height, (0, 0, 0)
            )
            white_bgra = api.render_bgra(
                copied_icon, width, height, (255, 255, 255)
            )
            pixels_bgra = _reconstruct_premultiplied_bgra(
                black_bgra, white_bgra, width, height
            )
            if pixels_bgra is None:
                return None

            return CursorSnapshot(
                source_handle=shared_cursor,
                width=width,
                height=height,
                hotspot_x=icon_info.hotspot_x,
                hotspot_y=icon_info.hotspot_y,
                pixels_bgra=pixels_bgra,
            )
        except Exception:
            return None
        finally:
            if icon_info is not None:
                self._release_bitmap(api, icon_info.hbm_mask)
                self._release_bitmap(api, icon_info.hbm_color)
            if copied_icon:
                try:
                    api.destroy_icon(copied_icon)
                except Exception:
                    pass

    @staticmethod
    def _release_bitmap(api: _CursorCaptureApi, handle: int) -> None:
        handle_value = _handle_value(handle)
        if handle_value == 0:
            return
        try:
            api.delete_object(handle_value)
        except Exception:
            pass


class _CtypesWin32Api:
    """Thin ctypes binding whose drawing resources are locally scoped."""

    def __init__(self, user32: object | None = None, gdi32: object | None = None) -> None:
        injected = user32 is not None or gdi32 is not None
        if injected:
            if user32 is None or gdi32 is None:
                raise ValueError("user32 and gdi32 must be injected together")
            self._user32 = user32
            self._gdi32 = gdi32
            return

        if os.name != "nt":
            raise OSError("Windows cursor capture is unavailable on this platform")
        dll_loader = getattr(ctypes, "WinDLL", None)
        if dll_loader is None:
            raise OSError("Windows DLL loading is unavailable")
        self._user32 = dll_loader("user32", use_last_error=True)
        self._gdi32 = dll_loader("gdi32", use_last_error=True)
        self._bind_signatures()

    def _bind_signatures(self) -> None:
        self._user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
        self._user32.GetCursorInfo.restype = ctypes.c_int32
        self._user32.CopyIcon.argtypes = [ctypes.c_void_p]
        self._user32.CopyIcon.restype = ctypes.c_void_p
        self._user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ICONINFO)]
        self._user32.GetIconInfo.restype = ctypes.c_int32
        self._user32.DrawIconEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._user32.DrawIconEx.restype = ctypes.c_int32
        self._user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        self._user32.DestroyIcon.restype = ctypes.c_int32

        self._gdi32.GetObjectW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_void_p,
        ]
        self._gdi32.GetObjectW.restype = ctypes.c_int32
        self._gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self._gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self._gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self._gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._gdi32.SelectObject.restype = ctypes.c_void_p
        self._gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self._gdi32.DeleteObject.restype = ctypes.c_int32
        self._gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self._gdi32.DeleteDC.restype = ctypes.c_int32

    def current_visible_cursor(self) -> int | None:
        cursor_info = _CURSORINFO()
        cursor_info.cbSize = ctypes.sizeof(_CURSORINFO)
        if not self._user32.GetCursorInfo(ctypes.byref(cursor_info)):
            raise _native_failure("GetCursorInfo")
        if not cursor_info.flags & _CURSOR_SHOWING:
            return None
        handle = _handle_value(cursor_info.hCursor)
        return handle or None

    def copy_icon(self, shared_cursor: int) -> int:
        copied_icon = _handle_value(self._user32.CopyIcon(_void_handle(shared_cursor)))
        if copied_icon == 0:
            raise _native_failure("CopyIcon")
        return copied_icon

    def get_icon_info(self, copied_icon: int) -> _NativeIconInfo:
        icon_info = _ICONINFO()
        if not self._user32.GetIconInfo(
            _void_handle(copied_icon), ctypes.byref(icon_info)
        ):
            raise _native_failure("GetIconInfo")
        return _NativeIconInfo(
            is_icon=bool(icon_info.fIcon),
            hotspot_x=int(icon_info.xHotspot),
            hotspot_y=int(icon_info.yHotspot),
            hbm_mask=_handle_value(icon_info.hbmMask),
            hbm_color=_handle_value(icon_info.hbmColor),
        )

    def bitmap_size(self, bitmap: int) -> tuple[int, int]:
        native_bitmap = _BITMAP()
        copied_bytes = self._gdi32.GetObjectW(
            _void_handle(bitmap), ctypes.sizeof(_BITMAP), ctypes.byref(native_bitmap)
        )
        if not copied_bytes:
            raise _native_failure("GetObjectW")
        return int(native_bitmap.bmWidth), int(native_bitmap.bmHeight)

    def render_bgra(
        self,
        copied_icon: int,
        width: int,
        height: int,
        background: tuple[int, int, int],
    ) -> bytes:
        if not 1 <= width <= MAX_CURSOR_SIDE or not 1 <= height <= MAX_CURSOR_SIDE:
            raise ValueError("cursor render dimensions are out of bounds")
        if len(background) != 3 or any(
            not isinstance(channel, int) or not 0 <= channel <= 255
            for channel in background
        ):
            raise ValueError("background must contain three byte-valued RGB channels")

        byte_count = width * height * 4
        bitmap_info = _BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = _BI_RGB
        bitmap_info.bmiHeader.biSizeImage = byte_count

        memory_dc = 0
        dib = 0
        old_bitmap = 0
        try:
            memory_dc = _handle_value(
                self._gdi32.CreateCompatibleDC(ctypes.c_void_p())
            )
            if memory_dc == 0:
                raise _native_failure("CreateCompatibleDC")

            bits = ctypes.c_void_p()
            dib = _handle_value(
                self._gdi32.CreateDIBSection(
                    _void_handle(0),
                    ctypes.byref(bitmap_info),
                    _DIB_RGB_COLORS,
                    ctypes.byref(bits),
                    _void_handle(0),
                    0,
                )
            )
            if dib == 0 or _handle_value(bits) == 0:
                raise _native_failure("CreateDIBSection")

            red, green, blue = background
            initial_pixels = bytes((blue, green, red, 0)) * (width * height)
            ctypes.memmove(_handle_value(bits), initial_pixels, byte_count)

            old_bitmap = _handle_value(
                self._gdi32.SelectObject(
                    _void_handle(memory_dc), _void_handle(dib)
                )
            )
            if old_bitmap == 0 or old_bitmap == _HGDI_ERROR:
                old_bitmap = 0
                raise _native_failure("SelectObject")

            drawn = self._user32.DrawIconEx(
                _void_handle(memory_dc),
                0,
                0,
                _void_handle(copied_icon),
                width,
                height,
                0,
                _void_handle(0),
                _DI_NORMAL,
            )
            if not drawn:
                raise _native_failure("DrawIconEx")
            return bytes(ctypes.string_at(_handle_value(bits), byte_count))
        finally:
            if old_bitmap and memory_dc:
                try:
                    self._gdi32.SelectObject(
                        _void_handle(memory_dc), _void_handle(old_bitmap)
                    )
                except Exception:
                    pass
            if dib:
                try:
                    self._gdi32.DeleteObject(_void_handle(dib))
                except Exception:
                    pass
            if memory_dc:
                try:
                    self._gdi32.DeleteDC(_void_handle(memory_dc))
                except Exception:
                    pass

    def delete_object(self, handle: int) -> None:
        if handle:
            self._gdi32.DeleteObject(_void_handle(handle))

    def destroy_icon(self, handle: int) -> None:
        if handle:
            self._user32.DestroyIcon(_void_handle(handle))
