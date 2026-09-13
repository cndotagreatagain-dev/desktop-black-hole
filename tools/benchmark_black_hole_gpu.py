from __future__ import annotations

import argparse
import ast
import ctypes
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from black_hole_shaders import (
    CURSOR_FRAGMENT_SHADER_SOURCE as HARNESS_CURSOR_FRAGMENT_SHADER_SOURCE,
)
from black_hole_postprocess import HDRPostprocess


SCENE_UNIFORM_NAMES = (
    "u_resolution",
    "u_time",
    "u_mouse",
    "u_mouse_inside",
    "u_drag_strength",
)

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
)


@dataclass(frozen=True, slots=True)
class ShaderSources:
    vertex: str
    scene_fragment: str
    cursor_fragment: str
    cursor_shader_source: str


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    project_root: Path
    frames: int
    warmup: int
    sizes: tuple[tuple[int, int], ...]


class QueryBackend(Protocol):
    def create(self) -> int: ...

    def begin(self, query: int) -> None: ...

    def end(self) -> None: ...

    def result_nanoseconds(self, query: int) -> int: ...

    def delete(self, query: int) -> None: ...


class Renderer(Protocol):
    def draw_scene(self) -> None: ...

    def draw_cursor(self) -> None: ...

    def draw_total(self) -> None: ...

    def draw_file_drag(self) -> None: ...

    def finish(self) -> None: ...

    def close(self) -> None: ...


