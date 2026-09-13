from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median


@dataclass(frozen=True, slots=True)
class SceneRenderOptions:
    companion: tuple[float,float,float,float] = (0.0,0.0,0.0,0.0)
    companion_light: float = 1.0
    companion_tail: tuple[float,float,float,float] = (0.0,0.0,0.0,0.0)
    mouse: tuple[float, float] = (0.0, 0.0)
    mouse_inside: float = 0.0
    drag_strength: float = 0.0
    quality: float = 1.0
    debug_view: float = 0.0
    instrument_edge_fade: bool = False
    isolate_photon_ring: bool = False
    disable_photon_ring: bool = False
    disable_lensed_glow: bool = False
    disable_drag_glow_boost: bool = False
    disable_backdrop: bool = False
    isolate_lens_shell: bool = False
    isolate_support: bool = False
    isolate_lens_shell_support: bool = False
    disable_lens_shell: bool = False
    disable_dark_shell: bool = False
    disable_warm_shell: bool = False
    coarse_trace: bool = False
    max_steps_override: int | None = None
    render_size: tuple[int, int] = (120, 80)
    render_time: float = 0.7


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    width: int
    height: int
    pixels: bytes


CURSOR_UNIFORM_NAMES = (
    "u_resolution",
    "u_cursor_texture",
    "u_cursor_top_left_px",
    "u_cursor_size_px",
    "u_pointer_px",
    "u_lens_center_px",
    "u_lens_radius_px",
    "u_lens_half_width_px",
    "u_max_displacement_px",
    "u_max_tangent_scale",
    "u_pixel_ratio",
    "u_cursor_body_axis",
    "u_cursor_body_extent",
    "u_cursor_time",
)


@dataclass(frozen=True, slots=True)
class CursorRenderOptions:
    cursor_bgra: bytes
    render_size: tuple[int, int] = (160, 120)
    cursor_size: tuple[int, int] = (64, 64)
    cursor_top_left_px: tuple[float, float] = (72.0, 92.0)
    pointer_px: tuple[float, float] = (104.0, 60.0)
    lens_center_px: tuple[float, float] = (64.0, 60.0)
    lens_radius_px: float = 40.0
    lens_half_width_px: float = 12.0
    max_displacement_px: float = 8.0
    max_tangent_scale: float = 1.45
    pixel_ratio: float = 1.0
    body_axis: tuple[float, float] = (0.0, -1.0)
    body_extent: float = 32.0
    cursor_time: float = 0.0


