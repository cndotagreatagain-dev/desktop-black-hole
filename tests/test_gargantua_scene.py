"""Regression checks after human inspection of real production captures.

These verify physical/output contracts, not likeness scores.
"""
import math
from functools import lru_cache

import pytest

from gl_test_support import SceneRenderOptions, render_scene

WIDTH, HEIGHT = 360, 240
CX, CY = WIDTH // 2, HEIGHT // 2


@lru_cache(maxsize=24)
def _render(time=0.7, quality=1.0, debug=0.0):
    return render_scene(SceneRenderOptions(
        render_size=(WIDTH, HEIGHT), render_time=time,
        quality=quality, debug_view=debug,
    ), production_resolve=True)


def _pixel(frame, x, y):
    offset = (y * frame.width + x) * 4
    return tuple(frame.pixels[offset:offset + 4])


def _luminance(pixel):
    return sum(a * b for a, b in zip(pixel, (0.2126, 0.7152, 0.0722)))


def test_gpu_capture_boundary_matches_schwarzschild_critical_impact():
    frame = _render(debug=1.0)
    observer = math.hypot(50.0, 3.1)
    critical_impact = 1.5 * math.sqrt(3.0)
    for dx, dy in ((0, 12), (0, 28), (28, 0), (-28, 0), (0, 46), (46, 0), (-46, 0)):
        x, y = CX + dx, CY + dy
        sx = ((x + 0.5) / WIDTH * 2.0 - 1.0) * WIDTH / HEIGHT
        sy = (y + 0.5) / HEIGHT * 2.0 - 1.0
        transverse = math.hypot(sx, sy) / math.sqrt(6.4 ** 2 + sx ** 2 + sy ** 2)
        impact = observer * transverse / math.sqrt(1.0 - 1.0 / observer)
        red, _, blue, _ = _pixel(frame, x, y)
        assert (red > blue) == (impact < critical_impact)


def test_multiple_disk_crossings_are_present_near_the_critical_curve():
    frame = _render(debug=2.0, quality=2.0)
    crossings = [round(red / 255.0 * 6.0) for red in frame.pixels[0::4]]
    assert max(crossings) >= 2
    assert crossings.count(0) > WIDTH * HEIGHT // 2


def test_captured_core_is_opaque_black_on_both_sides_of_the_foreground_disk():
    frame = _render()
    for x in range(CX - 20, CX + 21):
        for y in (CY + 12, CY - 18):
            assert _pixel(frame, x, y) == (0, 0, 0, 255)


def test_upper_and_lower_lensed_images_have_continuous_bright_arcs():
    frame = _render()
    for x in range(CX - 48, CX + 49):
        for rows in (range(CY + 15, CY + 88), range(CY - 88, CY - 15)):
            assert any(_luminance(_pixel(frame, x, y)) > 100 for y in rows)


def test_outer_foreground_disk_stays_thin_and_hot_inner_emission_is_warm_white():
    frame = _render()
    for dx in (-105, 105):
        axis = CY + round(0.03 * dx)
        bright_rows = [
            y for y in range(axis - 25, axis + 26)
            if _luminance(_pixel(frame, CX + dx, y)) > 100
        ]
        assert bright_rows
        assert max(bright_rows) - min(bright_rows) < 18
    hot_pixels = [
        _pixel(frame, x, y)
        for x in range(CX - 55, CX + 56)
        for y in range(CY + 40, CY + 75)
        if _luminance(_pixel(frame, x, y)) > 210
    ]
    assert len(hot_pixels) > 100
    assert all(r >= g >= b and r - b < 80 for r, g, b, _ in hot_pixels)


@pytest.mark.parametrize("quality", (0.0, 1.0, 2.0))
def test_quality_tiers_render_nonempty_premultiplied_frames_with_clear_edges(quality):
    frame = _render(quality=quality)
    border = (
        [_pixel(frame, x, 0) for x in range(WIDTH)]
        + [_pixel(frame, x, HEIGHT - 1) for x in range(WIDTH)]
        + [_pixel(frame, 0, y) for y in range(HEIGHT)]
        + [_pixel(frame, WIDTH - 1, y) for y in range(HEIGHT)]
    )
    assert all(pixel == (0, 0, 0, 0) for pixel in border)
    pixels = list(zip(*(frame.pixels[i::4] for i in range(4))))
    assert all(max(r, g, b) <= a for r, g, b, a in pixels)
    assert sum(max(r, g, b) > 160 for r, g, b, a in pixels) > 1000


def test_orbital_material_moves_but_the_geodesic_capture_boundary_does_not():
    first, second = _render(0.7), _render(1.7)
    assert sum(abs(a - b) > 3 for a, b in zip(first.pixels, second.pixels)) > 1500
    assert _render(0.7, debug=1.0).pixels == _render(1.7, debug=1.0).pixels


@pytest.mark.parametrize("time", (60.0, 300.0, 3600.0))
def test_long_running_material_keeps_moving_with_a_stable_black_shadow(time):
    first, second = _render(time), _render(time + 0.5)
    assert sum(abs(a - b) > 3 for a, b in zip(first.pixels, second.pixels)) > 1500
    assert _render(time, debug=1.0).pixels == _render(0.7, debug=1.0).pixels
    for y in (CY + 12, CY - 18):
        assert _pixel(first, CX, y) == (0, 0, 0, 255)


def test_backing_slightly_darkens_white_without_becoming_an_opaque_panel():
    frame = _render(300.0)
    # Empty space outside the thin foreground disk and the lensed image.
    for dx in (-90, 90):
        r, g, b, alpha = _pixel(frame, CX + dx, CY + 45)
        assert 10 <= alpha <= 55
        white_composite = tuple(channel + 255 - alpha for channel in (r, g, b))
        assert max(white_composite) < 250
        assert min(white_composite) > 200


def test_mouse_and_drag_inputs_do_not_change_scene_geometry():
    idle = _render()
    active = render_scene(SceneRenderOptions(
        render_size=(WIDTH, HEIGHT), mouse=(270.0, 156.0),
        mouse_inside=1.0, drag_strength=1.0,
    ), production_resolve=True)
    assert max(abs(a - b) for a, b in zip(idle.pixels, active.pixels)) <= 1


def test_display_resolve_preserves_subpixel_coverage_after_tone_mapping():
    # One bright and one black physical source column per output pixel.
    # Bloom extraction is zero at radiance <= 1. Averaging HDR before ACES
    # would make the entire output much brighter instead of half covered.
    source = r"""#version 330 core
out vec4 fragColor;
void main() {
    float stripe = mod(floor(gl_FragCoord.x), 2.0);
    fragColor = vec4(vec3(stripe), 1.0);
}
"""
    frame = render_scene(SceneRenderOptions(render_size=(24, 16)),
                         production_resolve=True, fragment_source_override=source)
    _, green, _, alpha = _pixel(frame, 12, 8)
    assert alpha == 255
    assert 100 <= green <= 125
