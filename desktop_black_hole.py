from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Sequence

from OpenGL import GL
from OpenGL.arrays.arraydatatype import ArrayDatatype
from OpenGL.GL.shaders import compileProgram, compileShader
from PySide6.QtCore import (
    QElapsedTimer,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QActionGroup,
    QCursor,
    QGuiApplication,
    QOpenGLContext,
    QSurfaceFormat,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
from ui_language import translate, translate_menu, activity_detail

from black_hole_postprocess import HDRPostprocess
from desktop_capture import DesktopCapture
from companion import CompanionOrbit
from codex_status import GlobalActivityMonitor, GlobalActivity, POLL_INTERVAL_MS
from dsh_status import DshActivityMonitor, aggregate_sources, disconnected as dsh_disconnected
from black_hole_shaders import (
    CURSOR_FRAGMENT_SHADER_SOURCE,
    CURSOR_DISTORTION_HALF_WIDTH_RATIO,
    CURSOR_INTERACTION_INNER_RATIO,
    CURSOR_INTERACTION_OUTER_RATIO,
    FULLSCREEN_VERTEX_SHADER_SOURCE as VERTEX_SHADER_SOURCE,
    PROJECTED_LENS_RADIUS,
    SCENE_FRAGMENT_SHADER_SOURCE as FRAGMENT_SHADER_SOURCE,
)
from windows_cursor import (
    MAX_CURSOR_SIDE,
    CursorProvider,
    CursorSnapshot,
    WindowsCursorProvider,
    cursor_body_geometry,
)

DEFAULT_WIDTH = 360
MIN_WIDTH = 240
MAX_WIDTH = 600
ASPECT_RATIO = 3.0 / 2.0
IDLE_INTERVAL_MS = 33
ACTIVE_INTERVAL_MS = 16
STARTUP_TIMEOUT_MS = 5_000
STARTUP_POLL_INTERVAL_MS = 25
SUPERSAMPLE_SCALE = 2
SCENE_QUALITY_STANDARD = 0
SCENE_QUALITY_HIGH = 1
SCENE_QUALITY_CINEMATIC = 2
MIN_DEBUG_VIEW = 0
MAX_DEBUG_VIEW = 9

_DEBUG_VIEW_KEYS = (
    Qt.Key_0,
    Qt.Key_1,
    Qt.Key_2,
    Qt.Key_3,
    Qt.Key_4,
    Qt.Key_5,
    Qt.Key_6,
    Qt.Key_7,
    Qt.Key_8,
    Qt.Key_9,
)


class ProxyCursorState(Enum):
    SYSTEM_VISIBLE = auto()
    PROXY_ARMED = auto()
    PROXY_VISIBLE = auto()


@dataclass(frozen=True, slots=True)
class ProxyTransition:
    request_id: int
    hide_system: bool = False
    restore_system: bool = False


class CursorProxyController:
    def __init__(self) -> None:
        self._state = ProxyCursorState.SYSTEM_VISIBLE
        self._request_id = 0

    @property
    def state(self) -> ProxyCursorState:
        return self._state

    @property
    def request_id(self) -> int:
        return self._request_id

    def arm(self) -> ProxyTransition:
        if self._state is ProxyCursorState.PROXY_VISIBLE:
            raise RuntimeError("deactivate the visible proxy before re-arming")
        self._request_id += 1
        self._state = ProxyCursorState.PROXY_ARMED
        return ProxyTransition(request_id=self._request_id)

    def upload_succeeded(
        self,
        request_id: int,
        *,
        still_eligible: bool = True,
    ) -> ProxyTransition:
        if (
            request_id != self._request_id
            or self._state is not ProxyCursorState.PROXY_ARMED
            or not still_eligible
        ):
            return ProxyTransition(request_id=self._request_id)
        self._state = ProxyCursorState.PROXY_VISIBLE
        return ProxyTransition(request_id=self._request_id, hide_system=True)

    def upload_failed(self, request_id: int) -> ProxyTransition:
        if (
            request_id == self._request_id
            and self._state is ProxyCursorState.PROXY_ARMED
        ):
            self._state = ProxyCursorState.SYSTEM_VISIBLE
        return ProxyTransition(request_id=self._request_id)

    def deactivate(self) -> ProxyTransition:
        restore_system = self._state is ProxyCursorState.PROXY_VISIBLE
        self._request_id += 1
        self._state = ProxyCursorState.SYSTEM_VISIBLE
        return ProxyTransition(
            request_id=self._request_id,
            restore_system=restore_system,
        )


@dataclass(frozen=True, slots=True)
class LensGeometry:
    center_x: float
    center_y: float
    radius: float
    interaction_inner_radius: float
    interaction_outer_radius: float
    distortion_half_width: float

    @classmethod
    def from_physical_size(
        cls,
        physical_width: int,
        physical_height: int,
    ) -> "LensGeometry":
        width = max(1, int(physical_width))
        height = max(1, int(physical_height))
        radius = PROJECTED_LENS_RADIUS * height * 0.5
        return cls(
            center_x=width * 0.5,
            center_y=height * 0.5,
            radius=radius,
            interaction_inner_radius=radius * CURSOR_INTERACTION_INNER_RATIO,
            interaction_outer_radius=radius * CURSOR_INTERACTION_OUTER_RATIO,
            distortion_half_width=(
                radius * CURSOR_DISTORTION_HALF_WIDTH_RATIO
            ),
        )

    @classmethod
    def from_logical_size(
        cls,
        logical_width: float,
        logical_height: float,
        dpr: float,
    ) -> "LensGeometry":
        if logical_width <= 0.0 or logical_height <= 0.0 or dpr <= 0.0:
            raise ValueError("logical dimensions and dpr must be positive")
        return cls.from_physical_size(
            max(1, round(logical_width * dpr)),
            max(1, round(logical_height * dpr)),
        )


def qt_position_to_gl_pixels(
    position: QPointF,
    logical_width: float,
    logical_height: float,
    dpr: float,
) -> tuple[float, float]:
    if logical_width <= 0.0 or logical_height <= 0.0 or dpr <= 0.0:
        raise ValueError("logical dimensions and dpr must be positive")
    physical_width = max(1, round(logical_width * dpr))
    physical_height = max(1, round(logical_height * dpr))
    scale_x = physical_width / logical_width
    scale_y = physical_height / logical_height
    return (
        float(position.x()) * scale_x,
        physical_height - float(position.y()) * scale_y,
    )


def cursor_rect_intersects_annulus(
    pointer_gl_px: tuple[float, float],
    snapshot: CursorSnapshot,
    lens_geometry: LensGeometry,
) -> bool:
    pointer_x, pointer_y = pointer_gl_px
    left = pointer_x - snapshot.hotspot_x
    right = left + snapshot.width
    top = pointer_y + snapshot.hotspot_y
    bottom = top - snapshot.height

    closest_x = min(max(lens_geometry.center_x, left), right)
    closest_y = min(max(lens_geometry.center_y, bottom), top)
    minimum_distance = math.hypot(
        closest_x - lens_geometry.center_x,
        closest_y - lens_geometry.center_y,
    )
    maximum_distance = max(
        math.hypot(
            corner_x - lens_geometry.center_x,
            corner_y - lens_geometry.center_y,
        )
        for corner_x in (left, right)
        for corner_y in (bottom, top)
    )
    return (
        minimum_distance <= lens_geometry.interaction_outer_radius + 1.0e-7
        and maximum_distance >= lens_geometry.interaction_inner_radius - 1.0e-7
    )


def cursor_candidate_intersects_annulus(
    pointer_gl_px: tuple[float, float],
    lens_geometry: LensGeometry,
    max_cursor_side: float = MAX_CURSOR_SIDE,
) -> bool:
    if max_cursor_side < 0.0:
        raise ValueError("max_cursor_side must not be negative")
    radial_extent = math.hypot(max_cursor_side, max_cursor_side)
    inner_radius = max(
        0.0,
        lens_geometry.interaction_inner_radius - radial_extent,
    )
    outer_radius = lens_geometry.interaction_outer_radius + radial_extent
    distance = math.hypot(
        pointer_gl_px[0] - lens_geometry.center_x,
        pointer_gl_px[1] - lens_geometry.center_y,
    )
    # Adding/subtracting the center can shift an inclusive boundary by one ULP.
    return inner_radius - 1.0e-7 <= distance <= outer_radius + 1.0e-7


def should_proxy_cursor(
    *,
    enabled: bool,
    pointer_inside: bool,
    intersects_lens: bool,
    gl_pipeline_ready: bool,
    file_drag_active: bool,
    moving_window: bool,
    menu_open: bool,
    closing: bool,
) -> bool:
    return (
        enabled
        and pointer_inside
        and intersects_lens
        and gl_pipeline_ready
        and not file_drag_active
        and not moving_window
        and not menu_open
        and not closing
    )



def scaled_size(width: int) -> QSize:
    clamped_width = max(MIN_WIDTH, min(MAX_WIDTH, int(width)))
    return QSize(clamped_width, round(clamped_width / ASPECT_RATIO))


def clamp_position(position: QPoint, size: QSize, screens: Sequence[QRect]) -> QPoint:
    if not screens:
        return QPoint(position)
    window_rect = QRect(position, size)
    if any(screen.contains(window_rect) for screen in screens):
        return QPoint(position)

    intersecting_screens = [
        screen for screen in screens if screen.intersects(window_rect)
    ]
    if intersecting_screens:
        def overlap_area(screen: QRect) -> int:
            intersection = screen.intersected(window_rect)
            return intersection.width() * intersection.height()

        target_screen = max(intersecting_screens, key=overlap_area)
    else:
        target_screen = min(
            screens,
            key=lambda screen: (screen.center() - position).manhattanLength(),
        )
    return QPoint(
        max(
            target_screen.left(),
            min(position.x(), target_screen.right() - size.width() + 1),
        ),
        max(
            target_screen.top(),
            min(position.y(), target_screen.bottom() - size.height() + 1),
        ),
    )


def _centered_position(size: QSize, screen: QRect | None) -> QPoint:
    if screen is None:
        return QPoint()
    return QPoint(
        screen.left() + (screen.width() - size.width()) // 2,
        screen.top() + (screen.height() - size.height()) // 2,
    )


def load_window_state(
    settings: QSettings,
    screens: Sequence[QRect],
) -> tuple[QPoint, QSize, bool]:
    try:
        width = int(settings.value("window/width", DEFAULT_WIDTH))
    except (TypeError, ValueError):
        width = DEFAULT_WIDTH
    size = scaled_size(width)

    stored_position = settings.value("window/position")
    if isinstance(stored_position, QPoint):
        position = QPoint(stored_position)
    else:
        position = _centered_position(size, screens[0] if screens else None)

    try:
        always_on_top = settings.value(
            "window/always_on_top",
            False,
            type=bool,
        )
    except (TypeError, ValueError):
        always_on_top = False
    return clamp_position(position, size, screens), size, bool(always_on_top)


def save_window_state(
    settings: QSettings,
    position: QPoint,
    size: QSize,
    always_on_top: bool,
) -> None:
    settings.setValue("window/position", QPoint(position))
    settings.setValue("window/width", int(size.width()))
    settings.setValue("window/always_on_top", bool(always_on_top))
    settings.sync()


def extract_local_paths(mime_data: QMimeData) -> list[str]:
    if not mime_data.hasUrls():
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for url in mime_data.urls():
        try:
            if not url.isValid() or not url.isLocalFile() or url.host():
                continue
            local_file = url.toLocalFile()
            if not local_file or "\x00" in local_file:
                continue
            if local_file.replace("\\", "/").startswith("//"):
                continue
            normalized = os.path.abspath(os.path.normpath(local_file))
            if normalized.replace("\\", "/").startswith("//"):
                continue
            identity = os.path.normcase(normalized)
            if identity in seen:
                continue
            seen.add(identity)
            paths.append(normalized)
        except (ValueError, OSError, RuntimeError):
            continue
    return paths


def frame_interval_ms(mouse_active: bool, drag_active: bool, moving: bool) -> int:
    return ACTIVE_INTERVAL_MS if mouse_active or drag_active or moving else IDLE_INTERVAL_MS


def configure_surface_format() -> QSurfaceFormat:
    surface_format = QSurfaceFormat()
    surface_format.setRenderableType(QSurfaceFormat.OpenGL)
    surface_format.setVersion(3, 3)
    surface_format.setProfile(QSurfaceFormat.CoreProfile)
    surface_format.setAlphaBufferSize(8)
    surface_format.setDepthBufferSize(0)
    surface_format.setStencilBufferSize(0)
    surface_format.setSamples(0)
    surface_format.setSwapInterval(1)
    return surface_format


class BlackHoleGLWidget(QOpenGLWidget):
    fatal_error = Signal(str)
    cursor_texture_ready = Signal(int)
    cursor_proxy_failed = Signal(int, str)
    cursor_pipeline_failed = Signal(str)
    system_cursor_restore_requested = Signal()
    desktop_lens_failed = Signal(str)

    _SCENE_UNIFORM_NAMES = (
        "u_resolution",
        "u_time",
        "u_quality",
        "u_debug_view",
        "u_companion",
        "u_companion_light",
        "u_companion_tail",
    )
    _CURSOR_UNIFORM_NAMES = (
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFormat(configure_surface_format())
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

        self._elapsed_timer = QElapsedTimer()
        self._elapsed_timer.start()
        self._last_frame_elapsed_ms = 0
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.PreciseTimer)
        self._frame_timer.timeout.connect(self.update)

        self._mouse_position: QPointF | None = None
        self._drag_target = 0.0
        self._drag_strength = 0.0
        self._moving = False
        self._scene_quality = SCENE_QUALITY_HIGH
        self._debug_view = MIN_DEBUG_VIEW
        self._postprocess = HDRPostprocess()
        self._desktop_frame = None
        self._companion_enabled = False
        self._companion_orbit = CompanionOrbit()

        self._scene_program = 0
        self._cursor_program = 0
        self._vertex_array = 0
        self._cursor_texture = 0
        self._supersample_framebuffer = 0
        self._supersample_renderbuffer = 0
        self._supersample_size = (0, 0)
        self._resource_context: QOpenGLContext | None = None
        self._render_failed = False
        self._gl_initialized = False
        self._scene_uniform_locations = {
            name: -1 for name in self._SCENE_UNIFORM_NAMES
        }
        self._cursor_uniform_locations = {
            name: -1 for name in self._CURSOR_UNIFORM_NAMES
        }
        self._cursor_pipeline_ready = False
        self._pending_cursor_request_id: int | None = None
        self._pending_cursor_snapshot: CursorSnapshot | None = None
        self._cursor_pose_request_id: int | None = None
        self._cursor_pointer_gl_px: tuple[float, float] | None = None
        self._cursor_lens_geometry: LensGeometry | None = None
        self._cursor_ready_request_id: int | None = None
        self._active_cursor_request_id: int | None = None
        self._uploaded_cursor_snapshot: CursorSnapshot | None = None
        self._uploaded_cursor_size = (0, 0)
        self._cursor_body_geometry = (0.35, -1.0, 24.0)
        self._cleanup_context: QOpenGLContext | None = None
        self._frame_timer.start(IDLE_INTERVAL_MS)

    @property
    def frame_interval(self) -> int:
        return self._frame_timer.interval()

    def set_desktop_frame(self, frame) -> None:
        self._desktop_frame = frame
        self.update()

    def set_companion_enabled(self, enabled: bool) -> None:
        self._companion_enabled = bool(enabled)
        self.update()

    def set_companion_activity(self, activity: str) -> None:
        self._companion_orbit.activity = activity
        self.update()

    @property
    def cursor_pipeline_ready(self) -> bool:
        return self._cursor_pipeline_ready

    @property
    def scene_quality(self) -> int:
        return self._scene_quality

    def set_scene_quality(self, quality: int) -> None:
        try:
            normalized = int(quality)
        except (TypeError, ValueError):
            normalized = SCENE_QUALITY_HIGH
        normalized = min(
            SCENE_QUALITY_CINEMATIC,
            max(SCENE_QUALITY_STANDARD, normalized),
        )
        if normalized == self._scene_quality:
            return
        self._scene_quality = normalized
        self.update()

    @property
    def debug_view(self) -> int:
        return self._debug_view

    def set_debug_view(self, debug_view: int) -> None:
        try:
            normalized = int(debug_view)
        except (TypeError, ValueError):
            normalized = MIN_DEBUG_VIEW
        normalized = min(MAX_DEBUG_VIEW, max(MIN_DEBUG_VIEW, normalized))
        if normalized == self._debug_view:
            return
        self._debug_view = normalized
        self.update()

    def arm_cursor_proxy(
        self,
        request_id: int,
        snapshot: CursorSnapshot,
        pointer_gl_px: tuple[float, float],
        lens_geometry: LensGeometry,
    ) -> None:
        self._pending_cursor_request_id = int(request_id)
        self._pending_cursor_snapshot = snapshot
        self._cursor_pose_request_id = int(request_id)
        self._cursor_pointer_gl_px = (
            float(pointer_gl_px[0]),
            float(pointer_gl_px[1]),
        )
        self._cursor_lens_geometry = lens_geometry
        self._cursor_ready_request_id = None
        self._active_cursor_request_id = None
        self.update()

    def update_cursor_proxy_pose(
        self,
        request_id: int,
        pointer_gl_px: tuple[float, float],
        lens_geometry: LensGeometry,
    ) -> None:
        if int(request_id) != self._cursor_pose_request_id:
            return
        self._cursor_pointer_gl_px = (
            float(pointer_gl_px[0]),
            float(pointer_gl_px[1]),
        )
        self._cursor_lens_geometry = lens_geometry
        self.update()

    def activate_cursor_proxy(self, request_id: int) -> bool:
        request_id = int(request_id)
        if (
            not self._cursor_pipeline_ready
            or not self._cursor_texture
            or self._uploaded_cursor_snapshot is None
            or self._cursor_ready_request_id != request_id
            or self._cursor_pose_request_id != request_id
            or self._cursor_pointer_gl_px is None
            or self._cursor_lens_geometry is None
        ):
            return False
        self._active_cursor_request_id = request_id
        self.update()
        return True

    def disarm_cursor_proxy(self, request_id: int | None = None) -> None:
        if request_id is not None:
            request_id = int(request_id)
            current_ids = {
                self._pending_cursor_request_id,
                self._cursor_pose_request_id,
                self._cursor_ready_request_id,
                self._active_cursor_request_id,
            }
            if request_id not in current_ids:
                return
        self._clear_cursor_request_state()
        self.update()

    def _clear_cursor_request_state(self) -> None:
        self._pending_cursor_request_id = None
        self._pending_cursor_snapshot = None
        self._cursor_pose_request_id = None
        self._cursor_pointer_gl_px = None
        self._cursor_lens_geometry = None
        self._cursor_ready_request_id = None
        self._active_cursor_request_id = None


    def set_drag_target(self, active: bool) -> None:
        self._drag_target = 1.0 if active else 0.0
        self._update_frame_interval()
        self.update()

    def set_mouse_state(self, position: QPointF | None) -> None:
        self._mouse_position = None if position is None else QPointF(position)
        self._update_frame_interval()
        self.update()

    def set_moving(self, active: bool) -> None:
        self._moving = bool(active)
        self._update_frame_interval()
        self.update()

    def _update_frame_interval(self) -> None:
        interval = frame_interval_ms(
            self._mouse_position is not None,
            self._drag_target > 0.0,
            self._moving,
        )
        if interval != self._frame_timer.interval():
            self._frame_timer.setInterval(interval)

    def initializeGL(self) -> None:
        self._gl_initialized = False
        try:
            context = self.context()
            if self._resource_context is not None and self._resource_context is not context:
                self.system_cursor_restore_requested.emit()
                self._reset_gl_handles()
                self._resource_context = None
                self._cleanup_context = None
            context_format = context.format()
            actual_version = (
                context_format.majorVersion(),
                context_format.minorVersion(),
            )
            if actual_version < (3, 3):
                raise RuntimeError(
                    "OpenGL 3.3 core profile is required; "
                    f"the current context is {actual_version[0]}.{actual_version[1]}"
                )

            if self._cleanup_context is not context:
                context.aboutToBeDestroyed.connect(self._cleanup_gl)
                self._cleanup_context = context
            self._resource_context = context
            ArrayDatatype.getRegistry().registerReturn("ctypesarrays")
            self._scene_program = int(
                compileProgram(
                    compileShader(VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
                    compileShader(
                        FRAGMENT_SHADER_SOURCE.replace(
                            "#version 330 core",
                            "#version 330 core\n#define OUTPUT_HDR 1",
                            1,
                        ),
                        GL.GL_FRAGMENT_SHADER,
                    ),
                )
            )
            self._vertex_array = int(GL.glGenVertexArrays(1))
            if not self._vertex_array:
                raise RuntimeError("could not create the fullscreen vertex array")
            GL.glBindVertexArray(self._vertex_array)
            self._scene_uniform_locations = {
                name: GL.glGetUniformLocation(self._scene_program, name)
                for name in self._SCENE_UNIFORM_NAMES
            }
            missing_scene_uniforms = [
                name
                for name, location in self._scene_uniform_locations.items()
                if location < 0
            ]
            if missing_scene_uniforms:
                raise RuntimeError(
                    "scene shader uniforms are inactive: "
                    + ", ".join(missing_scene_uniforms)
                )

            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)

            self._initialize_cursor_resources()
            self._render_failed = False
            self._gl_initialized = True
        except Exception as error:
            self.system_cursor_restore_requested.emit()
            self._release_current_gl_handles_noexcept()
            self._resource_context = None
            self._gl_initialized = False
            message = f"OpenGL initialization failed: {error}"
            QTimer.singleShot(0, lambda detail=message: self.fatal_error.emit(detail))

    def _initialize_cursor_resources(self) -> None:
        self._cursor_pipeline_ready = False
        partial_program = 0
        partial_texture = 0
        try:
            partial_program = int(
                compileProgram(
                    compileShader(VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
                    compileShader(
                        CURSOR_FRAGMENT_SHADER_SOURCE,
                        GL.GL_FRAGMENT_SHADER,
                    ),
                )
            )
            partial_texture = int(GL.glGenTextures(1))
            if not partial_texture:
                raise RuntimeError("could not create the cursor texture")
            locations = {
                name: GL.glGetUniformLocation(partial_program, name)
                for name in self._CURSOR_UNIFORM_NAMES
            }
            missing = [
                name for name, location in locations.items() if location < 0
            ]
            if missing:
                raise RuntimeError(
                    "cursor shader uniforms are inactive: " + ", ".join(missing)
                )
            self._cursor_program = partial_program
            self._cursor_texture = partial_texture
            self._cursor_uniform_locations = locations
            self._cursor_pipeline_ready = True
        except Exception as error:
            if partial_program:
                try:
                    GL.glDeleteProgram(partial_program)
                except Exception:
                    pass
            if partial_texture:
                try:
                    GL.glDeleteTextures(1, [partial_texture])
                except Exception:
                    pass
            self._cursor_program = 0
            self._cursor_texture = 0
            self._cursor_uniform_locations = {
                name: -1 for name in self._CURSOR_UNIFORM_NAMES
            }
            self._uploaded_cursor_snapshot = None
            self._uploaded_cursor_size = (0, 0)
            message = f"Cursor OpenGL pipeline unavailable: {error}"
            QTimer.singleShot(
                0,
                lambda detail=message: self.cursor_pipeline_failed.emit(detail),
            )

    def resizeGL(self, width: int, height: int) -> None:
        pixel_ratio = self.devicePixelRatioF()
        GL.glViewport(
            0,
            0,
            max(1, round(width * pixel_ratio)),
            max(1, round(height * pixel_ratio)),
        )

    def _reset_supersample_target(self) -> None:
        self._supersample_framebuffer = 0
        self._supersample_renderbuffer = 0
        self._supersample_size = (0, 0)

    def _reset_gl_handles(self) -> None:
        self._postprocess = HDRPostprocess()
        self._scene_program = 0
        self._cursor_program = 0
        self._vertex_array = 0
        self._cursor_texture = 0
        self._scene_uniform_locations = {
            name: -1 for name in self._SCENE_UNIFORM_NAMES
        }
        self._cursor_uniform_locations = {
            name: -1 for name in self._CURSOR_UNIFORM_NAMES
        }
        self._cursor_pipeline_ready = False
        self._uploaded_cursor_snapshot = None
        self._uploaded_cursor_size = (0, 0)
        self._clear_cursor_request_state()
        self._reset_supersample_target()

    def _release_current_gl_handles_noexcept(self) -> None:
        try:
            self._postprocess.close()
        except Exception:
            pass
        scene_program = self._scene_program
        cursor_program = self._cursor_program
        vertex_array = self._vertex_array
        cursor_texture = self._cursor_texture
        framebuffer = self._supersample_framebuffer
        renderbuffer = self._supersample_renderbuffer
        self._reset_gl_handles()
        deletions = (
            (GL.glDeleteProgram, (scene_program,)) if scene_program else None,
            (GL.glDeleteProgram, (cursor_program,)) if cursor_program else None,
            (
                (GL.glDeleteVertexArrays, (1, [vertex_array]))
                if vertex_array
                else None
            ),
            (
                (GL.glDeleteTextures, (1, [cursor_texture]))
                if cursor_texture
                else None
            ),
            (
                (GL.glDeleteFramebuffers, (1, [framebuffer]))
                if framebuffer
                else None
            ),
            (
                (GL.glDeleteRenderbuffers, (1, [renderbuffer]))
                if renderbuffer
                else None
            ),
        )
        for deletion in deletions:
            if deletion is None:
                continue
            function, arguments = deletion
            try:
                function(*arguments)
            except Exception:
                pass

    def _delete_supersample_target(self) -> None:
        framebuffer = self._supersample_framebuffer
        renderbuffer = self._supersample_renderbuffer
        self._reset_supersample_target()
        if framebuffer:
            GL.glDeleteFramebuffers(1, [framebuffer])
        if renderbuffer:
            GL.glDeleteRenderbuffers(1, [renderbuffer])

    @staticmethod
    def _restore_qt_target(
        framebuffer: int, pixel_width: int, pixel_height: int
    ) -> None:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
        GL.glViewport(0, 0, pixel_width, pixel_height)

    def _ensure_supersample_target(
        self, pixel_width: int, pixel_height: int, qt_framebuffer: int
    ) -> None:
        target_width = pixel_width * SUPERSAMPLE_SCALE
        target_height = pixel_height * SUPERSAMPLE_SCALE
        if (
            self._supersample_framebuffer
            and self._supersample_renderbuffer
            and self._supersample_size == (target_width, target_height)
        ):
            return

        self._delete_supersample_target()
        try:
            self._supersample_framebuffer = int(GL.glGenFramebuffers(1))
            GL.glBindFramebuffer(
                GL.GL_FRAMEBUFFER, self._supersample_framebuffer
            )
            self._supersample_renderbuffer = int(GL.glGenRenderbuffers(1))
            GL.glBindRenderbuffer(
                GL.GL_RENDERBUFFER, self._supersample_renderbuffer
            )
            GL.glRenderbufferStorage(
                GL.GL_RENDERBUFFER,
                GL.GL_RGBA16F,
                target_width,
                target_height,
            )
            GL.glFramebufferRenderbuffer(
                GL.GL_FRAMEBUFFER,
                GL.GL_COLOR_ATTACHMENT0,
                GL.GL_RENDERBUFFER,
                self._supersample_renderbuffer,
            )
            status = int(GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER))
            if status != GL.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError(
                    "supersample framebuffer is incomplete "
                    f"(0x{status:04x})"
                )
            self._supersample_size = (target_width, target_height)
        except Exception:
            self._delete_supersample_target()
            self._restore_qt_target(qt_framebuffer, pixel_width, pixel_height)
            raise

    def paintGL(self) -> None:
        if not self._scene_program or not self._vertex_array or self._render_failed:
            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            return

        elapsed_ms = self._elapsed_timer.elapsed()
        delta_seconds = max(0, elapsed_ms - self._last_frame_elapsed_ms) / 1000.0
        self._last_frame_elapsed_ms = elapsed_ms
        easing = 1.0 - math.exp(-10.0 * delta_seconds)
        self._drag_strength += (self._drag_target - self._drag_strength) * easing

        pixel_ratio = self.devicePixelRatioF()
        pixel_width = max(1, round(self.width() * pixel_ratio))
        pixel_height = max(1, round(self.height() * pixel_ratio))
        qt_framebuffer = int(self.defaultFramebufferObject())
        target_width = pixel_width * SUPERSAMPLE_SCALE
        target_height = pixel_height * SUPERSAMPLE_SCALE

        try:
            self._ensure_supersample_target(
                pixel_width, pixel_height, qt_framebuffer
            )
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glBindFramebuffer(
                GL.GL_FRAMEBUFFER, self._supersample_framebuffer
            )
            GL.glViewport(0, 0, target_width, target_height)
            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glUseProgram(self._scene_program)
            GL.glUniform2f(
                self._scene_uniform_locations["u_resolution"],
                float(target_width),
                float(target_height),
            )
            GL.glUniform1f(
                self._scene_uniform_locations["u_time"], elapsed_ms / 1000.0
            )
            GL.glUniform1f(
                self._scene_uniform_locations["u_quality"],
                float(self._scene_quality),
            )
            GL.glUniform1f(
                self._scene_uniform_locations["u_debug_view"],
                float(self._debug_view),
            )
            companion = self._companion_orbit.advance(delta_seconds)
            GL.glUniform4f(self._scene_uniform_locations["u_companion"],
                *companion[:3], companion[3] if self._companion_enabled else 0.0)
            GL.glUniform1f(self._scene_uniform_locations["u_companion_light"],companion[4])
            GL.glUniform4f(self._scene_uniform_locations["u_companion_tail"],
                          *self._companion_orbit.tail_uniform())
            GL.glBindVertexArray(self._vertex_array)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            desktop_origin = self.mapToGlobal(QPoint(0, 0))
            self._postprocess.desktop_frame = self._desktop_frame
            self._postprocess.desktop_geometry = (
                desktop_origin.x(), desktop_origin.y(), self.width(), self.height())
            self._postprocess.desktop_quality = self._scene_quality
            self._postprocess.render(
                self._supersample_framebuffer,
                (target_width, target_height),
                qt_framebuffer,
                (pixel_width, pixel_height),
                elapsed_ms / 1000.0,
                self._debug_view,
            )
            if self._postprocess.desktop_error:
                error = self._postprocess.desktop_error
                self._postprocess.desktop_error = ""
                self._desktop_frame = None
                self.desktop_lens_failed.emit(error)
            self._restore_qt_target(
                qt_framebuffer,
                pixel_width,
                pixel_height,
            )
        except Exception as error:
            try:
                self._restore_qt_target(
                    qt_framebuffer,
                    pixel_width,
                    pixel_height,
                )
            except Exception:
                pass
            self._report_scene_render_failure(error)
            return

        if self._cursor_pipeline_ready:
            self._process_pending_cursor_upload()
            self._draw_active_cursor(
                qt_framebuffer,
                pixel_width,
                pixel_height,
            )
        try:
            self._restore_qt_target(
                qt_framebuffer,
                pixel_width,
                pixel_height,
            )
        except Exception as error:
            self._report_scene_render_failure(error)

    def _report_scene_render_failure(self, error: Exception) -> None:
        if self._render_failed:
            return
        self._render_failed = True
        self.system_cursor_restore_requested.emit()
        message = f"OpenGL rendering failed: {error}"
        QTimer.singleShot(
            0,
            lambda detail=message: self.fatal_error.emit(detail),
        )

    def _mouse_uniforms(self, pixel_width: int, pixel_height: int) -> tuple[float, float, float]:
        if self._mouse_position is None:
            return pixel_width * 0.5, pixel_height * 0.5, 0.0
        logical_width = max(1.0, float(self.width()))
        logical_height = max(1.0, float(self.height()))
        normalized_x = min(1.0, max(0.0, self._mouse_position.x() / logical_width))
        normalized_y = min(1.0, max(0.0, self._mouse_position.y() / logical_height))
        return (
            normalized_x * pixel_width,
            (1.0 - normalized_y) * pixel_height,
            1.0,
        )

    def _process_pending_cursor_upload(self) -> None:
        request_id = self._pending_cursor_request_id
        snapshot = self._pending_cursor_snapshot
        if (
            request_id is None
            or snapshot is None
            or not self._cursor_pipeline_ready
            or not self._cursor_texture
        ):
            return
        try:
            if snapshot != self._uploaded_cursor_snapshot:
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._cursor_texture)
                GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
                GL.glTexParameteri(
                    GL.GL_TEXTURE_2D,
                    GL.GL_TEXTURE_MIN_FILTER,
                    GL.GL_LINEAR,
                )
                GL.glTexParameteri(
                    GL.GL_TEXTURE_2D,
                    GL.GL_TEXTURE_MAG_FILTER,
                    GL.GL_LINEAR,
                )
                GL.glTexParameteri(
                    GL.GL_TEXTURE_2D,
                    GL.GL_TEXTURE_WRAP_S,
                    GL.GL_CLAMP_TO_BORDER,
                )
                GL.glTexParameteri(
                    GL.GL_TEXTURE_2D,
                    GL.GL_TEXTURE_WRAP_T,
                    GL.GL_CLAMP_TO_BORDER,
                )
                GL.glTexParameterfv(
                    GL.GL_TEXTURE_2D,
                    GL.GL_TEXTURE_BORDER_COLOR,
                    (0.0, 0.0, 0.0, 0.0),
                )
                if self._uploaded_cursor_size == (
                    snapshot.width,
                    snapshot.height,
                ):
                    GL.glTexSubImage2D(
                        GL.GL_TEXTURE_2D,
                        0,
                        0,
                        0,
                        snapshot.width,
                        snapshot.height,
                        GL.GL_BGRA,
                        GL.GL_UNSIGNED_BYTE,
                        snapshot.pixels_bgra,
                    )
                else:
                    GL.glTexImage2D(
                        GL.GL_TEXTURE_2D,
                        0,
                        GL.GL_RGBA8,
                        snapshot.width,
                        snapshot.height,
                        0,
                        GL.GL_BGRA,
                        GL.GL_UNSIGNED_BYTE,
                        snapshot.pixels_bgra,
                    )
                self._uploaded_cursor_snapshot = snapshot
                self._cursor_body_geometry = cursor_body_geometry(snapshot)
                self._uploaded_cursor_size = (
                    snapshot.width,
                    snapshot.height,
                )
            self._pending_cursor_request_id = None
            self._pending_cursor_snapshot = None
            self._cursor_ready_request_id = request_id
            QTimer.singleShot(
                0,
                lambda generation=request_id: self.cursor_texture_ready.emit(
                    generation
                ),
            )
        except Exception as error:
            self._fail_cursor_request(
                request_id,
                f"Cursor texture upload failed: {error}",
            )

    def _draw_active_cursor(
        self,
        qt_framebuffer: int,
        pixel_width: int,
        pixel_height: int,
    ) -> None:
        request_id = self._active_cursor_request_id
        snapshot = self._uploaded_cursor_snapshot
        pointer = self._cursor_pointer_gl_px
        geometry = self._cursor_lens_geometry
        if (
            request_id is None
            or request_id != self._cursor_pose_request_id
            or request_id != self._cursor_ready_request_id
            or snapshot is None
            or pointer is None
            or geometry is None
            or not self._cursor_program
            or not self._cursor_texture
        ):
            return
        try:
            self._restore_qt_target(
                qt_framebuffer,
                pixel_width,
                pixel_height,
            )
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glUseProgram(self._cursor_program)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._cursor_texture)
            locations = self._cursor_uniform_locations
            top_left_x = pointer[0] - snapshot.hotspot_x
            top_left_y = pointer[1] + snapshot.hotspot_y
            pixel_ratio = max(1.0, self.devicePixelRatioF())
            maximum_displacement = min(64.0 * pixel_ratio, geometry.radius * 1.15)
            GL.glUniform2f(
                locations["u_resolution"],
                float(pixel_width),
                float(pixel_height),
            )
            GL.glUniform1i(locations["u_cursor_texture"], 0)
            GL.glUniform2f(
                locations["u_cursor_top_left_px"],
                top_left_x,
                top_left_y,
            )
            GL.glUniform2f(
                locations["u_cursor_size_px"],
                float(snapshot.width),
                float(snapshot.height),
            )
            GL.glUniform2f(
                locations["u_pointer_px"],
                pointer[0],
                pointer[1],
            )
            GL.glUniform2f(
                locations["u_lens_center_px"],
                geometry.center_x,
                geometry.center_y,
            )
            GL.glUniform1f(
                locations["u_lens_radius_px"],
                geometry.radius,
            )
            GL.glUniform1f(
                locations["u_lens_half_width_px"],
                geometry.distortion_half_width,
            )
            GL.glUniform1f(
                locations["u_max_displacement_px"],
                maximum_displacement,
            )
            GL.glUniform1f(
                locations["u_max_tangent_scale"],
                1.72,
            )
            GL.glUniform1f(locations["u_pixel_ratio"], max(1.0, self.devicePixelRatioF()))
            GL.glUniform2f(locations["u_cursor_body_axis"], *self._cursor_body_geometry[:2])
            GL.glUniform1f(locations["u_cursor_body_extent"], self._cursor_body_geometry[2])
            GL.glUniform1f(locations["u_cursor_time"], self._elapsed_timer.elapsed()/1000.0)
            GL.glBindVertexArray(self._vertex_array)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        except Exception as error:
            self._fail_cursor_request(
                request_id,
                f"Cursor overlay rendering failed: {error}",
            )

    def _fail_cursor_request(self, request_id: int, message: str) -> None:
        current_ids = {
            self._pending_cursor_request_id,
            self._cursor_pose_request_id,
            self._cursor_ready_request_id,
            self._active_cursor_request_id,
        }
        if request_id not in current_ids:
            return
        self._clear_cursor_request_state()
        QTimer.singleShot(
            0,
            lambda generation=request_id, detail=message: (
                self.cursor_proxy_failed.emit(generation, detail)
            ),
        )

    def _cleanup_gl(self) -> None:
        self.system_cursor_restore_requested.emit()
        self._gl_initialized = False
        self._cursor_pipeline_ready = False
        if (
            not self._scene_program
            and not self._cursor_program
            and not self._vertex_array
            and not self._cursor_texture
            and not self._supersample_framebuffer
            and not self._supersample_renderbuffer
        ):
            self._reset_gl_handles()
            self._cleanup_context = None
            self._resource_context = None
            return

        context = self.context()
        if (
            context is None
            or not context.isValid()
            or (
                self._resource_context is not None
                and self._resource_context is not context
            )
        ):
            self._reset_gl_handles()
            self._cleanup_context = None
            self._resource_context = None
            return

        self.makeCurrent()
        if QOpenGLContext.currentContext() is not context:
            self._reset_gl_handles()
            self._cleanup_context = None
            self._resource_context = None
            return
        try:
            self._release_current_gl_handles_noexcept()
            self._cleanup_context = None
            self._resource_context = None
        finally:
            self.doneCurrent()


