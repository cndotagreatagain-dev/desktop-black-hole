from __future__ import annotations

import ctypes
import inspect
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

import windows_cursor as wc


def _body_snapshot(width, height, points, hotspot=(0, 0)):
    pixels = bytearray(width * height * 4)
    for x, y, alpha in points:
        pixels[(y * width + x) * 4 + 3] = alpha
    return wc.CursorSnapshot(1, width, height, *hotspot, bytes(pixels))


def test_body_geometry_measures_alpha_centroid_not_transparent_canvas():
    points = [(1, 1, 255), (2, 4, 255), (3, 7, 128)]
    small = _body_snapshot(12, 12, points)
    padded = _body_snapshot(64, 64, points)
    axis_x, axis_y, extent = wc.cursor_body_geometry(small)
    weight = sum(p[2] for p in points)
    x = sum((p[0] + 0.5) * p[2] for p in points) / weight
    y = -sum((p[1] + 0.5) * p[2] for p in points) / weight
    assert (axis_x, axis_y) == pytest.approx((x / math.hypot(x, y), y / math.hypot(x, y)))
    assert extent == pytest.approx(max((px + 0.5)*axis_x - (py + 0.5)*axis_y
                                     for px, py, alpha in points) + 0.75)
    assert wc.cursor_body_geometry(padded) == pytest.approx((axis_x, axis_y, extent))


def test_body_geometry_uses_hotspot_relative_gl_coordinates():
    original = _body_snapshot(16, 16, [(2, 3, 255), (4, 9, 255)], hotspot=(1, 2))
    shifted = _body_snapshot(32, 32, [(9, 8, 255), (11, 14, 255)], hotspot=(8, 7))
    assert wc.cursor_body_geometry(original) == pytest.approx(wc.cursor_body_geometry(shifted))


@pytest.mark.parametrize("points", [[], [(1, 1, 255), (2, 1, 255), (1, 2, 255), (2, 2, 255)]])
def test_centered_or_empty_body_has_stable_finite_fallback(points):
    axis_x, axis_y, extent = wc.cursor_body_geometry(_body_snapshot(4, 4, points, hotspot=(2, 2)))
    assert (axis_x, axis_y) == pytest.approx((0.35 / math.hypot(0.35, 1), -1 / math.hypot(0.35, 1)))
    assert math.isfinite(extent) and extent >= 4.0


def _composite_over_white(pixels_bgra: bytes) -> bytes:
    result = bytearray()
    for blue, green, red, alpha in zip(*[iter(pixels_bgra)] * 4):
        background = 255 - alpha
        result.extend(
            (
                min(255, blue + background),
                min(255, green + background),
                min(255, red + background),
                0,
            )
        )
    return bytes(result)


class FakeCaptureApi:
    def __init__(self) -> None:
        self.shared_handle = 101
        self.copied_handle = 202
        self.info = wc._NativeIconInfo(
            is_icon=False,
            hotspot_x=1,
            hotspot_y=0,
            hbm_mask=303,
            hbm_color=404,
        )
        self.sizes = {303: (2, 2), 404: (2, 2)}
        self.expected_pixels = bytes(
            (
                0,
                0,
                0,
                0,
                20,
                10,
                5,
                64,
                120,
                100,
                80,
                128,
                255,
                200,
                40,
                255,
            )
        )
        self.black_pixels = bytes(
            channel if index % 4 != 3 else 0
            for index, channel in enumerate(self.expected_pixels)
        )
        self.white_pixels = _composite_over_white(self.expected_pixels)
        self.calls: list[tuple[object, ...]] = []
        self.fail_at: str | None = None

    def current_visible_cursor(self) -> int | None:
        self.calls.append(("current_visible_cursor",))
        if self.fail_at == "current_visible_cursor":
            raise OSError("cursor info failed")
        return self.shared_handle

    def copy_icon(self, shared_cursor: int) -> int:
        self.calls.append(("copy_icon", shared_cursor))
        if self.fail_at == "copy_icon":
            raise OSError("copy failed")
        return self.copied_handle

    def get_icon_info(self, copied_icon: int) -> wc._NativeIconInfo:
        self.calls.append(("get_icon_info", copied_icon))
        if self.fail_at == "get_icon_info":
            raise OSError("icon info failed")
        return self.info

    def bitmap_size(self, bitmap: int) -> tuple[int, int]:
        self.calls.append(("bitmap_size", bitmap))
        if self.fail_at == "bitmap_size":
            raise OSError("bitmap size failed")
        return self.sizes[bitmap]

    def render_bgra(
        self,
        copied_icon: int,
        width: int,
        height: int,
        background: tuple[int, int, int],
    ) -> bytes:
        self.calls.append(
            ("render_bgra", copied_icon, width, height, background)
        )
        if self.fail_at == "render_black" and background == (0, 0, 0):
            raise OSError("black render failed")
        if self.fail_at == "render_white" and background == (255, 255, 255):
            raise OSError("white render failed")
        if background == (0, 0, 0):
            return self.black_pixels
        if background == (255, 255, 255):
            return self.white_pixels
        raise AssertionError(f"unexpected background: {background!r}")

    def delete_object(self, handle: int) -> None:
        self.calls.append(("delete_object", handle))

    def destroy_icon(self, handle: int) -> None:
        self.calls.append(("destroy_icon", handle))