def render_cursor_shader(
    options: CursorRenderOptions,
    *,
    fragment_source_override: str | None = None,
) -> tuple[RenderedFrame, dict[str, int]]:
    cursor_width, cursor_height = options.cursor_size
    expected_size = cursor_width * cursor_height * 4
    if len(options.cursor_bgra) != expected_size:
        raise ValueError(
            f"cursor_bgra has {len(options.cursor_bgra)} bytes; expected {expected_size}"
        )
    probe = rf'''
import base64
import json
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader
from black_hole_shaders import CURSOR_FRAGMENT_SHADER_SOURCE, FULLSCREEN_VERTEX_SHADER_SOURCE
CURSOR_FRAGMENT_SHADER_SOURCE = {fragment_source_override!r} or CURSOR_FRAGMENT_SHADER_SOURCE

width, height = {options.render_size!r}
cursor_width, cursor_height = {options.cursor_size!r}
cursor_bgra = base64.b64decode({base64.b64encode(options.cursor_bgra).decode("ascii")!r})
app = QGuiApplication([])
surface_format = QSurfaceFormat()
surface_format.setRenderableType(QSurfaceFormat.OpenGL)
surface_format.setVersion(3, 3)
surface_format.setProfile(QSurfaceFormat.CoreProfile)
surface_format.setAlphaBufferSize(8)
surface = QOffscreenSurface()
surface.setFormat(surface_format)
surface.create()
context = QOpenGLContext()
context.setFormat(surface_format)
assert context.create(), "OpenGL 3.3 context creation failed"
assert context.makeCurrent(surface), "OpenGL context could not be made current"
program = compileProgram(
    compileShader(FULLSCREEN_VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
    compileShader(CURSOR_FRAGMENT_SHADER_SOURCE, GL.GL_FRAGMENT_SHADER),
)
locations = {{
    name: int(GL.glGetUniformLocation(program, name))
    for name in {CURSOR_UNIFORM_NAMES!r}
}}

target_texture = GL.glGenTextures(1)
GL.glBindTexture(GL.GL_TEXTURE_2D, target_texture)
GL.glTexImage2D(
    GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, width, height, 0,
    GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None,
)
framebuffer = GL.glGenFramebuffers(1)
GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
GL.glFramebufferTexture2D(
    GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
    GL.GL_TEXTURE_2D, target_texture, 0,
)
assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE

cursor_texture = GL.glGenTextures(1)
GL.glActiveTexture(GL.GL_TEXTURE0)
GL.glBindTexture(GL.GL_TEXTURE_2D, cursor_texture)
GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_BORDER)
GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_BORDER)
GL.glTexParameterfv(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_BORDER_COLOR, [0.0, 0.0, 0.0, 0.0])
GL.glTexImage2D(
    GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, cursor_width, cursor_height, 0,
    GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, cursor_bgra,
)

vertex_array = GL.glGenVertexArrays(1)
GL.glBindVertexArray(vertex_array)
GL.glViewport(0, 0, width, height)
GL.glUseProgram(program)
GL.glUniform2f(locations["u_resolution"], float(width), float(height))
GL.glUniform1i(locations["u_cursor_texture"], 0)
GL.glUniform2f(locations["u_cursor_top_left_px"], *{options.cursor_top_left_px!r})
GL.glUniform2f(locations["u_cursor_size_px"], float(cursor_width), float(cursor_height))
GL.glUniform2f(locations["u_pointer_px"], *{options.pointer_px!r})
GL.glUniform2f(locations["u_lens_center_px"], *{options.lens_center_px!r})
GL.glUniform1f(locations["u_lens_radius_px"], {options.lens_radius_px!r})
GL.glUniform1f(locations["u_lens_half_width_px"], {options.lens_half_width_px!r})
GL.glUniform1f(locations["u_max_displacement_px"], {options.max_displacement_px!r})
GL.glUniform1f(locations["u_max_tangent_scale"], {options.max_tangent_scale!r})
GL.glUniform1f(locations["u_pixel_ratio"], {options.pixel_ratio!r})
GL.glUniform2f(locations["u_cursor_body_axis"], *{options.body_axis!r})
GL.glUniform1f(locations["u_cursor_body_extent"], {options.body_extent!r})
GL.glUniform1f(locations["u_cursor_time"], {options.cursor_time!r})
GL.glDisable(GL.GL_BLEND)
GL.glClearColor(0.0, 0.0, 0.0, 0.0)
GL.glClear(GL.GL_COLOR_BUFFER_BIT)
GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
GL.glFinish()
pixels = bytes(GL.glReadPixels(
    0, 0, width, height, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
))
print(json.dumps({{
    "pixels": base64.b64encode(pixels).decode("ascii"),
    "locations": locations,
}}))
GL.glDeleteVertexArrays(1, [vertex_array])
GL.glDeleteTextures(1, [cursor_texture])
GL.glDeleteFramebuffers(1, [framebuffer])
GL.glDeleteTextures(1, [target_texture])
GL.glDeleteProgram(program)
context.doneCurrent()
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-"],
        input=probe,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    width, height = options.render_size
    return (
        RenderedFrame(width, height, base64.b64decode(payload["pixels"])),
        {name: int(location) for name, location in payload["locations"].items()},
    )


@dataclass(frozen=True, slots=True)
class SceneVisualMetrics:
    disk_width: int
    band_thickness: float
    far_arc_span: int
    far_arc_column_coverage: float
    black_core_width: int
    roll_rise: float
    hot_inner_fraction: float
    hot_inner_p95: float
    middle_disk_p95: float


def unpremultiplied_pixels(
    frame: RenderedFrame,
) -> list[tuple[float, float, float, float]]:
    result = []
    for offset in range(0, len(frame.pixels), 4):
        red, green, blue, alpha_byte = frame.pixels[offset:offset + 4]
        alpha = alpha_byte / 255.0
        if alpha >= 0.05:
            divisor = max(alpha_byte, 1)
            result.append(
                (red / divisor, green / divisor, blue / divisor, alpha)
            )
        else:
            result.append((0.0, 0.0, 0.0, alpha))
    return result


def _components(
    points: set[tuple[int, int]],
) -> list[set[tuple[int, int]]]:
    remaining = set(points)
    components = []
    while remaining:
        component = {remaining.pop()}
        pending = list(component)
        while pending:
            x, y = pending.pop()
            for neighbor_y in range(y - 1, y + 2):
                for neighbor_x in range(x - 1, x + 2):
                    neighbor = (neighbor_x, neighbor_y)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
        components.append(component)
    return components


def warm_support_mask(frame: RenderedFrame) -> set[tuple[int, int]]:
    pixels = unpremultiplied_pixels(frame)
    candidates = set()
    for index, (red, green, blue, alpha) in enumerate(pixels):
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if alpha >= 0.05 and luminance >= 0.12 and red >= 1.10 * blue:
            candidates.add((index % frame.width, index // frame.width))
    central_y0 = int(frame.height * 0.20)
    central_y1 = int(frame.height * 0.80)
    accepted = [
        component
        for component in _components(candidates)
        if len(component) >= 12
        and any(central_y0 <= y < central_y1 for _, y in component)
    ]
    return set().union(*accepted) if accepted else set()


def _percentile(values: list[float], fraction: float) -> float:
    assert values
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def analyze_scene_visuals(frame: RenderedFrame) -> SceneVisualMetrics:
    warm = warm_support_mask(frame)
    assert warm, "no connected warm disk support"
    pixels = unpremultiplied_pixels(frame)
    disk_left = min(x for x, _ in warm)
    disk_right = max(x for x, _ in warm)
    disk_width = disk_right - disk_left + 1

    fit_left = disk_left + round(0.15 * disk_width)
    fit_right = disk_left + round(0.85 * disk_width)
    columns = {}
    for x, y in warm:
        if fit_left <= x <= fit_right:
            columns.setdefault(x, []).append(y)

    def row_runs(rows):
        ordered_rows = sorted(rows)
        runs = []
        run = [ordered_rows[0]]
        for y in ordered_rows[1:]:
            if y <= run[-1] + 1:
                run.append(y)
            else:
                runs.append(run)
                run = [y]
        runs.append(run)
        return runs

    disk_center_x = (disk_left + disk_right) * 0.5
    shoulder_columns = {
        x: rows
        for x, rows in columns.items()
        if 0.26 * disk_width
        <= abs(x - disk_center_x)
        <= 0.43 * disk_width
    }
    if len(shoulder_columns) < 2:
        shoulder_columns = columns
    shoulder_runs = {}
    for x, rows in shoulder_columns.items():
        def luminance_at(y):
            red, green, blue, _ = pixels[y * frame.width + x]
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue

        peak_y = max(rows, key=luminance_at)
        peak_luminance = luminance_at(peak_y)
        spine_rows = [
            y for y in rows
            if luminance_at(y) >= max(0.42, peak_luminance * 0.80)
        ]
        runs = row_runs(spine_rows)
        shoulder_runs[x] = next(run for run in runs if peak_y in run)
    column_medians = [
        (x, median(run)) for x, run in shoulder_runs.items()
    ]
    mean_x = sum(x for x, _ in column_medians) / len(column_medians)
    mean_y = sum(y for _, y in column_medians) / len(column_medians)
    denominator = sum((x - mean_x) ** 2 for x, _ in column_medians)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in column_medians)
        / denominator
    )
    intercept = mean_y - slope * mean_x

    thicknesses = [float(len(run)) for run in shoulder_runs.values()]
    band_thickness = float(median(thicknesses))

    arc_offset = 0.035 * disk_width
    arc_components = _components({
        (x, y)
        for x, y in warm
        if y >= slope * x + intercept + arc_offset
    })
    largest_arc = max(arc_components, key=len, default=set())
    if largest_arc:
        arc_left = min(x for x, _ in largest_arc)
        arc_right = max(x for x, _ in largest_arc)
        far_arc_span = arc_right - arc_left + 1
        arc_coverage = len({x for x, _ in largest_arc}) / far_arc_span
    else:
        far_arc_span = 0
        arc_coverage = 0.0

    center_y = frame.height // 2
    dark_x = set()
    for x in range(frame.width):
        red, green, blue, alpha = pixels[center_y * frame.width + x]
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if alpha >= 0.75 and luminance <= 0.015:
            dark_x.add(x)
    center_x = frame.width // 2
    core_left = core_right = center_x
    if center_x in dark_x:
        while core_left - 1 in dark_x:
            core_left -= 1
        while core_right + 1 in dark_x:
            core_right += 1
        black_core_width = core_right - core_left + 1
    else:
        black_core_width = 0

    roll_rise = abs(float(slope * disk_width))

    half_span = max(1.0, disk_width * 0.5)
    hot_luminance = []
    middle_luminance = []
    radial_columns = {}
    for x, y in warm:
        radius_fraction = abs(x - frame.width * 0.5) / half_span
        red, green, blue, _ = pixels[y * frame.width + x]
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        main_band_half_width = max(
            band_thickness * 0.55,
            0.025 * disk_width,
        )
        on_main_band = abs(y - (slope * x + intercept)) <= main_band_half_width
        if 0.15 <= radius_fraction <= 0.36 and on_main_band:
            hot_luminance.append(luminance)
            radial_columns.setdefault(x, []).append(luminance)
        elif 0.38 <= radius_fraction <= 0.62 and on_main_band:
            middle_luminance.append(luminance)
    hot_threshold = max(0.72, _percentile(hot_luminance, 0.95) * 0.98)
    hot_columns = sorted(
        x
        for x, values in radial_columns.items()
        if _percentile(values, 0.95) >= hot_threshold
    )
    hot_runs = []
    if hot_columns:
        run = [hot_columns[0]]
        for x in hot_columns[1:]:
            if x == run[-1] + 1:
                run.append(x)
            else:
                hot_runs.append(run)
                run = [x]
        hot_runs.append(run)
    hot_fraction = max((len(run) / half_span for run in hot_runs), default=0.0)
    return SceneVisualMetrics(
        disk_width=disk_width,
        band_thickness=band_thickness,
        far_arc_span=far_arc_span,
        far_arc_column_coverage=arc_coverage,
        black_core_width=black_core_width,
        roll_rise=roll_rise,
        hot_inner_fraction=hot_fraction,
        hot_inner_p95=_percentile(hot_luminance, 0.95),
        middle_disk_p95=_percentile(middle_luminance, 0.95),
    )


def render_scene(
    options: SceneRenderOptions,
    *,
    production_resolve: bool = False,
    fragment_source_override: str | None = None,
    desktop_color: tuple[int, int, int] | None = None,
    desktop_gradient: bool = False,
) -> RenderedFrame:
    probe = rf'''