def nearest_rank_percentile(
    samples: Sequence[float], percentile: float
) -> float:
    if not samples:
        raise ValueError("samples must not be empty")
    if not 0.0 <= percentile <= 100.0:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(float(value) for value in samples)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            f"invalid size {value!r}; expected WIDTHxHEIGHT"
        ) from error
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("width and height must be positive")
    return width, height


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _deduplicate_sizes(
    sizes: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    return tuple(dict.fromkeys(sizes))


def parse_cli(argv: Sequence[str] | None = None) -> BenchmarkOptions:
    parser = argparse.ArgumentParser(
        description="Measure the black-hole GPU frame budget with timer queries."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--frames", type=_positive_integer, default=120)
    parser.add_argument("--warmup", type=_positive_integer, default=30)
    parser.add_argument(
        "--size",
        action="append",
        type=parse_size,
        dest="sizes",
        metavar="WIDTHxHEIGHT",
    )
    namespace = parser.parse_args(argv)
    sizes = namespace.sizes or [(360, 240), (600, 400), (720, 480)]
    return BenchmarkOptions(
        project_root=namespace.project_root.resolve(),
        frames=namespace.frames,
        warmup=namespace.warmup,
        sizes=_deduplicate_sizes(sizes),
    )


def _literal_string_assignments(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, str):
            values[target.id] = value
    return values


def load_shader_sources(project_root: Path) -> ShaderSources:
    project_root = Path(project_root).resolve()
    shader_path = project_root / "black_hole_shaders.py"
    desktop_path = project_root / "desktop_black_hole.py"

    values: dict[str, str] = {}
    if shader_path.is_file():
        values.update(_literal_string_assignments(shader_path))
    geodesic_path = project_root / "gargantua_scene_shader.py"
    if geodesic_path.is_file():
        values.update(_literal_string_assignments(geodesic_path))
    if desktop_path.is_file() and not {
        "FULLSCREEN_VERTEX_SHADER_SOURCE",
        "SCENE_FRAGMENT_SHADER_SOURCE",
    }.issubset(values):
        values.update(_literal_string_assignments(desktop_path))

    vertex = values.get("FULLSCREEN_VERTEX_SHADER_SOURCE") or values.get(
        "VERTEX_SHADER_SOURCE"
    )
    scene = values.get("SCENE_FRAGMENT_SHADER_SOURCE") or values.get(
        "FRAGMENT_SHADER_SOURCE"
    )
    if not vertex or not scene:
        raise ValueError(
            f"could not find literal scene shader sources beneath {project_root}"
        )
    project_cursor = values.get("CURSOR_FRAGMENT_SHADER_SOURCE")
    return ShaderSources(
        vertex=vertex,
        scene_fragment=scene,
        cursor_fragment=(
            project_cursor or HARNESS_CURSOR_FRAGMENT_SHADER_SOURCE
        ),
        cursor_shader_source="project" if project_cursor else "harness-fallback",
    )


def _measure_draw(
    draw: Callable[[], None],
    renderer: Renderer,
    query_backend: QueryBackend,
    warmup: int,
    frames: int,
) -> list[float]:
    for _ in range(warmup):
        draw()
    renderer.finish()

    query = query_backend.create()
    samples: list[float] = []
    try:
        for _ in range(frames):
            active = False
            query_backend.begin(query)
            active = True
            try:
                draw()
            finally:
                if active:
                    query_backend.end()
            elapsed_ns = query_backend.result_nanoseconds(query)
            samples.append(float(elapsed_ns) / 1_000_000.0)
    finally:
        query_backend.delete(query)
    return samples


def measure_gpu_states(
    renderer: Renderer,
    query_backend: QueryBackend,
    *,
    warmup: int,
    frames: int,
) -> dict[str, list[float]]:
    draws = (
        ("scene", renderer.draw_scene),
        ("cursor", renderer.draw_cursor),
        ("total", renderer.draw_total),
        ("file_drag", renderer.draw_file_drag),
    )
    measured: dict[str, list[float]] = {}
    try:
        for name, draw in draws:
            measured[name] = _measure_draw(
                draw,
                renderer,
                query_backend,
                warmup,
                frames,
            )
        return measured
    finally:
        renderer.close()


def build_result(
    size: tuple[int, int],
    measured: dict[str, Sequence[float]],
    cursor_shader_source: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "size": f"{size[0]}x{size[1]}",
        "cursor_shader_source": cursor_shader_source,
    }
    for state in ("scene", "cursor", "total", "file_drag"):
        samples = measured[state]
        result[f"{state}_p50_ms"] = nearest_rank_percentile(samples, 50.0)
        result[f"{state}_p95_ms"] = nearest_rank_percentile(samples, 95.0)
    return result


class OpenGLQueryBackend:
    def __init__(self, gl) -> None:
        self._gl = gl

    def create(self) -> int:
        query = _coerce_gl_integer(self._gl.glGenQueries(1))
        if not query:
            raise RuntimeError("could not allocate an OpenGL timer query")
        return query

    def begin(self, query: int) -> None:
        self._gl.glBeginQuery(self._gl.GL_TIME_ELAPSED, query)

    def end(self) -> None:
        self._gl.glEndQuery(self._gl.GL_TIME_ELAPSED)

    def result_nanoseconds(self, query: int) -> int:
        from OpenGL.raw.GL.VERSION.GL_3_3 import glGetQueryObjectui64v

        result = ctypes.c_uint64()
        glGetQueryObjectui64v(
            query,
            self._gl.GL_QUERY_RESULT,
            ctypes.byref(result),
        )
        return int(result.value)

    def delete(self, query: int) -> None:
        self._gl.glDeleteQueries(1, [query])


def _coerce_gl_integer(value) -> int:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if not raw:
            return 0
        return int.from_bytes(raw, byteorder=sys.byteorder, signed=False)
    if hasattr(value, "value"):
        return int(value.value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(value[0])


class OpenGLBenchmarkRenderer:
    SUPERSAMPLE_SCALE = 2

    def __init__(self, size: tuple[int, int], sources: ShaderSources) -> None:
        if os.name == "nt":
            os.environ.setdefault("QT_QPA_PLATFORM", "windows")

        from OpenGL import GL
        from OpenGL.arrays.arraydatatype import ArrayDatatype
        from OpenGL.GL.shaders import compileProgram, compileShader
        from PySide6.QtGui import (
            QGuiApplication,
            QOffscreenSurface,
            QOpenGLContext,
            QSurfaceFormat,
        )

        self._gl = GL
        self._context = None
        self._surface = None
        self._owns_application = QGuiApplication.instance() is None
        self._application = QGuiApplication.instance() or QGuiApplication([])
        self._scene_program = 0
        self._cursor_program = 0
        self._vertex_array = 0
        self._native_texture = 0
        self._native_framebuffer = 0
        self._supersample_renderbuffer = 0
        self._supersample_framebuffer = 0
        self._cursor_texture = 0
        self._closed = False
        self._size = size
        self._scene_locations: dict[str, int] = {}
        self._cursor_locations: dict[str, int] = {}
        self._postprocess = HDRPostprocess() if "u_quality" in sources.scene_fragment else None

        try:
            surface_format = QSurfaceFormat()
            surface_format.setRenderableType(QSurfaceFormat.OpenGL)
            surface_format.setVersion(3, 3)
            surface_format.setProfile(QSurfaceFormat.CoreProfile)
            surface_format.setAlphaBufferSize(8)

            self._surface = QOffscreenSurface()
            self._surface.setFormat(surface_format)
            self._surface.create()
            self._context = QOpenGLContext()
            self._context.setFormat(surface_format)
            if not self._context.create():
                raise RuntimeError("OpenGL 3.3 context creation failed")
            if not self._context.makeCurrent(self._surface):
                raise RuntimeError("OpenGL context could not be made current")
            actual = self._context.format()
            if (actual.majorVersion(), actual.minorVersion()) < (3, 3):
                raise RuntimeError("OpenGL 3.3 core profile is required")

            ArrayDatatype.getRegistry().registerReturn("ctypesarrays")
            self._scene_program = int(
                compileProgram(
                    compileShader(sources.vertex, GL.GL_VERTEX_SHADER),
                    compileShader(
                        sources.scene_fragment.replace(
                            "#version 330 core",
                            "#version 330 core\n#define OUTPUT_HDR 1", 1
                        ) if self._postprocess else sources.scene_fragment,
                        GL.GL_FRAGMENT_SHADER,
                    ),
                )
            )
            self._cursor_program = int(
                compileProgram(
                    compileShader(sources.vertex, GL.GL_VERTEX_SHADER),
                    compileShader(sources.cursor_fragment, GL.GL_FRAGMENT_SHADER),
                )
            )
            self._scene_locations = self._uniform_locations(
                self._scene_program,
                ("u_resolution", "u_time", "u_quality", "u_debug_view")
                if self._postprocess else SCENE_UNIFORM_NAMES,
            )
            self._cursor_locations = self._uniform_locations(
                self._cursor_program, CURSOR_UNIFORM_NAMES
            )
            self._vertex_array = int(GL.glGenVertexArrays(1))
            if not self._vertex_array:
                raise RuntimeError("could not allocate the fullscreen vertex array")
            GL.glBindVertexArray(self._vertex_array)
            self._create_targets()
            self._create_cursor_texture()
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        except Exception:
            self.close()
            raise

    @property
    def gl(self):
        return self._gl

    def _uniform_locations(
        self, program: int, names: Sequence[str]
    ) -> dict[str, int]:
        locations = {
            name: int(self._gl.glGetUniformLocation(program, name))
            for name in names
        }
        missing = [name for name, location in locations.items() if location < 0]
        if missing:
            raise RuntimeError("inactive shader uniforms: " + ", ".join(missing))
        return locations

    def _create_targets(self) -> None:
        GL = self._gl
        width, height = self._size
        target_width = width * self.SUPERSAMPLE_SCALE
        target_height = height * self.SUPERSAMPLE_SCALE

        self._native_texture = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._native_texture)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA8,
            width,
            height,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            None,
        )
        self._native_framebuffer = int(GL.glGenFramebuffers(1))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._native_framebuffer)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER,
            GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D,
            self._native_texture,
            0,
        )
        self._require_complete_framebuffer("native")

        self._supersample_renderbuffer = int(GL.glGenRenderbuffers(1))
        GL.glBindRenderbuffer(
            GL.GL_RENDERBUFFER, self._supersample_renderbuffer
        )
        GL.glRenderbufferStorage(
            GL.GL_RENDERBUFFER,
            GL.GL_RGBA16F if self._postprocess else GL.GL_RGBA8,
            target_width,
            target_height,
        )
        self._supersample_framebuffer = int(GL.glGenFramebuffers(1))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._supersample_framebuffer)
        GL.glFramebufferRenderbuffer(
            GL.GL_FRAMEBUFFER,
            GL.GL_COLOR_ATTACHMENT0,
            GL.GL_RENDERBUFFER,
            self._supersample_renderbuffer,
        )
        self._require_complete_framebuffer("supersample")

    def _require_complete_framebuffer(self, label: str) -> None:
        status = int(self._gl.glCheckFramebufferStatus(self._gl.GL_FRAMEBUFFER))
        if status != self._gl.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"{label} framebuffer is incomplete (0x{status:04x})")

    @staticmethod
    def _synthetic_cursor_bgra() -> bytes:
        pixels = bytearray(32 * 32 * 4)
        for y in range(32):
            for x in range(32):
                inside = (x <= 7 and y <= 23 and x <= y // 2 + 2) or (
                    5 <= x <= 13 and 15 <= y <= 20
                )
                if not inside:
                    continue
                edge = x == 0 or y == 0 or x >= max(0, y // 2 + 1)
                alpha = 255
                value = 40 if edge else 242
                offset = (y * 32 + x) * 4
                pixels[offset:offset + 4] = bytes((value, value, value, alpha))
        return bytes(pixels)

    def _create_cursor_texture(self) -> None:
        GL = self._gl
        self._cursor_texture = int(GL.glGenTextures(1))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._cursor_texture)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(
            GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_BORDER
        )
        GL.glTexParameteri(
            GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_BORDER
        )
        GL.glTexParameterfv(
            GL.GL_TEXTURE_2D,
            GL.GL_TEXTURE_BORDER_COLOR,
            [0.0, 0.0, 0.0, 0.0],
        )
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA8,
            32,
            32,
            0,
            GL.GL_BGRA,
            GL.GL_UNSIGNED_BYTE,
            self._synthetic_cursor_bgra(),
        )

    def _draw_scene_pass(self, drag_strength: float) -> None:
        GL = self._gl
        width, height = self._size
        target_width = width * self.SUPERSAMPLE_SCALE
        target_height = height * self.SUPERSAMPLE_SCALE
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._supersample_framebuffer)
        GL.glViewport(0, 0, target_width, target_height)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glUseProgram(self._scene_program)
        locations = self._scene_locations
        GL.glUniform2f(
            locations["u_resolution"], float(target_width), float(target_height)
        )
        GL.glUniform1f(locations["u_time"], 0.7)
        if self._postprocess:
            GL.glUniform1f(locations["u_quality"], 1.0)
            GL.glUniform1f(locations["u_debug_view"], 0.0)
        else:
            GL.glUniform2f(
                locations["u_mouse"], target_width * 0.5, target_height * 0.5
            )
            GL.glUniform1f(locations["u_mouse_inside"], 0.0)
            GL.glUniform1f(locations["u_drag_strength"], drag_strength)
        GL.glBindVertexArray(self._vertex_array)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        if self._postprocess:
            self._postprocess.render(
                self._supersample_framebuffer, (target_width, target_height),
                self._native_framebuffer, (width, height), 0.7, 0,
            )
        else:
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self._supersample_framebuffer)
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self._native_framebuffer)
            GL.glBlitFramebuffer(
                0, 0, target_width, target_height, 0, 0, width, height,
                GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR,
            )
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._native_framebuffer)
        GL.glViewport(0, 0, width, height)

    def _draw_cursor_pass(self) -> None:
        GL = self._gl
        width, height = self._size
        center_x = width * 0.5
        center_y = height * 0.5
        body_axis_location = GL.glGetUniformLocation(self._cursor_program, "u_cursor_body_axis")
        stretch_cursor = body_axis_location >= 0
        if stretch_cursor:
            from black_hole_shaders import PROJECTED_LENS_RADIUS
            radius = height * 0.5 * PROJECTED_LENS_RADIUS
        else:
            radius = height * 0.35
        pointer_x = center_x + radius
        pointer_y = center_y
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._native_framebuffer)
        GL.glViewport(0, 0, width, height)
        GL.glUseProgram(self._cursor_program)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._cursor_texture)
        locations = self._cursor_locations
        GL.glUniform2f(locations["u_resolution"], float(width), float(height))
        GL.glUniform1i(locations["u_cursor_texture"], 0)
        GL.glUniform2f(
            locations["u_cursor_top_left_px"], pointer_x - 2.0, pointer_y + 2.0
        )
        GL.glUniform2f(locations["u_cursor_size_px"], 32.0, 32.0)
        GL.glUniform2f(locations["u_pointer_px"], pointer_x, pointer_y)
        GL.glUniform2f(locations["u_lens_center_px"], center_x, center_y)
        GL.glUniform1f(locations["u_lens_radius_px"], radius)
        GL.glUniform1f(locations["u_lens_half_width_px"], radius * (0.50 if stretch_cursor else 0.22))
        GL.glUniform1f(
            locations["u_max_displacement_px"],
            min(64.0, radius * 1.15) if stretch_cursor else min(12.0, max(6.0, 8.0 * width / 360.0)),
        )
        flow_location = GL.glGetUniformLocation(self._cursor_program, "u_cursor_time")
        GL.glUniform1f(flow_location, 300.0)
        GL.glUniform1f(locations["u_max_tangent_scale"], (1.72 if flow_location >= 0 else 4.8) if stretch_cursor else 1.45)
        # Optional on old snapshots / the legacy fallback shader.
        GL.glUniform1f(GL.glGetUniformLocation(self._cursor_program, "u_pixel_ratio"), 1.0)
        GL.glUniform2f(body_axis_location, 0.35, -1.0)
        GL.glUniform1f(GL.glGetUniformLocation(self._cursor_program, "u_cursor_body_extent"), 28.0)
        GL.glBindVertexArray(self._vertex_array)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

    def draw_scene(self) -> None:
        self._draw_scene_pass(0.0)

    def draw_cursor(self) -> None:
        GL = self._gl
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._native_framebuffer)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        self._draw_cursor_pass()

    def draw_total(self) -> None:
        self._draw_scene_pass(0.0)
        self._draw_cursor_pass()

    def draw_file_drag(self) -> None:
        self._draw_scene_pass(1.0)

    def finish(self) -> None:
        self._gl.glFinish()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        GL = self._gl
        context = self._context
        if context is not None and self._surface is not None:
            try:
                context.makeCurrent(self._surface)
            except Exception:
                pass
        if self._postprocess:
            try:
                self._postprocess.close()
            except Exception:
                pass
        deletions = (
            (GL.glDeleteProgram, (self._scene_program,)),
            (GL.glDeleteProgram, (self._cursor_program,)),
            (GL.glDeleteVertexArrays, (1, [self._vertex_array])),
            (GL.glDeleteTextures, (1, [self._cursor_texture])),
            (GL.glDeleteFramebuffers, (1, [self._supersample_framebuffer])),
            (GL.glDeleteRenderbuffers, (1, [self._supersample_renderbuffer])),
            (GL.glDeleteFramebuffers, (1, [self._native_framebuffer])),
            (GL.glDeleteTextures, (1, [self._native_texture])),
        )
        for function, arguments in deletions:
            if arguments[-1] == 0 or (
                isinstance(arguments[-1], list) and not arguments[-1][0]
            ):
                continue
            try:
                function(*arguments)
            except Exception:
                pass
        if context is not None:
            try:
                context.doneCurrent()
            except Exception:
                pass
        self._context = None
        self._surface = None


def benchmark_size(
    size: tuple[int, int],
    sources: ShaderSources,
    *,
    warmup: int,
    frames: int,
) -> dict[str, object]:
    renderer = OpenGLBenchmarkRenderer(size, sources)
    measured = measure_gpu_states(
        renderer,
        OpenGLQueryBackend(renderer.gl),
        warmup=warmup,
        frames=frames,
    )
    return build_result(size, measured, sources.cursor_shader_source)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_cli(argv)
    sources = load_shader_sources(options.project_root)
    for size in options.sizes:
        print(
            json.dumps(
                benchmark_size(
                    size,
                    sources,
                    warmup=options.warmup,
                    frames=options.frames,
                ),
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