def _cleanup_calls(api: FakeCaptureApi) -> list[tuple[object, ...]]:
    return [
        call
        for call in api.calls
        if call[0] in {"delete_object", "destroy_icon"}
    ]


def test_hidden_cursor_returns_none_without_copying_or_deleting_shared_handle():
    api = FakeCaptureApi()
    api.shared_handle = 0

    assert wc.WindowsCursorProvider(api).capture() is None
    assert api.calls == [("current_visible_cursor",)]


def test_valid_cursor_returns_tight_top_down_premultiplied_snapshot_and_cleans_up():
    api = FakeCaptureApi()

    snapshot = wc.WindowsCursorProvider(api).capture()

    assert snapshot == wc.CursorSnapshot(
        source_handle=api.shared_handle,
        width=2,
        height=2,
        hotspot_x=1,
        hotspot_y=0,
        pixels_bgra=api.expected_pixels,
    )
    assert snapshot.stride == 8
    assert all(
        channel <= alpha
        for blue, green, red, alpha in zip(*[iter(snapshot.pixels_bgra)] * 4)
        for channel in (blue, green, red)
    )
    assert _cleanup_calls(api) == [
        ("delete_object", api.info.hbm_mask),
        ("delete_object", api.info.hbm_color),
        ("destroy_icon", api.copied_handle),
    ]
    assert ("destroy_icon", api.shared_handle) not in api.calls


@pytest.mark.parametrize("failure", ["bitmap_size", "render_black", "render_white"])
def test_every_failure_after_get_icon_info_releases_both_bitmaps_and_copy(failure):
    api = FakeCaptureApi()
    api.fail_at = failure

    assert wc.WindowsCursorProvider(api).capture() is None
    assert _cleanup_calls(api) == [
        ("delete_object", api.info.hbm_mask),
        ("delete_object", api.info.hbm_color),
        ("destroy_icon", api.copied_handle),
    ]


def test_get_icon_info_failure_releases_only_the_copied_icon():
    api = FakeCaptureApi()
    api.fail_at = "get_icon_info"

    assert wc.WindowsCursorProvider(api).capture() is None
    assert _cleanup_calls(api) == [("destroy_icon", api.copied_handle)]


@pytest.mark.parametrize(
    ("size", "hotspot"),
    [
        ((0, 2), (0, 0)),
        ((257, 2), (0, 0)),
        ((2, 2), (2, 0)),
        ((2, 2), (0, 2)),
    ],
)
def test_invalid_dimensions_and_hotspots_return_none_and_release_resources(
    size, hotspot
):
    api = FakeCaptureApi()
    api.sizes[api.info.hbm_color] = size
    api.info = wc._NativeIconInfo(
        is_icon=False,
        hotspot_x=hotspot[0],
        hotspot_y=hotspot[1],
        hbm_mask=api.info.hbm_mask,
        hbm_color=api.info.hbm_color,
    )

    assert wc.WindowsCursorProvider(api).capture() is None
    assert _cleanup_calls(api)[-1] == ("destroy_icon", api.copied_handle)