class DesktopBlackHole(BlackHoleGLWidget):
    def __init__(
        self,
        settings: QSettings | None = None,
        parent=None,
        *,
        cursor_provider: CursorProvider | None = None,
        desktop_lens_default: bool = False,
        companion_default: bool = False,
    ):
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        language = self._settings.value("ui/language", "en")
        self._language = language if language in ("en", "zh") else "en"
        stored_scene_quality = self._settings.value(
            "effects/scene_quality",
            SCENE_QUALITY_HIGH,
        )
        self.set_scene_quality(stored_scene_quality)
        self._cursor_provider = (
            cursor_provider
            if cursor_provider is not None
            else WindowsCursorProvider()
        )
        self._cursor_proxy_controller = CursorProxyController()
        try:
            cursor_lens_enabled = self._settings.value(
                "effects/cursor_lens_enabled",
                True,
                type=bool,
            )
        except (TypeError, ValueError):
            cursor_lens_enabled = True
        self._cursor_lens_enabled = bool(cursor_lens_enabled)
        self._raw_pointer_position: QPointF | None = None
        self._file_drag_active = False
        self._menu_open = False
        self._closing = False
        self._candidate_active = False
        self._exact_intersects = False
        self._candidate_failure_latched = False
        self._known_cursor_bounds: CursorSnapshot | None = None
        self._preliminary_cursor_snapshot: CursorSnapshot | None = None
        self._proxy_request_snapshot: CursorSnapshot | None = None
        self._owns_blank_cursor = False
        self._saved_cursor_was_explicit = False
        self._saved_explicit_cursor: QCursor | None = None
        self.cursor_texture_ready.connect(self._on_cursor_texture_ready)
        self.cursor_proxy_failed.connect(self._on_cursor_proxy_failed)
        self.cursor_pipeline_failed.connect(self._on_cursor_pipeline_failed)
        self.system_cursor_restore_requested.connect(
            self._restore_system_cursor
        )
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        position, size, self._always_on_top = load_window_state(
            self._settings,
            screens,
        )
        try:
            self._desktop_layer = bool(self._settings.value(
                "window/desktop_layer", False, type=bool))
        except (TypeError, ValueError):
            self._desktop_layer = False
        # Desktop mode wins if an externally edited settings file enables both.
        if self._desktop_layer:
            self._always_on_top = False
        self.setWindowFlags(self._window_layer_flags())
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAcceptDrops(True)
        self.resize(size)
        self.move(position)
        self._move_offset: QPoint | None = None
        self._wheel_angle_remainder = 0
        self._desktop_capture = DesktopCapture(self)
        self._desktop_capture.frame_ready.connect(self.set_desktop_frame)
        self._desktop_capture.failed.connect(self._on_desktop_lens_failed)
        self.desktop_lens_failed.connect(self._desktop_capture.fail)
        self._desktop_lens_error = ""
        try:
            desktop_enabled = self._settings.value(
                "effects/desktop_lens_enabled", desktop_lens_default, type=bool)
        except (TypeError, ValueError):
            desktop_enabled = desktop_lens_default
        self._desktop_lens_enabled = bool(desktop_enabled)
        self._desktop_capture.set_enabled(self._desktop_lens_enabled)

        # Global, local hook integration. Old saved task bindings are ignored.
        codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self._activity_monitor = GlobalActivityMonitor(codex_root, self)
        self._activity_monitor.activity_ready.connect(self._accept_companion_activity)
        self._activity = GlobalActivity()
        self._activity_label = translate("Codex：未连接", self._language)
        self._source_activity = {"codex": self._activity, "dsh": dsh_disconnected()}
        self._source_enabled = {}
        for source, default in (("codex", True), ("dsh", False)):
            try:
                self._source_enabled[source] = self._settings.value(
                    "status/" + source + "_enabled", default, type=bool)
            except (TypeError, ValueError):
                self._source_enabled[source] = default
        self._dsh_monitor = None
        self._status_menu_action = None
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(POLL_INTERVAL_MS)
        self._activity_timer.timeout.connect(self._poll_companion_activity)
        try:
            companion_enabled = self._settings.value(
                "effects/companion_enabled", companion_default, type=bool)
        except (TypeError, ValueError):
            companion_enabled = companion_default
        self.set_companion_enabled(companion_enabled)

    def set_companion_enabled(self, enabled: bool) -> None:
        super().set_companion_enabled(enabled)
        if hasattr(self, "_activity_timer"):
            self._settings.setValue("effects/companion_enabled", bool(enabled))
            if enabled:
                self._activity_timer.start()
                self._poll_companion_activity()
            else:
                self._activity_timer.stop()

    def _poll_companion_activity(self) -> None:
        if self._source_enabled["codex"]:
            self._source_activity["codex"] = self._activity_monitor.poll()
        if self._source_enabled["dsh"]:
            if self._dsh_monitor is None:
                monitor = DshActivityMonitor(self)
                self._dsh_monitor = monitor
                monitor.activity_ready.connect(
                    lambda activity, owner=monitor: self._accept_dsh_activity(owner, activity))
            self._source_activity["dsh"] = self._dsh_monitor.poll()
        self._refresh_activity()

    def _accept_companion_activity(self, activity) -> None:
        if self._source_enabled["codex"]:
            self._source_activity["codex"] = activity
            self._refresh_activity()

    def _accept_dsh_activity(self, owner, activity) -> None:
        if owner is self._dsh_monitor and self._source_enabled["dsh"]:
            self._source_activity["dsh"] = activity
            self._refresh_activity()

    def _set_status_source(self, source: str, enabled: bool) -> None:
        self._source_enabled[source] = bool(enabled)
        self._settings.setValue("status/" + source + "_enabled", bool(enabled))
        if source == "dsh" and not enabled and self._dsh_monitor is not None:
            self._dsh_monitor.close()
            self._dsh_monitor = None
            self._source_activity["dsh"] = dsh_disconnected()
        if self._companion_enabled:
            self._poll_companion_activity()

    def _refresh_activity(self) -> None:
        if self._closing or not self._companion_enabled:
            return
        activity = aggregate_sources([self._source_activity[key]
                                      for key, enabled in self._source_enabled.items() if enabled])
        self._activity = activity
        self._activity_label = translate(activity.label, self._language)
        self.set_companion_activity(activity.state)
        if self._status_menu_action is not None:
            self._status_menu_action.setText(self._activity_label)
            self._status_menu_action.setToolTip(activity_detail(activity, self._language))

    def _show_codex_status(self) -> None:
        QMessageBox.information(self, translate("本机总状态灯", self._language),
                                activity_detail(self._activity, self._language))

    def set_language(self, language: str) -> None:
        if language not in ("en", "zh"):
            return
        self._language = language
        self._settings.setValue("ui/language", language)
        self._settings.sync()
        self._activity_label = translate(self._activity.label, language)
        if self._status_menu_action is not None:
            self._status_menu_action.setText(self._activity_label)
            self._status_menu_action.setToolTip(activity_detail(self._activity, language))

    @property
    def desktop_lens_enabled(self) -> bool:
        return self._desktop_lens_enabled

    def set_desktop_lens_enabled(self, enabled: bool) -> None:
        self._desktop_lens_enabled = bool(enabled)
        self._desktop_lens_error = ""
        self._postprocess.desktop_error = ""
        self._settings.setValue("effects/desktop_lens_enabled", bool(enabled))
        self._settings.sync()
        self._desktop_capture.set_enabled(enabled)

    def _on_desktop_lens_failed(self, message: str) -> None:
        self._desktop_lens_error = str(message)
        self.set_desktop_frame(None)
        print(f"Desktop lens disabled for this session: {message}", file=sys.stderr)

    @property
    def cursor_lens_enabled(self) -> bool:
        return self._cursor_lens_enabled

    def set_scene_quality(self, quality: int) -> None:
        super().set_scene_quality(quality)
        if hasattr(self, "_settings"):
            self._settings.setValue(
                "effects/scene_quality",
                self.scene_quality,
            )

    def _pointer_lens_context(
        self,
    ) -> tuple[tuple[float, float], LensGeometry] | None:
        if self._raw_pointer_position is None:
            return None
        logical_width = max(1.0, float(self.width()))
        logical_height = max(1.0, float(self.height()))
        dpr = max(0.01, float(self.devicePixelRatioF()))
        pointer = qt_position_to_gl_pixels(
            self._raw_pointer_position,
            logical_width,
            logical_height,
            dpr,
        )
        geometry = LensGeometry.from_logical_size(
            logical_width,
            logical_height,
            dpr,
        )
        return pointer, geometry

    def _proxy_base_allowed(self) -> bool:
        return should_proxy_cursor(
            enabled=self._cursor_lens_enabled,
            pointer_inside=self._raw_pointer_position is not None,
            intersects_lens=True,
            gl_pipeline_ready=self.cursor_pipeline_ready,
            file_drag_active=self._file_drag_active,
            moving_window=self._moving,
            menu_open=self._menu_open,
            closing=self._closing,
        )

    def _snapshot_is_exactly_eligible(
        self,
        snapshot: CursorSnapshot,
        pointer: tuple[float, float],
        geometry: LensGeometry,
    ) -> bool:
        return should_proxy_cursor(
            enabled=self._cursor_lens_enabled,
            pointer_inside=self._raw_pointer_position is not None,
            intersects_lens=cursor_rect_intersects_annulus(
                pointer,
                snapshot,
                geometry,
            ),
            gl_pipeline_ready=self.cursor_pipeline_ready,
            file_drag_active=self._file_drag_active,
            moving_window=self._moving,
            menu_open=self._menu_open,
            closing=self._closing,
        )

    def _capture_for_current_entry(
        self,
        pointer: tuple[float, float],
        geometry: LensGeometry,
    ) -> None:
        if self._cursor_proxy_controller.state is ProxyCursorState.PROXY_VISIBLE:
            return
        if (
            self._cursor_proxy_controller.state
            is not ProxyCursorState.SYSTEM_VISIBLE
        ):
            previous_request = self._cursor_proxy_controller.request_id
            self._cursor_proxy_controller.deactivate()
            BlackHoleGLWidget.disarm_cursor_proxy(self, previous_request)
        transition = self._cursor_proxy_controller.arm()
        try:
            snapshot = self._cursor_provider.capture()
        except Exception:
            snapshot = None
        if snapshot is None:
            self._candidate_failure_latched = True
            self._cursor_proxy_controller.upload_failed(transition.request_id)
            BlackHoleGLWidget.disarm_cursor_proxy(
                self,
                transition.request_id,
            )
            return

        self._known_cursor_bounds = snapshot
        self._preliminary_cursor_snapshot = snapshot
        exact = self._snapshot_is_exactly_eligible(
            snapshot,
            pointer,
            geometry,
        )
        self._exact_intersects = exact
        if not exact:
            return
        self._proxy_request_snapshot = snapshot
        self._preliminary_cursor_snapshot = None
        self.arm_cursor_proxy(
            transition.request_id,
            snapshot,
            pointer,
            geometry,
        )

    def _evaluate_cursor_proxy(self) -> None:
        context = self._pointer_lens_context()
        if context is None:
            if (
                self._candidate_active
                or self._owns_blank_cursor
                or self._cursor_proxy_controller.state
                is not ProxyCursorState.SYSTEM_VISIBLE
            ):
                self._restore_system_cursor()
            self._clear_candidate_entry()
            return
        pointer, geometry = context
        if not self._proxy_base_allowed():
            if (
                self._owns_blank_cursor
                or self._cursor_proxy_controller.state
                is not ProxyCursorState.SYSTEM_VISIBLE
            ):
                self._restore_system_cursor()
            return

        candidate = cursor_candidate_intersects_annulus(pointer, geometry)
        if not candidate:
            if (
                self._candidate_active
                or self._owns_blank_cursor
                or self._cursor_proxy_controller.state
                is not ProxyCursorState.SYSTEM_VISIBLE
            ):
                self._restore_system_cursor()
            self._clear_candidate_entry()
            return

        if not self._candidate_active:
            self._candidate_active = True
            self._exact_intersects = False
            self._candidate_failure_latched = False
            self._known_cursor_bounds = None
            self._preliminary_cursor_snapshot = None
            self._proxy_request_snapshot = None
            self._capture_for_current_entry(pointer, geometry)
            return

        if self._candidate_failure_latched:
            return

        if (
            self._cursor_proxy_controller.state
            is ProxyCursorState.PROXY_VISIBLE
        ):
            snapshot = (
                self._proxy_request_snapshot
                if self._proxy_request_snapshot is not None
                else self._known_cursor_bounds
            )
            exact = (
                snapshot is not None
                and self._snapshot_is_exactly_eligible(
                    snapshot,
                    pointer,
                    geometry,
                )
            )
            if exact:
                self._exact_intersects = True
                self.update_cursor_proxy_pose(
                    self._cursor_proxy_controller.request_id,
                    pointer,
                    geometry,
                )
                return
            self._exact_intersects = False
            self._preliminary_cursor_snapshot = None
            self._restore_system_cursor()
            return

        snapshot = self._known_cursor_bounds
        exact = (
            snapshot is not None
            and self._snapshot_is_exactly_eligible(
                snapshot,
                pointer,
                geometry,
            )
        )
        was_exact = self._exact_intersects
        self._exact_intersects = exact
        if exact and not was_exact:
            self._capture_for_current_entry(pointer, geometry)
        elif exact and self._proxy_request_snapshot is not None:
            self.update_cursor_proxy_pose(
                self._cursor_proxy_controller.request_id,
                pointer,
                geometry,
            )

    def _on_cursor_texture_ready(self, request_id: int) -> None:
        if (
            request_id != self._cursor_proxy_controller.request_id
            or self._cursor_proxy_controller.state
            is not ProxyCursorState.PROXY_ARMED
            or self._proxy_request_snapshot is None
        ):
            return
        context = self._pointer_lens_context()
        eligible = False
        if context is not None:
            pointer, geometry = context
            eligible = self._snapshot_is_exactly_eligible(
                self._proxy_request_snapshot,
                pointer,
                geometry,
            )
        if not eligible:
            self._cursor_proxy_controller.upload_succeeded(
                request_id,
                still_eligible=False,
            )
            self._exact_intersects = False
            return
        if not self.activate_cursor_proxy(request_id):
            self._cursor_proxy_controller.upload_failed(request_id)
            self._candidate_failure_latched = True
            BlackHoleGLWidget.disarm_cursor_proxy(self, request_id)
            self._proxy_request_snapshot = None
            return
        transition = self._cursor_proxy_controller.upload_succeeded(
            request_id,
            still_eligible=True,
        )
        if not transition.hide_system:
            BlackHoleGLWidget.disarm_cursor_proxy(self, request_id)
            return

        self._saved_cursor_was_explicit = self.testAttribute(Qt.WA_SetCursor)
        self._saved_explicit_cursor = (
            QCursor(self.cursor()) if self._saved_cursor_was_explicit else None
        )
        try:
            self.setCursor(Qt.BlankCursor)
        except Exception:
            self._candidate_failure_latched = True
            self._restore_system_cursor()
            return
        self._owns_blank_cursor = True
        self.update()

    def _on_cursor_proxy_failed(
        self,
        request_id: int,
        _message: str,
    ) -> None:
        if request_id != self._cursor_proxy_controller.request_id:
            return
        self._candidate_failure_latched = True
        self._cursor_proxy_controller.upload_failed(request_id)
        self._restore_system_cursor()

    def _on_cursor_pipeline_failed(self, _message: str) -> None:
        self._restore_system_cursor()

    def _restore_system_cursor(self) -> None:
        if self._owns_blank_cursor:
            try:
                if (
                    self._saved_cursor_was_explicit
                    and self._saved_explicit_cursor is not None
                ):
                    self.setCursor(self._saved_explicit_cursor)
                else:
                    self.unsetCursor()
            except Exception:
                try:
                    self.unsetCursor()
                except Exception:
                    pass
        self._owns_blank_cursor = False
        self._saved_cursor_was_explicit = False
        self._saved_explicit_cursor = None
        self._cursor_proxy_controller.deactivate()
        BlackHoleGLWidget.disarm_cursor_proxy(self)
        self._proxy_request_snapshot = None
        self._preliminary_cursor_snapshot = None
        self._exact_intersects = False

    def _clear_candidate_entry(self) -> None:
        self._candidate_active = False
        self._exact_intersects = False
        self._candidate_failure_latched = False
        self._known_cursor_bounds = None
        self._preliminary_cursor_snapshot = None
        self._proxy_request_snapshot = None

    def _set_cursor_lens_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._restore_system_cursor()
        self._clear_candidate_entry()
        self._cursor_lens_enabled = enabled
        self._settings.setValue(
            "effects/cursor_lens_enabled",
            enabled,
        )
        self._settings.sync()
        if enabled and not self._menu_open:
            self._evaluate_cursor_proxy()

    def _screen_geometries(self) -> list[QRect]:
        return [screen.availableGeometry() for screen in QGuiApplication.screens()]

    def _persist_window_state(self) -> None:
        self._settings.setValue("window/desktop_layer", self._desktop_layer)
        save_window_state(
            self._settings,
            self.pos(),
            self.size(),
            self._always_on_top,
        )

    def _current_screen_geometry(self) -> QRect | None:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _set_always_on_top(self, enabled: bool) -> None:
        self._apply_window_layer(
            always_on_top=bool(enabled),
            desktop_layer=self._desktop_layer and not enabled,
        )

    def _set_desktop_layer(self, enabled: bool) -> None:
        self._apply_window_layer(
            always_on_top=self._always_on_top and not enabled,
            desktop_layer=bool(enabled),
        )

    def _window_layer_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._desktop_layer:
            # Keep the top-level Tool so capture exclusion and global cursor
            # coordinates remain valid. Qt supports bottom-hint frameless windows.
            flags |= Qt.WindowStaysOnBottomHint
        elif self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        return flags

    def _apply_window_layer(self, *, always_on_top: bool, desktop_layer: bool) -> None:
        if (self._always_on_top, self._desktop_layer) == (always_on_top, desktop_layer):
            return
        self._restore_system_cursor()
        geometry = QRect(self.geometry())
        was_visible = self.isVisible()
        self._always_on_top = always_on_top
        self._desktop_layer = desktop_layer
        self.setWindowFlags(self._window_layer_flags())
        self.setGeometry(geometry)
        if was_visible:
            self.show()
            self.setGeometry(geometry)
        self._persist_window_state()
        self._evaluate_cursor_proxy()

    def _restore_default_size(self) -> None:
        old_size = self.size()
        new_size = scaled_size(DEFAULT_WIDTH)
        centered_position = self.pos() + QPoint(
            (old_size.width() - new_size.width()) // 2,
            (old_size.height() - new_size.height()) // 2,
        )
        self.resize(new_size)
        self.move(
            clamp_position(
                centered_position,
                new_size,
                self._screen_geometries(),
            )
        )
        self._persist_window_state()

    def _center_on_current_screen(self) -> None:
        self.move(_centered_position(self.size(), self._current_screen_geometry()))
        self._persist_window_state()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._restore_system_cursor()
            self._move_offset = event.globalPosition().toPoint() - self.pos()
            self.set_moving(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._set_pointer_from_event_position(event.position())
        if self._move_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._move_offset)
        event.accept()

    def _set_pointer_from_event_position(self, position: QPointF) -> None:
        self._raw_pointer_position = QPointF(position)
        height = max(1.0, float(self.height()))
        mouse_x = (2.0 * position.x() - self.width()) / height
        mouse_y = (self.height() - 2.0 * position.y()) / height
        self.set_mouse_state(QPointF(mouse_x, mouse_y))
        self._evaluate_cursor_proxy()

    def _begin_file_drag(self) -> None:
        if not self._file_drag_active:
            self._restore_system_cursor()
        self._file_drag_active = True
        self._preliminary_cursor_snapshot = None
        self._exact_intersects = False

    def _clear_drag_hover(self) -> None:
        self._restore_system_cursor()
        self._file_drag_active = False
        self._raw_pointer_position = None
        self.set_drag_target(False)
        self.set_mouse_state(None)
        self._clear_candidate_entry()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._move_offset = None
            self.set_moving(False)
            self._persist_window_state()
            self._evaluate_cursor_proxy()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._raw_pointer_position = None
        self.set_mouse_state(None)
        self._restore_system_cursor()
        self._clear_candidate_entry()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        angle_delta = event.angleDelta().y()
        self._wheel_angle_remainder += angle_delta
        if self._wheel_angle_remainder >= 0:
            notches = self._wheel_angle_remainder // 120
        else:
            notches = -((-self._wheel_angle_remainder) // 120)
        if notches:
            self._restore_system_cursor()
            self._wheel_angle_remainder -= notches * 120
            old_size = self.size()
            new_size = scaled_size(old_size.width() + 30 * notches)
            centered_position = self.pos() + QPoint(
                (old_size.width() - new_size.width()) // 2,
                (old_size.height() - new_size.height()) // 2,
            )
            position = clamp_position(
                centered_position,
                new_size,
                self._screen_geometries(),
            )
            self.resize(new_size)
            self.move(position)
            self._persist_window_state()
            self._evaluate_cursor_proxy()
            event.accept()
            return
        if angle_delta:
            event.accept()
            return
        super().wheelEvent(event)

    def dragEnterEvent(self, event) -> None:
        self._begin_file_drag()
        can_copy = bool(event.possibleActions() & Qt.CopyAction)
        if can_copy and extract_local_paths(event.mimeData()):
            self._set_pointer_from_event_position(event.position())
            self.set_drag_target(True)
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        self._clear_drag_hover()
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self._begin_file_drag()
        can_copy = bool(event.possibleActions() & Qt.CopyAction)
        if can_copy and extract_local_paths(event.mimeData()):
            self._set_pointer_from_event_position(event.position())
            self.set_drag_target(True)
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        self._clear_drag_hover()
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._clear_drag_hover()
        event.accept()

    def dropEvent(self, event) -> None:
        self._clear_drag_hover()
        if not event.possibleActions() & Qt.CopyAction:
            event.ignore()
            return
        paths = extract_local_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        for path in paths:
            print(path, flush=True)
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self._menu_open = True
        self._restore_system_cursor()
        try:
            menu = QMenu(self)
            menu.setToolTipsVisible(True)
            cursor_lens_action = menu.addAction("鼠标引力效果")
            cursor_lens_action.setCheckable(True)
            cursor_lens_action.setChecked(self._cursor_lens_enabled)
            cursor_lens_action.toggled.connect(
                self._set_cursor_lens_enabled
            )
            desktop_lens_action = menu.addAction("桌面背景扭曲")
            desktop_lens_action.setCheckable(True)
            desktop_lens_action.setChecked(self._desktop_lens_enabled)
            desktop_lens_action.setToolTip(
                ("背景扭曲暂不可用：" + self._desktop_lens_error)
                if self._desktop_lens_error else
                "实时读取本机桌面，不录制。关闭后恢复普通透明效果。")
            desktop_lens_action.toggled.connect(self.set_desktop_lens_enabled)
            companion_action = menu.addAction("齐马蓝伴星（状态灯）")
            companion_action.setToolTip(
                "空闲：外环慢飞；处理中：盘上方悬浮、五秒一圈、弧形长尾。\n"
                "未连接或状态待确认：外环暗光。可选 Codex / DSH 本机总状态。")
            companion_action.setCheckable(True)
            companion_action.setChecked(self._companion_enabled)
            companion_action.toggled.connect(self.set_companion_enabled)
            status_action = menu.addAction(self._activity_label)
            self._status_menu_action = status_action
            status_action.setToolTip(activity_detail(self._activity, self._language))
            status_action.triggered.connect(self._show_codex_status)
            sources_menu = menu.addMenu("状态来源")
            for source, label in (("codex", "Codex 总状态"), ("dsh", "DSH 总状态（可选）")):
                action = sources_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(self._source_enabled[source])
                action.setToolTip(activity_detail(self._source_activity[source], self._language))
                action.triggered.connect(
                    lambda enabled, key=source: self._set_status_source(key, enabled))
            from dsh_setup import show_dsh_setup
            sources_menu.addAction("DSH 接入向导", lambda: show_dsh_setup(self))
            if getattr(sys, "frozen", False):
                from packaged_status_setup import show_setup
                sources_menu.addSeparator()
                sources_menu.addAction("安装 Codex 接入（无需 Python）",
                                       lambda: show_setup(self))
                sources_menu.addAction("移除本黑洞的 Codex 接入",
                                       lambda: show_setup(self, uninstall=True))
            always_on_top_action = menu.addAction("始终置顶")
            always_on_top_action.setCheckable(True)
            always_on_top_action.setChecked(self._always_on_top)
            always_on_top_action.triggered.connect(self._set_always_on_top)
            desktop_layer_action = menu.addAction("固定在桌面层（置底）")
            desktop_layer_action.setCheckable(True)
            desktop_layer_action.setChecked(self._desktop_layer)
            desktop_layer_action.setToolTip(
                "留在其他应用窗口下面，保留右键、拖动和文件拖放。\n"
                "与始终置顶互斥；两项都关闭时为普通窗口。\n"
                "这是窗口置底，不是嵌入壁纸；Win+D 显示桌面时可能一起隐藏。")
            desktop_layer_action.triggered.connect(self._set_desktop_layer)
            layer_group = QActionGroup(menu)
            layer_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
            layer_group.addAction(always_on_top_action)
            layer_group.addAction(desktop_layer_action)
            quality_menu = menu.addMenu("渲染画质")
            quality_group = QActionGroup(quality_menu)
            quality_group.setExclusive(True)
            for label, quality in (
                ("标准", SCENE_QUALITY_STANDARD),
                ("高清", SCENE_QUALITY_HIGH),
                ("电影级", SCENE_QUALITY_CINEMATIC),
            ):
                action = quality_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(self.scene_quality == quality)
                action.triggered.connect(
                    lambda checked=False, value=quality: self.set_scene_quality(value)
                )
                quality_group.addAction(action)
            menu.addAction("恢复默认大小", self._restore_default_size)
            menu.addAction("在当前屏幕居中", self._center_on_current_screen)
            language_menu = menu.addMenu("语言 / Language")
            language_group = QActionGroup(language_menu)
            language_group.setExclusive(True)
            for code, label in (("en", "English"), ("zh", "中文")):
                action = language_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(self._language == code)
                action.triggered.connect(
                    lambda checked=False, value=code: self.set_language(value))
                language_group.addAction(action)
            from autostart import add_startup_action
            add_startup_action(menu, self)
            menu.addAction("退出", self.close)
            translate_menu(menu, self._language)
            menu.exec(event.globalPos())
            event.accept()
        finally:
            self._status_menu_action = None
            self._menu_open = False
            if not self._closing:
                self._evaluate_cursor_proxy()

    def keyPressEvent(self, event) -> None:
        try:
            debug_view = _DEBUG_VIEW_KEYS.index(event.key())
        except ValueError:
            super().keyPressEvent(event)
            return
        self.set_debug_view(debug_view)
        event.accept()

    def closeEvent(self, event) -> None:
        self._closing = True
        self._activity_timer.stop()
        self._activity_monitor.close()
        if self._dsh_monitor is not None:
            self._dsh_monitor.close()
        self._desktop_capture.stop()
        self._restore_system_cursor()
        self._persist_window_state()
        self._frame_timer.stop()
        if self._cleanup_context is not None and self._cleanup_context.isValid():
            self._cleanup_gl()
        super().closeEvent(event)

    def _mouse_uniforms(
        self,
        pixel_width: int,
        pixel_height: int,
    ) -> tuple[float, float, float]:
        if self._mouse_position is None:
            return super()._mouse_uniforms(pixel_width, pixel_height)
        aspect = max(1.0, float(self.width())) / max(1.0, float(self.height()))
        return (
            (self._mouse_position.x() / aspect + 1.0) * pixel_width * 0.5,
            (self._mouse_position.y() + 1.0) * pixel_height * 0.5,
            1.0,
        )


class _StartupFailureGuard:
    def __init__(self, window: DesktopBlackHole):
        self._window = window
        self._reported = False

    @property
    def reported(self) -> bool:
        return self._reported

    def report(self, message: str) -> None:
        if self._reported:
            return
        self._reported = True
        self._window.close()
        QMessageBox.critical(self._window, "Desktop Black Hole", message)


class _StartupMonitor:
    def __init__(
        self,
        window: DesktopBlackHole,
        failure_guard: _StartupFailureGuard,
        *,
        timeout_ms: int = STARTUP_TIMEOUT_MS,
        poll_interval_ms: int = STARTUP_POLL_INTERVAL_MS,
    ):
        self._window = window
        self._failure_guard = failure_guard
        self._timeout_ms = int(timeout_ms)
        self._elapsed_timer = QElapsedTimer()
        self._timer = QTimer(window)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(int(poll_interval_ms))
        self._timer.timeout.connect(self._poll)
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active or self._failure_guard.reported:
            return
        self._active = True
        self._elapsed_timer.start()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._active = False

    def report(self, message: str) -> None:
        self.stop()
        self._failure_guard.report(message)

    def _poll(self) -> None:
        if not self._active:
            return
        context = self._window.context()
        if (
            self._window._gl_initialized
            and context is not None
            and context.isValid()
        ):
            self.stop()
            return
        if self._elapsed_timer.elapsed() >= self._timeout_ms:
            self.report(
                "OpenGL initialization failed: no valid OpenGL 3.3 context was created."
            )


def main(argv: Sequence[str] | None = None) -> int:
    if QApplication.instance() is not None:
        raise RuntimeError("main() cannot run with an existing QApplication instance")
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication(list(sys.argv if argv is None else argv))
    app.setOrganizationName("Desktop Black Hole")
    app.setApplicationName("Desktop Black Hole")

    window = DesktopBlackHole(desktop_lens_default=True, companion_default=True)
    startup_failure_guard = _StartupFailureGuard(window)
    startup_monitor = _StartupMonitor(window, startup_failure_guard)
    window.fatal_error.connect(startup_monitor.report)
    window.show()
    startup_monitor.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
