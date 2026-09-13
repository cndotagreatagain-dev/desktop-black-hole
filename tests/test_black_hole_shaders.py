import math
import re
from dataclasses import replace
from pathlib import Path

from black_hole_shaders import (
    CURSOR_FRAGMENT_SHADER_SOURCE,
    FULLSCREEN_VERTEX_SHADER_SOURCE,
    PROJECTED_LENS_RADIUS,
    PROJECTED_PHOTON_RADIUS,
    SCENE_FRAGMENT_SHADER_SOURCE,
)
from gl_test_support import (
    CURSOR_UNIFORM_NAMES,
    CursorRenderOptions,
    RenderedFrame,
    analyze_scene_visuals,
    render_cursor_shader,
    warm_support_mask,
)

import desktop_black_hole as dbh


def test_shader_module_exports_the_production_scene_sources():
    assert FULLSCREEN_VERTEX_SHADER_SOURCE.startswith("#version 330 core")
    assert SCENE_FRAGMENT_SHADER_SOURCE.startswith("#version 330 core")
    assert dbh.VERTEX_SHADER_SOURCE is FULLSCREEN_VERTEX_SHADER_SOURCE
    assert dbh.FRAGMENT_SHADER_SOURCE is SCENE_FRAGMENT_SHADER_SOURCE


def test_scene_is_procedural_schwarzschild_and_has_no_screen_space_fakes():
    source = SCENE_FRAGMENT_SHADER_SOURCE
    for forbidden in (
        "horseshoeArc", "horizonCoverage", "directDisk", "diskPlane",
        "abs(radius - PHOTON_ORBIT)", "sampler2D", "texture(",
    ):
        assert forbidden not in source
    assert "schwarzschildRK4" in source
    assert "nextState.x >= 1.0 / SCHWARZSCHILD_RADIUS" in source
    assert "nextDiskPhi += PI" in source
    assert "proceduralSky(lastDirection)" in source
    assert "omegaK * angularMomentum" in source
    assert "0.45 / pow(max(referenceRadius, 0.50), 0.85)" in source

    production_sources = "\n".join(
        Path(name).read_text(encoding="utf-8")
        for name in (
            "gargantua_scene_shader.py", "black_hole_shaders.py",
            "desktop_black_hole.py", "windows_cursor.py", "black_hole_postprocess.py",
        )
    )
    for capture_api in (
        "QScreen.grabWindow", "BitBlt", "PrintWindow", "glCopyTexImage",
        "glCopyTexSubImage", "glReadPixels",
    ):
        assert capture_api not in production_sources


def _coordinate_cursor_bgra(width=64, height=64):
    pixels = bytearray()
    for y in range(height):
        green = 16 + round(224 * y / (height - 1))
        for x in range(width):
            red = 16 + round(224 * x / (width - 1))
            pixels.extend((32, green, red, 255))
    return bytes(pixels)


def _premultiplied_cursor_bgra(width=64, height=64):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            alpha = 48 + ((x * 7 + y * 11) % 208)
            red = round(alpha * (0.20 + 0.55 * x / (width - 1)))
            green = round(alpha * (0.15 + 0.45 * y / (height - 1)))
            blue = round(alpha * 0.12)
            pixels.extend((blue, green, red, alpha))
    return bytes(pixels)


def _rgba_at(frame, x, y):
    offset = (y * frame.width + x) * 4
    return tuple(frame.pixels[offset:offset + 4])


def _decode_coordinate(red_or_green, extent=64):
    return (red_or_green - 16) * (extent - 1) / 224


def test_cursor_shader_links_with_all_uniforms_and_is_deterministic():
    assert CURSOR_FRAGMENT_SHADER_SOURCE.startswith("#version 330 core")
    options = CursorRenderOptions(cursor_bgra=_coordinate_cursor_bgra())
    first, locations = render_cursor_shader(options)
    second, second_locations = render_cursor_shader(options)
    assert locations.keys() == set(CURSOR_UNIFORM_NAMES)
    assert all(location >= 0 for location in locations.values())
    assert second_locations.keys() == locations.keys()
    assert first.pixels == second.pixels