def test_monochrome_cursor_uses_half_the_mask_height():
    api = FakeCaptureApi()
    api.info = wc._NativeIconInfo(
        is_icon=False,
        hotspot_x=1,
        hotspot_y=1,
        hbm_mask=api.info.hbm_mask,
        hbm_color=0,
    )
    api.sizes[api.info.hbm_mask] = (2, 4)

    snapshot = wc.WindowsCursorProvider(api).capture()

    assert snapshot is not None
    assert (snapshot.width, snapshot.height) == (2, 2)
    assert ("delete_object", 0) not in api.calls


@pytest.mark.parametrize(
    ("black_pixels", "white_pixels"),
    [
        (bytes(15), bytes(16)),
        (bytes(16), bytes(15)),
        (bytes(16), bytes((255, 255, 255, 0)) * 4),
        (bytes(16), bytes((255, 0, 0, 0)) * 4),
        (bytes(16), bytes((255, 250, 255, 0)) * 4),
        (bytes((255, 255, 255, 0)) * 4, bytes(16)),
    ],
)
def test_invalid_buffers_transparency_and_xor_pixels_return_none(
    black_pixels, white_pixels
):
    api = FakeCaptureApi()
    api.black_pixels = black_pixels
    api.white_pixels = white_pixels

    assert wc.WindowsCursorProvider(api).capture() is None
    assert _cleanup_calls(api)[-1] == ("destroy_icon", api.copied_handle)


def test_alpha_tolerance_is_rounded_and_stored_color_is_clamped_to_alpha():
    black = bytes((102, 0, 0, 0))
    white = bytes((255, 155, 155, 0))

    assert wc._reconstruct_premultiplied_bgra(black, white, 1, 1) == bytes(
        (101, 0, 0, 101)
    )


def test_maximum_snapshot_is_bounded_to_256_square_texture():
    width = height = 256
    pixels = bytes((1, 1, 1, 255)) * (width * height)
    snapshot = wc.CursorSnapshot(1, width, height, 0, 0, pixels)

    assert len(snapshot.pixels_bgra) == 256 * 256 * 4
    with pytest.raises(ValueError):
        wc.CursorSnapshot(1, 257, 1, 0, 0, bytes(257 * 4))


