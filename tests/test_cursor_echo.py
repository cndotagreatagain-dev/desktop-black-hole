"""GL contracts for native-silhouette echoes, after visual inspection."""
from dataclasses import replace

from gl_test_support import CursorRenderOptions, render_cursor_shader


def _options():
    pixels = bytearray(32*32*4)
    for y in range(2, 25):
        for x in range(4, min(19, 5+(y-2)//2)):
            index = (y*32+x)*4
            pixels[index:index+4] = bytes((180, 220, 250, 255))
    return CursorRenderOptions(
        cursor_bgra=bytes(pixels), cursor_size=(32, 32),
        pointer_px=(108.0, 82.0), cursor_top_left_px=(104.0, 84.0),
        lens_center_px=(64.0, 60.0), lens_half_width_px=20.0,
        max_displacement_px=46.0, max_tangent_scale=1.72,
        body_axis=(0.35, -1.0), body_extent=26.0,
    )


def test_empty_native_cursor_never_emits_a_procedural_light_cord():
    options = replace(_options(), cursor_bgra=bytes(32*32*4))
    frame, _ = render_cursor_shader(options)
    assert not any(frame.pixels)


def test_echo_cycle_renews_continuously_at_zero_opacity():
    options = _options()
    before, _ = render_cursor_shader(replace(options, cursor_time=(1-0.0001)/0.28))
    after, _ = render_cursor_shader(replace(options, cursor_time=(1+0.0001)/0.28))
    assert max(abs(a-b) for a,b in zip(before.pixels, after.pixels)) <= 3


def test_echoes_remain_premultiplied_and_move_after_one_hour():
    options = replace(_options(), cursor_time=3600.0)
    first, _ = render_cursor_shader(options)
    later, _ = render_cursor_shader(replace(options, cursor_time=3600.5))
    assert sum(abs(a-b)>5 for a,b in zip(first.pixels, later.pixels)) > 40
    for frame in (first, later):
        assert all(max(frame.pixels[i:i+3]) <= frame.pixels[i+3]+1
                   for i in range(0,len(frame.pixels),4))


def test_center_crossing_has_no_ghost_direction_flip():
    options = _options()
    left, _ = render_cursor_shader(replace(options, lens_center_px=(107.99,82.0)))
    right, _ = render_cursor_shader(replace(options, lens_center_px=(108.01,82.0)))
    assert max(abs(a-b) for a,b in zip(left.pixels,right.pixels)) <= 3