def test_cursor_flow_advects_on_a_fixed_inward_curve_without_wobble():
    lowered = CURSOR_FRAGMENT_SHADER_SOURCE.lower()
    for forbidden in ("velocity", "random", "noise", "hash"):
        assert forbidden not in lowered
    assert "extensionProfile" in CURSOR_FRAGMENT_SHADER_SOURCE
    assert "u_cursor_body_axis" in CURSOR_FRAGMENT_SHADER_SOURCE
    assert "clamp(u_max_tangent_scale,1.0,2.2)" in (
        CURSOR_FRAGMENT_SHADER_SOURCE
    )
    assert "return texture(u_cursor_texture,local/u_cursor_size_px);" in (
        CURSOR_FRAGMENT_SHADER_SOURCE
    )
    assert "fract(u_cursor_time*0.28+float(i)/3.0)" in CURSOR_FRAGMENT_SHADER_SOURCE
    centerline = CURSOR_FRAGMENT_SHADER_SOURCE.split("vec2 inwardPoint", 1)[1].split("vec4 inwardFlow", 1)[0]
    assert "u_cursor_time" not in centerline
    assert "(1.0-contraction*t)" in centerline
    assert "vec4 ink=cursorSample(u_pointer_px+source);" in CURSOR_FRAGMENT_SHADER_SOURCE
    assert "smoothstep(0.0,0.16,age)" in CURSOR_FRAGMENT_SHADER_SOURCE
    assert "smoothstep(0.48,1.0,age)" in CURSOR_FRAGMENT_SHADER_SOURCE


def _native_body_only(options):
    # Isolate the native coordinate map; independently test emitted flow below.
    return render_cursor_shader(options, fragment_source_override=
        CURSOR_FRAGMENT_SHADER_SOURCE.replace("vec4 flow=inwardFlow(p);", "vec4 flow=vec4(0);"))


def test_cursor_shader_preserves_premultiplied_bgra_and_top_down_rows():
    frame, _ = render_cursor_shader(
        CursorRenderOptions(
            cursor_bgra=_premultiplied_cursor_bgra(),
            max_displacement_px=0.0,
            max_tangent_scale=1.0,
        )
    )
    for red, green, blue, alpha in zip(
        frame.pixels[0::4],
        frame.pixels[1::4],
        frame.pixels[2::4],
        frame.pixels[3::4],
    ):
        assert red <= alpha + 1
        assert green <= alpha + 1
        assert blue <= alpha + 1

    coordinate_frame, _ = render_cursor_shader(
        CursorRenderOptions(
            cursor_bgra=_coordinate_cursor_bgra(),
            max_displacement_px=0.0,
            max_tangent_scale=1.0,
        )
    )
    above_green = _rgba_at(coordinate_frame, 104, 70)[1]
    below_green = _rgba_at(coordinate_frame, 104, 50)[1]
    assert above_green < below_green


def test_cursor_distortion_anchors_hotspot_and_bounds_scale_and_displacement():
    options = CursorRenderOptions(
        cursor_bgra=_coordinate_cursor_bgra(),
        render_size=(224, 160), pointer_px=(104.0, 110.0),
        cursor_top_left_px=(72.0, 142.0), lens_center_px=(104.0, 110.0),
        max_displacement_px=64.0, max_tangent_scale=4.8,
    )
    distorted, _ = _native_body_only(options)
    baseline, _ = render_cursor_shader(
        replace(options, max_displacement_px=0.0, max_tangent_scale=1.0)
    )
    pointer_x, pointer_y = options.pointer_px
    anchored = 0
    for y in range(distorted.height):
        for x in range(distorted.width):
            distance = ((x + 0.5 - pointer_x) ** 2 + (y + 0.5 - pointer_y) ** 2) ** 0.5
            if distance <= 2.0:
                anchored += 1
                assert _rgba_at(distorted, x, y) == _rgba_at(baseline, x, y)
    assert anchored >= 8

    # Independently invert the gentle native-body extension; flow is separate.
    profile = lambda u: u - 5.0 * (1.0 - math.exp(-u / 5.0))
    strain = 1.2
    assert strain * profile(options.body_extent - 2.0) <= 64.0
    for y in (97, 89, 81, 73):
        destination_long = pointer_y - (y + 0.5)
        low, high = 2.0, destination_long
        for _ in range(40):
            source = (low + high) * 0.5
            if source + strain * profile(source - 2.0) < destination_long:
                low = source
            else:
                high = source
        pixel = _rgba_at(distorted, 104, y)
        assert pixel[3] >= 160
        decoded_source = _decode_coordinate(pixel[1]*255/pixel[3]) + 0.5 - 32.0
        assert abs(decoded_source - (low + high) * 0.5) < 0.6

    oversized, _ = _native_body_only(replace(options, max_displacement_px=1000.0))
    assert oversized.pixels == distorted.pixels  # Extra length cap is real.
    short_body = replace(options, body_extent=12.0)
    capped, _ = _native_body_only(short_body)
    excessive, _ = _native_body_only(replace(short_body, max_tangent_scale=100.0))
    assert capped.pixels == excessive.pixels  # Native magnification is capped at 2.2.