import base64
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader
from black_hole_shaders import (
    FULLSCREEN_VERTEX_SHADER_SOURCE,
    SCENE_FRAGMENT_SHADER_SOURCE,
)

fragment_source = {fragment_source_override!r} or SCENE_FRAGMENT_SHADER_SOURCE
if {production_resolve!r}:
    fragment_source = fragment_source.replace(
        "#version 330 core", "#version 330 core\n#define OUTPUT_HDR 1", 1
    )

def replace_required(source, old, new, label):
    replaced = source.replace(old, new)
    assert replaced != source, label + " shader instrumentation target was not found"
    return replaced

max_steps_override = {options.max_steps_override!r}
if max_steps_override is not None:
    production_step_line = "const int MAX_STEPS = 128;"
    requested_step_line = f"const int MAX_STEPS = {{max_steps_override}};"
    if requested_step_line != production_step_line:
        fragment_source = replace_required(
            fragment_source,
            production_step_line,
            requested_step_line,
            "max steps",
        )
if {options.instrument_edge_fade!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(mapped * fadedSceneAlpha, alpha);",
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(vec3(edgeFade), edgeFade);",
        "edge fade",
    )
if {options.isolate_photon_ring!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_DISK_OUTPUT\n    return vec4(emission, opacity);",
        "// TEST_PROBE_DISK_OUTPUT\n    return vec4(0.0);",
        "disk isolation",
    )