def test_provider_accepts_at_most_a_256_square_texture():
    api = FakeCaptureApi()
    api.sizes[api.info.hbm_color] = (256, 256)
    opaque_pixel = bytes((1, 1, 1, 0))
    api.black_pixels = opaque_pixel * (256 * 256)
    api.white_pixels = opaque_pixel * (256 * 256)

    snapshot = wc.WindowsCursorProvider(api).capture()

    assert snapshot is not None
    assert len(snapshot.pixels_bgra) == wc.MAX_CURSOR_TEXTURE_BYTES
    assert len(snapshot.pixels_bgra) == 256 * 256 * 4


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_handle": 0},
        {"width": 0},
        {"height": 257},
        {"hotspot_x": -1},
        {"hotspot_y": 2},
        {"pixels_bgra": b"too short"},
    ],
)
def test_cursor_snapshot_rejects_invalid_public_contract(kwargs):
    values = {
        "source_handle": 1,
        "width": 2,
        "height": 2,
        "hotspot_x": 0,
        "hotspot_y": 0,
        "pixels_bgra": bytes(16),
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        wc.CursorSnapshot(**values)


class FakeLowLevelTables:
    def __init__(
        self,
        *,
        dib_bits: bool = True,
        select_result: object = 401,
        draw_result: object = 1,
    ) -> None:
        self.buffer = ctypes.create_string_buffer(16)
        self.calls: list[tuple[object, ...]] = []
        self.dib_bits = dib_bits
        self.select_result = select_result
        self.draw_result = draw_result
        self.user32 = SimpleNamespace(DrawIconEx=self.draw_icon_ex)
        self.gdi32 = SimpleNamespace(
            CreateCompatibleDC=self.create_compatible_dc,
            CreateDIBSection=self.create_dib_section,
            SelectObject=self.select_object,
            DeleteObject=self.delete_object,
            DeleteDC=self.delete_dc,
        )

    def create_compatible_dc(self, source_dc):
        self.calls.append(("CreateCompatibleDC", wc._handle_value(source_dc)))
        return 301

    def create_dib_section(self, dc, info, usage, bits, section, offset):
        self.calls.append(("CreateDIBSection", wc._handle_value(dc)))
        if self.dib_bits:
            ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(
                self.buffer
            )
        return 302

    def select_object(self, dc, bitmap):
        pair = (wc._handle_value(dc), wc._handle_value(bitmap))
        self.calls.append(("SelectObject", *pair))
        if pair[1] == 302:
            if isinstance(self.select_result, BaseException):
                raise self.select_result
            return self.select_result
        return 302

    def draw_icon_ex(self, dc, x, y, icon, width, height, step, brush, flags):
        self.calls.append(("DrawIconEx", wc._handle_value(icon), width, height))
        if isinstance(self.draw_result, BaseException):
            raise self.draw_result
        return self.draw_result

    def delete_object(self, bitmap):
        self.calls.append(("DeleteObject", wc._handle_value(bitmap)))
        return 1

    def delete_dc(self, dc):
        self.calls.append(("DeleteDC", wc._handle_value(dc)))
        return 1


def _assert_low_level_render_cleanup(tables: FakeLowLevelTables) -> None:
    assert ("DeleteObject", 302) in tables.calls
    assert ("DeleteDC", 301) in tables.calls


def test_dib_bits_failure_deletes_dib_and_memory_dc():
    tables = FakeLowLevelTables(dib_bits=False)
    api = wc._CtypesWin32Api(tables.user32, tables.gdi32)

    with pytest.raises(OSError):
        api.render_bgra(99, 2, 2, (0, 0, 0))

    _assert_low_level_render_cleanup(tables)
    assert not any(call[0] == "DrawIconEx" for call in tables.calls)


@pytest.mark.parametrize("select_result", [0, wc._HGDI_ERROR, OSError("select exploded")])
def test_select_failure_deletes_dib_and_memory_dc_without_drawing(select_result):
    tables = FakeLowLevelTables(select_result=select_result)
    api = wc._CtypesWin32Api(tables.user32, tables.gdi32)

    with pytest.raises(OSError):
        api.render_bgra(99, 2, 2, (0, 0, 0))

    _assert_low_level_render_cleanup(tables)
    assert not any(call[0] == "DrawIconEx" for call in tables.calls)


@pytest.mark.parametrize("draw_result", [0, OSError("draw exploded")])
def test_draw_failure_restores_selection_then_deletes_dib_and_memory_dc(draw_result):
    tables = FakeLowLevelTables(draw_result=draw_result)
    api = wc._CtypesWin32Api(tables.user32, tables.gdi32)

    with pytest.raises(OSError):
        api.render_bgra(99, 2, 2, (255, 255, 255))

    assert ("SelectObject", 301, 401) in tables.calls
    _assert_low_level_render_cleanup(tables)


def test_failure_after_draw_restores_selection_and_cleans_objects(monkeypatch):
    tables = FakeLowLevelTables(draw_result=1)
    api = wc._CtypesWin32Api(tables.user32, tables.gdi32)
    monkeypatch.setattr(wc.ctypes, "string_at", lambda *_: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        api.render_bgra(99, 2, 2, (0, 0, 0))

    assert ("SelectObject", 301, 401) in tables.calls
    _assert_low_level_render_cleanup(tables)


def test_default_provider_is_lazy_and_unavailable_dlls_are_safe(monkeypatch):
    provider = wc.WindowsCursorProvider()

    class UnavailableApi:
        def __init__(self):
            raise OSError("not Windows")

    monkeypatch.setattr(wc, "_CtypesWin32Api", UnavailableApi)

    assert provider.capture() is None


def test_cursor_capture_module_has_no_ui_or_gpu_side_effect_dependencies():
    source = Path(inspect.getsourcefile(wc) or "").read_text(encoding="utf-8")

    assert "PySide6" not in source
    assert "OpenGL" not in source
    assert "Show" + "Cursor" not in source
    assert "Set" + "Cursor" not in source