def test_cursor_flow_animates_without_moving_the_native_tip():
    options = CursorRenderOptions(cursor_bgra=_coordinate_cursor_bgra(),
                                  max_displacement_px=46.0, max_tangent_scale=4.8,
                                  lens_half_width_px=20.0)
    first, _ = render_cursor_shader(options)
    later, _ = render_cursor_shader(replace(options, cursor_time=0.35))
    assert first.pixels != later.pixels
    assert _rgba_at(first, 104, 60) == _rgba_at(later, 104, 60)
    flow_source = CURSOR_FRAGMENT_SHADER_SOURCE.replace(
        "fragColor=arrow+flow*(1.0-arrow.a);", "fragColor=flow;")
    flow_first, _ = render_cursor_shader(options, fragment_source_override=flow_source)
    flow_later, _ = render_cursor_shader(replace(options, cursor_time=0.35), fragment_source_override=flow_source)
    assert sum(abs(a-b)>5 for a,b in zip(flow_first.pixels, flow_later.pixels)) > 30
    # The 64-logical-pixel reach cap also applies to the emitted spiral.
    capped, _ = render_cursor_shader(replace(options, max_displacement_px=64))
    excessive, _ = render_cursor_shader(replace(options, max_displacement_px=1000))
    assert capped.pixels == excessive.pixels


def test_cursor_outside_the_optical_band_matches_native_sampling():
    options = CursorRenderOptions(cursor_bgra=_coordinate_cursor_bgra(),
                                  pointer_px=(120.0, 60.0), cursor_top_left_px=(88.0, 92.0),
                                  lens_center_px=(24.0, 60.0), lens_radius_px=24.0,
                                  lens_half_width_px=12.0, max_tangent_scale=1.65)
    curved, _ = render_cursor_shader(options)
    flat, _ = render_cursor_shader(replace(options, max_displacement_px=0.0, max_tangent_scale=1.0))
    assert max(abs(a - b) for a, b in zip(curved.pixels, flat.pixels)) <= 1


def test_high_dpi_hotspot_protection_scales_in_physical_pixels():
    options = CursorRenderOptions(cursor_bgra=_coordinate_cursor_bgra(), pixel_ratio=2.0,
                                  max_displacement_px=16.0, max_tangent_scale=1.65)
    curved, _ = render_cursor_shader(options)
    flat, _ = render_cursor_shader(replace(options, max_displacement_px=0.0, max_tangent_scale=1.0))
    for y in range(56, 64):
        for x in range(100, 108):
            if (x + 0.5 - 104) ** 2 + (y + 0.5 - 60) ** 2 <= 16:
                assert _rgba_at(curved, x, y) == _rgba_at(flat, x, y)


def test_cursor_inner_ramp_does_not_fold_the_pointer_body():
    # Decode an actual GL-rendered coordinate field. A local orientation flip
    # would fold/tear the native arrow body during an inward crossing.
    for ratio in (0.0, 0.20, 0.76, 1.0, 1.25):
        angle = math.pi * 0.12
        pointer = (64 + 40 * ratio * math.cos(angle), 60 + 40 * ratio * math.sin(angle))
        frame, _ = _native_body_only(CursorRenderOptions(
            cursor_bgra=_coordinate_cursor_bgra(), pointer_px=pointer,
            cursor_top_left_px=(pointer[0] - 10, pointer[1] + 10),
            lens_half_width_px=20.0, max_displacement_px=46.0,
            max_tangent_scale=4.8))
        checked = 0
        for y in range(round(pointer[1]) - 22, round(pointer[1]) - 2):
            for x in range(round(pointer[0]), round(pointer[0]) + 13):
                p, px, py = (_rgba_at(frame, x, y), _rgba_at(frame, x + 2, y),
                             _rgba_at(frame, x, y + 2))
                if min(p[3], px[3], py[3]) < 160:
                    continue
                p, px, py = (tuple(c*255/pixel[3] for c in pixel[:3]) for pixel in (p,px,py))
                determinant = ((px[0] - p[0]) * (py[1] - p[1])
                               - (py[0] - p[0]) * (px[1] - p[1]))
                # Source Y is top-down, so an unfolded map has negative sign.
                assert determinant <= 8  # Small allowance for RGBA8 quantization.
                checked += 1
        assert checked > 150