if {options.disable_photon_ring!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_PHOTON_RING\n    "
        "vec3 ringColor = vec3(1.80, 1.35, 0.72) * photonRing * 1.05;",
        "// TEST_PROBE_PHOTON_RING\n    "
        "photonRing = 0.0;\n    vec3 ringColor = vec3(0.0);",
        "photon ring disable",
    )
if {options.disable_lensed_glow!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_LENSED_ARC\n    "
        "lensedArc *= 1.0 - horizonCoverage;",
        "// TEST_PROBE_LENSED_ARC\n    lensedArc = 0.0;",
        "lensed glow disable",
    )
if {options.disable_drag_glow_boost!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_DRAG_GLOW\n    "
        "float dragGlow = clamp(u_drag_strength, 0.0, 1.0);",
        "// TEST_PROBE_DRAG_GLOW\n    float dragGlow = 0.0;",
        "drag glow boost disable",
    )
if {options.disable_backdrop!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_BACKDROP\n    backdropAlpha *= edgeFade;",
        "// TEST_PROBE_BACKDROP\n    backdropAlpha = 0.0;",
        "backdrop disable",
    )
if {options.disable_lens_shell!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_SHELL_COMPOSITE\n    "
        "float shellAlpha = darkShellAlpha + "
        "warmShellAlpha * (1.0 - darkShellAlpha);",
        "// TEST_PROBE_SHELL_COMPOSITE\n    "
        "darkShellAlpha = 0.0;\n    warmShellAlpha = 0.0;\n    "
        "float shellAlpha = 0.0;",
        "lens shell disable",
    )