def test_cursor_center_crossing_has_no_direction_flip():
    options = CursorRenderOptions(cursor_bgra=_coordinate_cursor_bgra(),
                                  pointer_px=(80.0, 60.0), cursor_top_left_px=(48.0, 92.0),
                                  lens_center_px=(80.0, 60.0), max_displacement_px=46.0,
                                  max_tangent_scale=4.8)
    # Move the lens rather than the pointer to compare the same source texels.
    left, _ = _native_body_only(replace(options, lens_center_px=(79.99, 60.0)))
    right, _ = _native_body_only(replace(options, lens_center_px=(80.01, 60.0)))
    # Only decode fully opaque red/green coordinates, not moving alpha edges.
    opaque_deltas = [abs(a - b) for index, (a, b) in enumerate(zip(left.pixels, right.pixels))
                     if index % 4 < 2 and left.pixels[index // 4 * 4 + 3] == 255
                     and right.pixels[index // 4 * 4 + 3] == 255]
    assert max(opaque_deltas) <= 2


def test_cursor_inside_event_horizon_remains_visible_with_fixed_hotspot():
    options = CursorRenderOptions(
        cursor_bgra=_coordinate_cursor_bgra(),
        cursor_top_left_px=(32.0, 92.0),
        pointer_px=(64.0, 60.0),
        lens_center_px=(64.0, 60.0),
    )
    distorted, _ = render_cursor_shader(options)
    baseline, _ = render_cursor_shader(
        replace(options, max_displacement_px=0.0, max_tangent_scale=1.0)
    )
    hotspot = _rgba_at(distorted, 64, 60)
    assert hotspot[3] >= 250
    for y in range(58, 62):
        for x in range(62, 66):
            if ((x + 0.5 - 64.0) ** 2 + (y + 0.5 - 60.0) ** 2) ** 0.5 <= 2.0:
                assert _rgba_at(distorted, x, y) == _rgba_at(baseline, x, y)


def test_warm_support_discards_small_noise_and_keeps_central_component():
    width, height = 12, 10
    pixels = bytearray(width * height * 4)
    expected = {(x, y) for x in range(3, 7) for y in range(3, 6)}
    for x, y in {(0, 0), (1, 0), (10, 9)} | expected:
        offset = (y * width + x) * 4
        pixels[offset:offset + 4] = bytes((230, 150, 20, 255))
    assert warm_support_mask(RenderedFrame(width, height, bytes(pixels))) == expected


def test_visual_metric_helpers_measure_span_roll_core_and_continuous_arc():
    width, height = 100, 60
    pixels = bytearray(width * height * 4)
    for x in range(8, 93):
        axis = 24 + round(0.08 * (x - 8))
        for y in range(axis - 2, axis + 3):
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = bytes((220, 130, 25, 255))
    for x in range(28, 73):
        axis = 24 + round(0.08 * (x - 8))
        y = axis + 8 + round(5 * (1.0 - abs(x - 50) / 23))
        for row in range(y, y + 3):
            offset = (row * width + x) * 4
            pixels[offset:offset + 4] = bytes((240, 170, 45, 255))
    for x in range(41, 60):
        offset = (height // 2 * width + x) * 4
        pixels[offset:offset + 4] = bytes((0, 0, 0, 255))
    metrics = analyze_scene_visuals(RenderedFrame(width, height, bytes(pixels)))
    assert metrics.disk_width == 85
    assert metrics.band_thickness >= 5
    assert metrics.far_arc_span >= 40
    assert metrics.far_arc_column_coverage >= 0.95
    assert metrics.black_core_width == 19
    assert metrics.roll_rise >= 3