if {options.disable_dark_shell!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_SHELL_DARK\n    "
        "float darkShellAlpha = min(0.08, darkShellBase * shellVisibility);",
        "// TEST_PROBE_SHELL_DARK\n    float darkShellAlpha = 0.0;",
        "dark shell disable",
    )
if {options.disable_warm_shell!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_SHELL_WARM\n    "
        "float warmShellAlpha = min(0.045, warmShellBase * shellVisibility);",
        "// TEST_PROBE_SHELL_WARM\n    float warmShellAlpha = 0.0;",
        "warm shell disable",
    )
if {options.isolate_lens_shell!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(mapped * fadedSceneAlpha, alpha);",
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(shellPremultiplied * edgeFade, "
        "shellAlpha * edgeFade);",
        "lens shell isolation",
    )
if {options.isolate_support!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(mapped * fadedSceneAlpha, alpha);",
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(0.0, 0.0, 0.0, backdropAlpha);",
        "support isolation",
    )
if {options.isolate_lens_shell_support!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(mapped * fadedSceneAlpha, alpha);",
        "// TEST_PROBE_FINAL_OUTPUT\n    "
        "fragColor = vec4(vec3(shellSupport), shellSupport);",
        "lens shell support isolation",
    )
if {options.coarse_trace!r}:
    fragment_source = replace_required(
        fragment_source,
        "// TEST_PROBE_STEP_SIZE\n        "
        "float stepSize = clamp(radius * 0.064, 0.017, 0.44);",
        "// TEST_PROBE_STEP_SIZE\n        "
        "float stepSize = clamp(radius * 0.075, 0.020, 0.48);",
        "coarse trace",
    )

output_width, output_height = {options.render_size!r}
production_resolve = {production_resolve!r}
render_scale = 2 if production_resolve else 1
width = output_width * render_scale
height = output_height * render_scale
mouse_x = {options.mouse[0]!r} * render_scale
mouse_y = {options.mouse[1]!r} * render_scale
app = QGuiApplication([])
surface_format = QSurfaceFormat()
surface_format.setRenderableType(QSurfaceFormat.OpenGL)
surface_format.setVersion(3, 3)
surface_format.setProfile(QSurfaceFormat.CoreProfile)
surface_format.setAlphaBufferSize(8)
surface = QOffscreenSurface()
surface.setFormat(surface_format)
surface.create()
context = QOpenGLContext()
context.setFormat(surface_format)
assert context.create(), "OpenGL 3.3 context creation failed"
assert context.makeCurrent(surface), "OpenGL context could not be made current"
program = compileProgram(
    compileShader(FULLSCREEN_VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
    compileShader(fragment_source, GL.GL_FRAGMENT_SHADER),
)
texture = GL.glGenTextures(1)
GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F if production_resolve else GL.GL_RGBA8, width, height, 0,
                GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
framebuffer = GL.glGenFramebuffers(1)
GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                          GL.GL_TEXTURE_2D, texture, 0)
assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE
vertex_array = GL.glGenVertexArrays(1)
GL.glBindVertexArray(vertex_array)
GL.glViewport(0, 0, width, height)
GL.glUseProgram(program)
GL.glUniform2f(GL.glGetUniformLocation(program, "u_resolution"), width, height)
GL.glUniform1f(GL.glGetUniformLocation(program, "u_time"), {options.render_time!r})
GL.glUniform1f(GL.glGetUniformLocation(program, "u_quality"), {options.quality!r})
GL.glUniform4f(GL.glGetUniformLocation(program, "u_companion"), *{options.companion!r})
GL.glUniform1f(GL.glGetUniformLocation(program, "u_companion_light"), {options.companion_light!r})
GL.glUniform4f(GL.glGetUniformLocation(program, "u_companion_tail"), *{options.companion_tail!r})
GL.glUniform1f(
    GL.glGetUniformLocation(program, "u_debug_view"),
    {options.debug_view!r},
)
GL.glUniform2f(GL.glGetUniformLocation(program, "u_mouse"), mouse_x, mouse_y)
GL.glUniform1f(
    GL.glGetUniformLocation(program, "u_mouse_inside"),
    {options.mouse_inside!r},
)
GL.glUniform1f(
    GL.glGetUniformLocation(program, "u_drag_strength"),
    {options.drag_strength!r},
)
GL.glClearColor(0.0, 0.0, 0.0, 0.0)
GL.glClear(GL.GL_COLOR_BUFFER_BIT)
GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
GL.glFinish()

resolve_texture = 0
resolve_framebuffer = 0
if production_resolve:
    resolve_texture = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, resolve_texture)
    GL.glTexImage2D(
        GL.GL_TEXTURE_2D,
        0,
        GL.GL_RGBA8,
        output_width,
        output_height,
        0,
        GL.GL_RGBA,
        GL.GL_UNSIGNED_BYTE,
        None,
    )
    resolve_framebuffer = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, resolve_framebuffer)
    GL.glFramebufferTexture2D(
        GL.GL_FRAMEBUFFER,
        GL.GL_COLOR_ATTACHMENT0,
        GL.GL_TEXTURE_2D,
        resolve_texture,
        0,
    )
    assert (
        GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        == GL.GL_FRAMEBUFFER_COMPLETE
    )
    from black_hole_postprocess import HDRPostprocess
    postprocess = HDRPostprocess()
    if {desktop_color is not None or desktop_gradient!r}:
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage, QColor
        from desktop_capture import DesktopFrame
        desktop = QImage(output_width, output_height, QImage.Format_RGBA8888)
        desktop.fill(QColor(*{desktop_color or (80, 140, 200)!r}))
        if {desktop_gradient!r}:
            for y in range(output_height):
                for x in range(output_width):
                    desktop.setPixelColor(x, y, QColor(
                        round(255*x/(output_width-1)), round(255*y/(output_height-1)), 200))
        postprocess.desktop_frame = DesktopFrame(
            desktop, QRect(0, 0, output_width, output_height), 1, 1.0)
        postprocess.desktop_geometry = (0, 0, output_width, output_height)
        postprocess.desktop_quality = {options.quality!r}
    postprocess.render(framebuffer, (width, height), resolve_framebuffer,
        (output_width, output_height), {options.render_time!r}, {options.debug_view!r})
    assert not postprocess.desktop_error, postprocess.desktop_error
    postprocess.close()
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, resolve_framebuffer)

pixels = bytes(
    GL.glReadPixels(
        0,
        0,
        output_width,
        output_height,
        GL.GL_RGBA,
        GL.GL_UNSIGNED_BYTE,
    )
)
print(base64.b64encode(pixels).decode("ascii"))
GL.glDeleteVertexArrays(1, [vertex_array])
if resolve_framebuffer:
    GL.glDeleteFramebuffers(1, [resolve_framebuffer])
if resolve_texture:
    GL.glDeleteTextures(1, [resolve_texture])
GL.glDeleteFramebuffers(1, [framebuffer])
GL.glDeleteTextures(1, [texture])
GL.glDeleteProgram(program)
context.doneCurrent()
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-"],
        input=probe,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    width, height = options.render_size
    return RenderedFrame(
        width=width,
        height=height,
        pixels=base64.b64decode(result.stdout.strip()),
    )
