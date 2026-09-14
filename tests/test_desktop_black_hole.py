import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from gl_test_support import SceneRenderOptions, analyze_scene_visuals, render_scene
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QCloseEvent,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import desktop_black_hole as dbh
from desktop_black_hole import (
    ACTIVE_INTERVAL_MS,
    BlackHoleGLWidget,
    DesktopBlackHole,
    IDLE_INTERVAL_MS,
    clamp_position,
    configure_surface_format,
    extract_local_paths,
    frame_interval_ms,
    load_window_state,
    save_window_state,
    scaled_size,
)
from windows_cursor import CursorSnapshot


def test_module_import_does_not_create_or_run_qapplication():
    probe = (
        "import desktop_black_hole; "
        "from PySide6.QtWidgets import QApplication; "
        "assert callable(desktop_black_hole.main); "
        "assert QApplication.instance() is None"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_main_rejects_an_existing_qapplication():
    probe = r'''
from PySide6.QtWidgets import QApplication
import desktop_black_hole

app = QApplication([])
try:
    desktop_black_hole.main(["desktop_black_hole.py"])
except RuntimeError as error:
    assert "existing QApplication" in str(error)
else:
    raise AssertionError("main() accepted an existing QApplication")
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_main_allows_deferred_opengl_startup_without_a_dialog():
    probe = r'''
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import desktop_black_hole

dialogs = []
window_state = {}
original_show = desktop_black_hole.DesktopBlackHole.show

class MessageBoxProbe:
    @staticmethod
    def critical(parent, title, message):
        dialogs.append((parent, title, message))

def show_and_schedule_exit(window):
    original_show(window)
    window_state["window"] = window

    def record_readiness_and_exit():
        context = window.context()
        window_state["visible"] = window.isVisible()
        window_state["gl_initialized"] = window._gl_initialized
        window_state["valid_context"] = (
            context is not None and context.isValid()
        )
        QApplication.quit()

    QTimer.singleShot(750, record_readiness_and_exit)

desktop_black_hole.QMessageBox = MessageBoxProbe
desktop_black_hole.DesktopBlackHole.show = show_and_schedule_exit
exit_code = desktop_black_hole.main(["desktop_black_hole.py"])
assert exit_code == 0
assert window_state["visible"]
assert window_state["gl_initialized"]
assert window_state["valid_context"]
assert dialogs == []
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_monitor_times_out_missing_context_and_closes_widget(
    qapp,
    monkeypatch,
    tmp_path,
):
    dialogs = []

    class MessageBoxProbe:
        @staticmethod
        def critical(parent, title, message):
            dialogs.append((parent, title, message))

    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = DesktopBlackHole(settings=settings)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    monkeypatch.setattr(dbh, "QMessageBox", MessageBoxProbe)
    window.show()
    monkeypatch.setattr(window, "context", lambda: None)
    guard = dbh._StartupFailureGuard(window)
    monitor = dbh._StartupMonitor(
        window,
        guard,
        timeout_ms=10,
        poll_interval_ms=1,
    )
    window.fatal_error.connect(monitor.report)

    monitor.start()
    QTest.qWait(30)

    assert not monitor.active
    assert not window.isVisible()
    assert not window._frame_timer.isActive()
    assert len(dialogs) == 1
    assert dialogs[0][0] is window
    assert "OpenGL" in dialogs[0][2]


def test_startup_monitor_shows_one_dialog_for_timeout_and_queued_failure(
    qapp,
    monkeypatch,
    tmp_path,
):
    dialogs = []

    class MessageBoxProbe:
        @staticmethod
        def critical(parent, title, message):
            dialogs.append((parent, title, message))

    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = DesktopBlackHole(settings=settings)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    monkeypatch.setattr(dbh, "QMessageBox", MessageBoxProbe)
    window.show()
    monkeypatch.setattr(window, "context", lambda: None)
    guard = dbh._StartupFailureGuard(window)
    monitor = dbh._StartupMonitor(
        window,
        guard,
        timeout_ms=10,
        poll_interval_ms=1,
    )
    window.fatal_error.connect(monitor.report)

    monitor.start()
    window.initializeGL()
    QTest.qWait(30)

    assert not monitor.active
    assert not window.isVisible()
    assert not window._frame_timer.isActive()
    assert len(dialogs) == 1
    assert dialogs[0][0] is window
    assert "OpenGL" in dialogs[0][2]


def test_startup_monitor_does_not_restart_after_an_immediate_fatal_error(
    qapp,
    monkeypatch,
    tmp_path,
):
    dialogs = []

    class MessageBoxProbe:
        @staticmethod
        def critical(parent, title, message):
            dialogs.append((parent, title, message))

    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = DesktopBlackHole(settings=settings)
    monkeypatch.setattr(dbh, "QMessageBox", MessageBoxProbe)
    guard = dbh._StartupFailureGuard(window)
    monitor = dbh._StartupMonitor(
        window,
        guard,
        timeout_ms=10,
        poll_interval_ms=1,
    )
    window.fatal_error.connect(monitor.report)

    window.fatal_error.emit("OpenGL initialization failed immediately")
    monitor.start()

    assert not monitor.active
    assert not window._frame_timer.isActive()
    assert len(dialogs) == 1


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def desktop_window_factory(qapp):
    windows = []
    object_names = set()

    def create(settings, **kwargs):
        # Existing interaction assertions exercise Chinese; language tests opt out.
        language = kwargs.pop('ui_language', 'zh')
        if language is not None:
            settings.setValue('ui/language', language)
        window = DesktopBlackHole(settings=settings, **kwargs)
        object_name = f"managed-desktop-window-{id(window)}"
        window.setObjectName(object_name)
        windows.append(window)
        object_names.add(object_name)
        return window

    yield create

    for window in windows:
        window.close()
        assert not window._frame_timer.isActive()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    leaked_names = {
        widget.objectName()
        for widget in QApplication.topLevelWidgets()
        if widget.objectName() in object_names
    }
    assert leaked_names == set()


def _render_shader(
    mouse=(0.0, 0.0),
    mouse_inside=0.0,
    drag_strength=0.0,
    instrument_edge_fade=False,
    isolate_photon_ring=False,
    disable_photon_ring=False,
    disable_lensed_glow=False,
    disable_drag_glow_boost=False,
    disable_backdrop=False,
    isolate_lens_shell=False,
    isolate_support=False,
    isolate_lens_shell_support=False,
    disable_lens_shell=False,
    disable_dark_shell=False,
    disable_warm_shell=False,
    coarse_trace=False,
    max_steps_override=None,
    render_size=(120, 80),
    render_time=0.7,
    production_resolve=False,
):
    frame = render_scene(
        SceneRenderOptions(
            mouse=mouse,
            mouse_inside=mouse_inside,
            drag_strength=drag_strength,
            instrument_edge_fade=instrument_edge_fade,
            isolate_photon_ring=isolate_photon_ring,
            disable_photon_ring=disable_photon_ring,
            disable_lensed_glow=disable_lensed_glow,
            disable_drag_glow_boost=disable_drag_glow_boost,
            disable_backdrop=disable_backdrop,
            isolate_lens_shell=isolate_lens_shell,
            isolate_support=isolate_support,
            isolate_lens_shell_support=isolate_lens_shell_support,
            disable_lens_shell=disable_lens_shell,
            disable_dark_shell=disable_dark_shell,
            disable_warm_shell=disable_warm_shell,
            coarse_trace=coarse_trace,
            max_steps_override=max_steps_override,
            render_size=render_size,
            render_time=render_time,
        ),
        production_resolve=production_resolve,
    )
    return frame.width, frame.height, frame.pixels


def _pixel(pixels, width, x, y):
    offset = (y * width + x) * 4
    return tuple(pixels[offset:offset + 4])


def _pixel_luminance(pixel):
    red, green, blue, _ = pixel
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


class _CursorProviderProbe:
    def __init__(self, results, on_capture=None):
        self._results = list(results)
        self._last = self._results[-1] if self._results else None
        self._on_capture = on_capture
        self.capture_count = 0

    def capture(self):
        self.capture_count += 1
        if self._on_capture is not None:
            self._on_capture()
        if self._results:
            self._last = self._results.pop(0)
        return self._last


def _task8_snapshot(source_handle=301, width=16, height=16):
    return CursorSnapshot(
        source_handle=source_handle,
        width=width,
        height=height,
        hotspot_x=width // 2,
        hotspot_y=height // 2,
        pixels_bgra=bytes((20, 40, 80, 255)) * (width * height),
    )


def _ring_pointer(window):
    return QPointF(
        window.width() * 0.5
        + dbh.PROJECTED_LENS_RADIUS * window.height() * 0.5,
        window.height() * 0.5,
    )


def _outside_lens_pointer(window):
    # Inside the window/candidate area, but beyond the entire cursor handoff
    # annulus. The center can legitimately intersect the wider safety margin.
    geometry = dbh.LensGeometry.from_logical_size(
        window.width(), window.height(), window.devicePixelRatioF())
    return QPointF(window.width() * 0.5
                   + geometry.interaction_outer_radius / window.devicePixelRatioF() + 32,
                   window.height() * 0.5)


def _enable_mock_cursor_pipeline(window, monkeypatch, activate=True):
    window._cursor_pipeline_ready = True
    window._cursor_texture = 501
    monkeypatch.setattr(
        window,
        "activate_cursor_proxy",
        lambda request_id: bool(activate),
    )


def test_cursor_lens_setting_defaults_on_and_round_trips_without_window_api_change(
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    first = desktop_window_factory(settings)
    assert first.cursor_lens_enabled is True
    assert len(load_window_state(settings, first._screen_geometries())) == 3

    first._set_cursor_lens_enabled(False)

    assert settings.value(
        "effects/cursor_lens_enabled",
        True,
        type=bool,
    ) is False
    second = desktop_window_factory(settings)
    assert second.cursor_lens_enabled is False
    second._set_cursor_lens_enabled(True)
    third = desktop_window_factory(settings)
    assert third.cursor_lens_enabled is True


def test_companion_global_scope_and_shutdown(
    desktop_window_factory, tmp_path, monkeypatch,
):
    from codex_status import GlobalActivity
    task_id = "00000000-0000-4000-8000-000000000123"
    monkeypatch.setenv("CODEX_THREAD_ID", task_id)
    # Resolve into a temporary empty source tree: unit tests never read real tasks.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    settings.setValue("companion/thread_id", "old-bound-task")
    window = desktop_window_factory(settings, companion_default=True)
    assert window._companion_enabled
    assert window._activity_timer.isActive()
    assert settings.value("companion/thread_id") == "old-bound-task"
    assert not hasattr(window, "_companion_thread_id")
    assert window._activity_timer.interval() == 2000
    monkeypatch.setattr(window._activity_monitor, "poll", lambda: GlobalActivity("busy", "Codex：处理中"))
    window._poll_companion_activity()
    assert window._companion_orbit.activity == "busy"
    assert window._activity_label == "Codex：处理中"
    window.set_companion_enabled(False)
    assert not window._activity_timer.isActive()
    window.close()
    assert window._activity_monitor.closed
    assert not window._activity_timer.isActive()
    monkeypatch.delenv("CODEX_THREAD_ID")
    restored = desktop_window_factory(settings, companion_default=True)
    assert not restored._companion_enabled
    assert not hasattr(restored, "_companion_thread_id")


def test_open_context_menu_refreshes_global_status(qapp, desktop_window_factory, tmp_path, monkeypatch):
    from codex_status import GlobalActivity
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    window = desktop_window_factory(
        QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat), companion_default=True)
    observed = {}

    def inspect():
        menu = qapp.activePopupWidget()
        try:
            window._accept_companion_activity(GlobalActivity("busy", "Codex：处理中", "本机总状态"))
            action = window._status_menu_action
            observed.update(text=action.text(), tooltip=action.toolTip(), enabled=action.isEnabled())
        finally:
            menu.close()

    QTimer.singleShot(0, inspect)
    qapp.sendEvent(window, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(20, 20), QPoint(120, 140)))
    assert observed == {"text": "Codex：处理中", "tooltip": "本机总状态", "enabled": True}
    assert window._status_menu_action is None


def test_dsh_is_opt_in_persists_and_rejects_late_callbacks(desktop_window_factory,tmp_path,monkeypatch):
    from codex_status import GlobalActivity
    monkeypatch.setenv("LOCALAPPDATA",str(tmp_path/"local"))
    monkeypatch.setenv("CODEX_HOME",str(tmp_path/"codex"))
    settings=QSettings(str(tmp_path/"state.ini"),QSettings.IniFormat)
    window=desktop_window_factory(settings,companion_default=True)
    assert window._dsh_monitor is None
    assert not window._source_enabled["dsh"]
    window._set_status_source("codex",False)
    window._set_status_source("dsh",True)
    monitor=window._dsh_monitor
    monkeypatch.setattr(monitor,"poll",lambda:GlobalActivity("busy","DSH：处理中"))
    window._poll_companion_activity()
    assert window._companion_orbit.activity=="busy"
    window._set_status_source("dsh",False)
    window._accept_dsh_activity(monitor,GlobalActivity("busy","late"))
    assert window._activity.state=="unknown"
    assert monitor.closed and window._dsh_monitor is None
    assert not settings.value("status/dsh_enabled",type=bool)
    assert not (tmp_path/"local"/"DesktopBlackHole").exists()


def test_context_menu_exposes_checked_cursor_lens_toggle(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    observed = {}

    def inspect_and_toggle():
        menu = qapp.activePopupWidget()
        assert menu is not None
        action = next(action for action in menu.actions() if action.text() == "鼠标引力效果")
        observed["text"] = action.text()
        observed["checkable"] = action.isCheckable()
        observed["checked"] = action.isChecked()
        action.trigger()
        menu.close()

    QTimer.singleShot(0, inspect_and_toggle)
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse,
        QPoint(20, 20),
        QPoint(120, 140),
    )
    qapp.sendEvent(window, event)

    assert observed == {
        "text": "鼠标引力效果",
        "checkable": True,
        "checked": True,
    }
    assert window.cursor_lens_enabled is False
    assert settings.value(
        "effects/cursor_lens_enabled",
        True,
        type=bool,
    ) is False


def test_matching_ready_activates_before_hiding_and_restores_inherited_cursor(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    capture_visibility = []
    provider = _CursorProviderProbe(
        [_task8_snapshot()],
        on_capture=lambda: capture_visibility.append(
            (window._owns_blank_cursor, window.cursor().shape())
        ),
    )
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    window.unsetCursor()
    assert not window.testAttribute(Qt.WA_SetCursor)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    activation_order = []
    unset_calls = []
    monkeypatch.setattr(
        window,
        "activate_cursor_proxy",
        lambda request_id: activation_order.append(
            ("activate", window._cursor_proxy_controller.state)
        )
        or True,
    )
    original_set_cursor = window.setCursor
    original_unset_cursor = window.unsetCursor

    def record_blank(cursor):
        shape = cursor if isinstance(cursor, Qt.CursorShape) else cursor.shape()
        activation_order.append(("set", shape))
        original_set_cursor(cursor)

    monkeypatch.setattr(window, "setCursor", record_blank)
    monkeypatch.setattr(
        window,
        "unsetCursor",
        lambda: (unset_calls.append(True), original_unset_cursor()),
    )

    window._set_pointer_from_event_position(_ring_pointer(window))
    request_id = window._cursor_proxy_controller.request_id

    assert provider.capture_count == 1
    assert capture_visibility == [(False, Qt.ArrowCursor)]
    assert window._owns_blank_cursor is False
    window.cursor_texture_ready.emit(request_id)

    assert activation_order[0] == ("activate", dbh.ProxyCursorState.PROXY_ARMED)
    assert activation_order[1] == ("set", Qt.BlankCursor)
    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.PROXY_VISIBLE
    assert window._owns_blank_cursor is True
    assert window.cursor().shape() == Qt.BlankCursor

    window._restore_system_cursor()
    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.SYSTEM_VISIBLE
    assert window._owns_blank_cursor is False
    assert unset_calls == [True]
    assert window.cursor().shape() == Qt.ArrowCursor


def test_candidate_capture_refreshes_only_on_exact_rising_edges_and_never_while_visible(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe(
        [
            _task8_snapshot(401),
            _task8_snapshot(402),
            _task8_snapshot(403),
        ]
    )
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)

    outside = _outside_lens_pointer(window)
    window._set_pointer_from_event_position(outside)
    assert provider.capture_count == 1
    assert window._exact_intersects is False

    ring = _ring_pointer(window)
    window._set_pointer_from_event_position(ring)
    assert provider.capture_count == 2
    request_id = window._cursor_proxy_controller.request_id
    window.cursor_texture_ready.emit(request_id)
    assert window._owns_blank_cursor

    window._set_pointer_from_event_position(ring + QPointF(2.0, 1.0))
    window._set_pointer_from_event_position(ring + QPointF(-2.0, -1.0))
    assert provider.capture_count == 2

    window._set_pointer_from_event_position(outside)
    assert not window._owns_blank_cursor
    assert provider.capture_count == 2
    window._set_pointer_from_event_position(ring)
    assert provider.capture_count == 3


def test_capture_failure_latches_until_leave_reentry_or_toggle(
    qapp,
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe([None, _task8_snapshot(502)])
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    ring = _ring_pointer(window)

    window._set_pointer_from_event_position(ring)
    window._set_pointer_from_event_position(ring + QPointF(2.0, 0.0))
    assert provider.capture_count == 1
    assert window._candidate_failure_latched

    qapp.sendEvent(window, QEvent(QEvent.Leave))
    window._set_pointer_from_event_position(ring)
    assert provider.capture_count == 2

    window.cursor_proxy_failed.emit(
        window._cursor_proxy_controller.request_id,
        "upload failed",
    )
    assert window._candidate_failure_latched
    count = provider.capture_count
    window._set_pointer_from_event_position(ring + QPointF(-2.0, 0.0))
    assert provider.capture_count == count
    window._set_cursor_lens_enabled(False)
    window._set_cursor_lens_enabled(True)
    assert provider.capture_count == count + 1


@pytest.mark.parametrize("dpr", (1.0, 1.25, 1.5, 2.0))
def test_ui_passes_hotspot_pose_in_physical_gl_pixels_for_each_dpi(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
    dpr,
):
    settings = QSettings(
        str(tmp_path / f"state-{dpr}.ini"),
        QSettings.IniFormat,
    )
    provider = _CursorProviderProbe([_task8_snapshot()])
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    monkeypatch.setattr(window, "devicePixelRatioF", lambda: dpr)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    position = _ring_pointer(window)

    window._set_pointer_from_event_position(position)

    physical_width = round(window.width() * dpr)
    physical_height = round(window.height() * dpr)
    expected_x = position.x() * physical_width / window.width()
    expected_y = physical_height - position.y() * physical_height / window.height()
    actual_x, actual_y = window._cursor_pointer_gl_px
    assert abs(actual_x - expected_x) <= 1.0
    assert abs(actual_y - expected_y) <= 1.0


def test_file_drag_never_captures_cursor_even_when_pointer_crosses_ring(
    qapp,
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe([_task8_snapshot()])
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    local_path = tmp_path / "drag.txt"
    local_path.write_text("drag", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(local_path))])
    ring = _ring_pointer(window).toPoint()
    enter = QDragEnterEvent(
        ring,
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    move = QDragMoveEvent(
        ring,
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )

    qapp.sendEvent(window, enter)
    qapp.sendEvent(window, move)

    assert enter.isAccepted()
    assert move.isAccepted()
    assert provider.capture_count == 0
    assert window._file_drag_active is True
    qapp.sendEvent(window, QDragLeaveEvent())
    assert window._file_drag_active is False


def test_stale_ready_and_failed_gl_activation_never_blank_cursor(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe(
        [_task8_snapshot(601), _task8_snapshot(602)]
    )
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    window.unsetCursor()
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=False)
    set_calls = []
    original_set = window.setCursor
    monkeypatch.setattr(
        window,
        "setCursor",
        lambda cursor: (set_calls.append(cursor), original_set(cursor)),
    )
    ring = _ring_pointer(window)

    window._set_pointer_from_event_position(ring)
    stale_request = window._cursor_proxy_controller.request_id
    window._restore_system_cursor()
    window.cursor_texture_ready.emit(stale_request)
    assert set_calls == []
    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.SYSTEM_VISIBLE

    window._set_pointer_from_event_position(ring)
    current_request = window._cursor_proxy_controller.request_id
    window.cursor_texture_ready.emit(current_request)
    assert set_calls == []
    assert window._owns_blank_cursor is False
    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.SYSTEM_VISIBLE
    assert window._candidate_failure_latched


def test_ready_after_exact_exit_stays_armed_and_refreshes_on_reentry(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe(
        [_task8_snapshot(701), _task8_snapshot(702)]
    )
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    window.unsetCursor()
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    ring = _ring_pointer(window)
    outside = _outside_lens_pointer(window)

    window._set_pointer_from_event_position(ring)
    old_request = window._cursor_proxy_controller.request_id
    window._set_pointer_from_event_position(outside)
    window.cursor_texture_ready.emit(old_request)

    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.PROXY_ARMED
    assert window._owns_blank_cursor is False
    assert provider.capture_count == 1

    window._set_pointer_from_event_position(ring)
    assert provider.capture_count == 2
    assert window._cursor_proxy_controller.request_id > old_request


def test_blank_cursor_install_exception_disarms_without_touching_original_cursor(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe([_task8_snapshot()])
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    window.setCursor(Qt.CrossCursor)
    original_shape = window.cursor().shape()
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    monkeypatch.setattr(
        window,
        "setCursor",
        lambda cursor: (_ for _ in ()).throw(RuntimeError("blank failed")),
    )

    window._set_pointer_from_event_position(_ring_pointer(window))
    request_id = window._cursor_proxy_controller.request_id
    window.cursor_texture_ready.emit(request_id)

    assert window._candidate_failure_latched
    assert window._owns_blank_cursor is False
    assert window._cursor_proxy_controller.state is dbh.ProxyCursorState.SYSTEM_VISIBLE
    assert window.cursor().shape() == original_shape
    assert window._active_cursor_request_id is None


def test_explicit_custom_cursor_is_restored_exactly_and_repeated_restore_is_idempotent(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    provider = _CursorProviderProbe([_task8_snapshot()])
    window = desktop_window_factory(settings, cursor_provider=provider)
    window.resize(360, 240)
    window.setCursor(Qt.CrossCursor)
    _enable_mock_cursor_pipeline(window, monkeypatch, activate=True)
    window._set_pointer_from_event_position(_ring_pointer(window))
    request_id = window._cursor_proxy_controller.request_id
    window.cursor_texture_ready.emit(request_id)
    assert window.cursor().shape() == Qt.BlankCursor

    restore_shapes = []
    original_set = window.setCursor

    def record_restore(cursor):
        shape = cursor if isinstance(cursor, Qt.CursorShape) else cursor.shape()
        restore_shapes.append(shape)
        original_set(cursor)

    monkeypatch.setattr(window, "setCursor", record_restore)
    window._restore_system_cursor()
    window._restore_system_cursor()

    assert restore_shapes == [Qt.CrossCursor]
    assert window.cursor().shape() == Qt.CrossCursor
    assert window.testAttribute(Qt.WA_SetCursor)


def test_restore_before_blank_never_mutates_qwidget_cursor(
    desktop_window_factory,
    monkeypatch,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(
        settings,
        cursor_provider=_CursorProviderProbe([]),
    )
    mutations = []
    monkeypatch.setattr(
        window,
        "setCursor",
        lambda *args: mutations.append(("set", args)),
    )
    monkeypatch.setattr(
        window,
        "unsetCursor",
        lambda *args: mutations.append(("unset", args)),
    )

    window._restore_system_cursor()
    window._restore_system_cursor()
    window.cursor_pipeline_failed.emit("soft failure")
    window.system_cursor_restore_requested.emit()

    assert mutations == []
    assert not hasattr(window, "_capture_timer")


def test_surface_format_requests_alpha_and_opengl_33():
    surface_format = configure_surface_format()
    assert (surface_format.majorVersion(), surface_format.minorVersion()) == (3, 3)
    assert surface_format.profile() == QSurfaceFormat.CoreProfile
    assert surface_format.alphaBufferSize() >= 8
    assert surface_format.samples() == 0


def test_gl_widget_starts_with_idle_timer(qapp):
    widget = BlackHoleGLWidget()
    assert widget.frame_interval == IDLE_INTERVAL_MS
    widget.set_drag_target(True)
    assert widget.frame_interval == ACTIVE_INTERVAL_MS
    widget.set_drag_target(False)
    assert widget.frame_interval == IDLE_INTERVAL_MS
    widget.set_mouse_state(QPointF(12.0, 8.0))
    assert widget.frame_interval == ACTIVE_INTERVAL_MS
    widget.set_mouse_state(None)
    assert widget.frame_interval == IDLE_INTERVAL_MS
    widget.set_moving(True)
    assert widget.frame_interval == ACTIVE_INTERVAL_MS
    widget.set_moving(False)
    assert widget.frame_interval == IDLE_INTERVAL_MS
    widget.deleteLater()


def test_cursor_proxy_api_queues_only_cpu_state_until_paint(qapp, monkeypatch):
    widget = BlackHoleGLWidget()
    snapshot = CursorSnapshot(
        source_handle=7,
        width=2,
        height=2,
        hotspot_x=1,
        hotspot_y=1,
        pixels_bgra=bytes((0, 0, 0, 0)) * 4,
    )
    geometry = dbh.LensGeometry.from_physical_size(360, 240)
    gl_calls = []
    monkeypatch.setattr(
        dbh.GL,
        "glGenTextures",
        lambda *args: gl_calls.append(("gen", args)),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glBindTexture",
        lambda *args: gl_calls.append(("bind", args)),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glTexImage2D",
        lambda *args: gl_calls.append(("image", args)),
    )

    widget.arm_cursor_proxy(11, snapshot, (210.0, 120.0), geometry)
    widget.update_cursor_proxy_pose(11, (212.0, 121.0), geometry)

    assert gl_calls == []
    assert widget.activate_cursor_proxy(11) is False
    widget.disarm_cursor_proxy(11)
    assert gl_calls == []
    widget.deleteLater()


def _cursor_snapshot(
    source_handle=7,
    width=2,
    height=2,
    hotspot_x=1,
    hotspot_y=1,
    pixel=(8, 16, 24, 255),
):
    return CursorSnapshot(
        source_handle=source_handle,
        width=width,
        height=height,
        hotspot_x=hotspot_x,
        hotspot_y=hotspot_y,
        pixels_bgra=bytes(pixel) * (width * height),
    )


def _prepare_cursor_paint_probe(widget, monkeypatch):
    monkeypatch.setattr(dbh.GL, "glUniform4f", lambda *args: None)
    widget.resize(200, 100)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 1.0)
    monkeypatch.setattr(widget, "defaultFramebufferObject", lambda: 41)
    widget._scene_program = 7
    widget._cursor_program = 9
    widget._vertex_array = 8
    widget._cursor_texture = 10
    widget._cursor_pipeline_ready = True
    widget._scene_uniform_locations = {
        name: f"scene:{name}" for name in widget._SCENE_UNIFORM_NAMES
    }
    widget._cursor_uniform_locations = {
        name: f"cursor:{name}" for name in widget._CURSOR_UNIFORM_NAMES
    }
    widget._supersample_framebuffer = 71
    widget._supersample_renderbuffer = 72
    widget._supersample_size = (400, 200)
    calls = {
        "events": [],
        "draw": [],
        "image": [],
        "sub_image": [],
        "tex_parameter": [],
        "pixel_store": [],
        "uniform_2f": [],
        "uniform_1f": [],
        "uniform_1i": [],
        "enable": [],
        "disable": [],
        "blend": [],
        "framebuffer": [],
        "viewport": [],
    }
    monkeypatch.setattr(
        widget._postprocess, "render",
        lambda *args: calls["events"].append(("hdr_resolve",) + args),
    )

    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glClear", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glDisable",
        lambda value: (
            calls["disable"].append(value),
            calls["events"].append(("disable", value)),
        ),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glEnable",
        lambda value: calls["enable"].append(value),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glBlendFunc",
        lambda *args: calls["blend"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glBindFramebuffer",
        lambda target, handle: (
            calls["framebuffer"].append((target, handle)),
            calls["events"].append(("framebuffer", target, handle)),
        ),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glViewport",
        lambda *args: (
            calls["viewport"].append(args),
            calls["events"].append(("viewport",) + args),
        ),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glUseProgram",
        lambda handle: calls["events"].append(("program", handle)),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glUniform2f",
        lambda *args: calls["uniform_2f"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glUniform1f",
        lambda *args: calls["uniform_1f"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glUniform1i",
        lambda *args: calls["uniform_1i"].append(args),
    )
    monkeypatch.setattr(dbh.GL, "glBindVertexArray", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glDrawArrays",
        lambda *args: (
            calls["draw"].append(args),
            calls["events"].append(("draw",) + args),
        ),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glBlitFramebuffer",
        lambda *args: calls["events"].append(("blit",) + args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glActiveTexture",
        lambda *args: calls["events"].append(("active_texture",) + args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glBindTexture",
        lambda *args: calls["events"].append(("texture",) + args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glPixelStorei",
        lambda *args: calls["pixel_store"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glTexParameteri",
        lambda *args: calls["tex_parameter"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glTexParameterfv",
        lambda *args: calls["tex_parameter"].append(args),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glTexImage2D",
        lambda *args: (
            calls["image"].append(args),
            calls["events"].append(("image",)),
        ),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glTexSubImage2D",
        lambda *args: (
            calls["sub_image"].append(args),
            calls["events"].append(("sub_image",)),
        ),
    )
    return calls


def test_cursor_upload_reuse_and_native_overlay_draw_order(qapp, monkeypatch):
    widget = BlackHoleGLWidget()
    calls = _prepare_cursor_paint_probe(widget, monkeypatch)
    geometry = dbh.LensGeometry.from_physical_size(200, 100)
    ready = []
    widget.cursor_texture_ready.connect(ready.append)
    first = _cursor_snapshot()

    widget.arm_cursor_proxy(1, first, (135.0, 50.0), geometry)
    widget.paintGL()
    qapp.processEvents()

    assert ready == [1]
    assert len(calls["draw"]) == 1
    assert len(calls["image"]) == 1
    image = calls["image"][0]
    assert image[2] == dbh.GL.GL_RGBA8
    assert image[6:8] == (dbh.GL.GL_BGRA, dbh.GL.GL_UNSIGNED_BYTE)
    assert calls["pixel_store"] == [(dbh.GL.GL_UNPACK_ALIGNMENT, 1)]
    scene_draw_index = calls["events"].index(
        ("draw", dbh.GL.GL_TRIANGLES, 0, 3)
    )
    blit_index = next(
        index for index, event in enumerate(calls["events"]) if event[0] == "hdr_resolve"
    )
    image_index = calls["events"].index(("image",))
    qt_restore_index = next(
        index
        for index, event in enumerate(calls["events"])
        if index > blit_index
        and event == ("framebuffer", dbh.GL.GL_FRAMEBUFFER, 41)
    )
    assert scene_draw_index < blit_index < qt_restore_index < image_index

    assert widget.activate_cursor_proxy(1) is True
    calls["draw"].clear()
    calls["events"].clear()
    widget.paintGL()
    assert len(calls["draw"]) == 2
    assert calls["events"].index(("program", 7)) < next(
        index for index, event in enumerate(calls["events"]) if event[0] == "hdr_resolve"
    )
    assert calls["events"].index(("program", 9)) > next(
        index for index, event in enumerate(calls["events"]) if event[0] == "hdr_resolve"
    )
    assert calls["framebuffer"][-1] == (dbh.GL.GL_FRAMEBUFFER, 41)
    assert calls["viewport"][-1] == (0, 0, 200, 100)
    assert dbh.GL.GL_BLEND in calls["enable"]
    assert dbh.GL.GL_DEPTH_TEST in calls["disable"]
    assert dbh.GL.GL_CULL_FACE in calls["disable"]
    assert dbh.GL.GL_SCISSOR_TEST in calls["disable"]
    assert (dbh.GL.GL_ONE, dbh.GL.GL_ONE_MINUS_SRC_ALPHA) in calls["blend"]
    assert ("cursor:u_cursor_texture", 0) in calls["uniform_1i"]
    cursor_vector_uniforms = {
        call[0] for call in calls["uniform_2f"] if call[0].startswith("cursor:")
    }
    assert cursor_vector_uniforms == {
        "cursor:u_resolution",
        "cursor:u_cursor_top_left_px",
        "cursor:u_cursor_size_px",
        "cursor:u_pointer_px",
        "cursor:u_lens_center_px",
        "cursor:u_cursor_body_axis",
    }
    cursor_scalars = dict(calls["uniform_1f"])
    assert {
        name for name in cursor_scalars if name.startswith("cursor:")
    } == {
        "cursor:u_lens_radius_px",
        "cursor:u_lens_half_width_px",
        "cursor:u_max_displacement_px",
        "cursor:u_max_tangent_scale",
        "cursor:u_pixel_ratio",
        "cursor:u_cursor_body_extent",
        "cursor:u_cursor_time",
    }
    assert cursor_scalars["cursor:u_max_tangent_scale"] == 1.72
    assert cursor_scalars["cursor:u_pixel_ratio"] == 1.0
    assert cursor_scalars["cursor:u_max_displacement_px"] == pytest.approx(
        min(64.0, geometry.radius * 1.15))
    assert cursor_scalars["cursor:u_cursor_body_extent"] >= 4.0

    second = _cursor_snapshot(source_handle=8, pixel=(9, 17, 25, 255))
    widget.arm_cursor_proxy(2, second, (135.0, 50.0), geometry)
    widget.paintGL()
    qapp.processEvents()
    assert ready[-1] == 2
    assert len(calls["sub_image"]) == 1

    calls["image"].clear()
    calls["sub_image"].clear()
    calls["tex_parameter"].clear()
    widget.arm_cursor_proxy(3, second, (136.0, 51.0), geometry)
    widget.paintGL()
    qapp.processEvents()
    assert ready[-1] == 3
    assert calls["image"] == []
    assert calls["sub_image"] == []
    assert calls["tex_parameter"] == []

    larger = _cursor_snapshot(
        source_handle=9,
        width=3,
        height=2,
        hotspot_x=1,
        hotspot_y=1,
    )
    widget.arm_cursor_proxy(4, larger, (136.0, 51.0), geometry)
    widget.paintGL()
    qapp.processEvents()
    assert ready[-1] == 4
    assert len(calls["image"]) == 1
    widget._reset_gl_handles()
    widget.deleteLater()


@pytest.mark.parametrize("failure_stage", ("upload", "draw"))
def test_cursor_pass_failure_is_generation_scoped_and_never_fatal(
    qapp,
    monkeypatch,
    failure_stage,
):
    widget = BlackHoleGLWidget()
    calls = _prepare_cursor_paint_probe(widget, monkeypatch)
    geometry = dbh.LensGeometry.from_physical_size(200, 100)
    failures = []
    fatal = []
    restore = []
    widget.cursor_proxy_failed.connect(
        lambda request_id, message: failures.append((request_id, message))
    )
    widget.fatal_error.connect(fatal.append)
    widget.system_cursor_restore_requested.connect(lambda: restore.append(True))
    snapshot = _cursor_snapshot()

    if failure_stage == "upload":
        monkeypatch.setattr(
            dbh.GL,
            "glTexImage2D",
            lambda *args: (_ for _ in ()).throw(RuntimeError("upload exploded")),
        )
        widget.arm_cursor_proxy(21, snapshot, (135.0, 50.0), geometry)
        widget.paintGL()
    else:
        widget.arm_cursor_proxy(21, snapshot, (135.0, 50.0), geometry)
        widget.paintGL()
        qapp.processEvents()
        assert widget.activate_cursor_proxy(21)
        original_use_program = dbh.GL.glUseProgram

        def fail_cursor_program(handle):
            if handle == 9:
                raise RuntimeError("draw exploded")
            return original_use_program(handle)

        monkeypatch.setattr(dbh.GL, "glUseProgram", fail_cursor_program)
        widget.paintGL()

    qapp.processEvents()
    widget.paintGL()
    qapp.processEvents()
    assert len(failures) == 1
    assert failures[0][0] == 21
    assert failure_stage in failures[0][1].lower()
    assert fatal == []
    assert restore == []
    assert widget._render_failed is False
    assert calls["framebuffer"][-1] == (dbh.GL.GL_FRAMEBUFFER, 41)
    widget._reset_gl_handles()
    widget.deleteLater()


def test_resize_gl_sets_a_physical_pixel_viewport(qapp, monkeypatch):
    widget = BlackHoleGLWidget()
    viewport_calls = []
    monkeypatch.setattr(dbh.GL, "glViewport", lambda *args: viewport_calls.append(args))

    widget.resizeGL(120, 80)

    pixel_ratio = widget.devicePixelRatioF()
    assert viewport_calls == [
        (0, 0, round(120 * pixel_ratio), round(80 * pixel_ratio))
    ]
    widget.deleteLater()


def test_paint_gl_supersamples_then_resolves_hdr_to_the_qt_framebuffer(
    qapp, monkeypatch
):
    monkeypatch.setattr(dbh.GL, "glUniform4f", lambda *args: None)
    widget = BlackHoleGLWidget()
    widget.resize(200, 100)
    widget._scene_program = 7
    widget._vertex_array = 8
    widget._scene_uniform_locations = {
        name: name for name in widget._SCENE_UNIFORM_NAMES
    }
    uniform_vectors = {}
    uniform_scalars = {}
    draw_calls = []
    viewport_calls = []
    framebuffer_binds = []
    blit_calls = []
    monkeypatch.setattr(widget._postprocess, "render", lambda *args: blit_calls.append(args))

    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glClear", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDisable", lambda *args: None)
    monkeypatch.setattr(widget, "defaultFramebufferObject", lambda: 41)
    monkeypatch.setattr(dbh.GL, "glGenFramebuffers", lambda count: 71)
    monkeypatch.setattr(dbh.GL, "glGenRenderbuffers", lambda count: 72)
    monkeypatch.setattr(
        dbh.GL, "glBindFramebuffer", lambda *args: framebuffer_binds.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glBindRenderbuffer", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glRenderbufferStorage", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glFramebufferRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glCheckFramebufferStatus",
        lambda *args: dbh.GL.GL_FRAMEBUFFER_COMPLETE,
    )
    monkeypatch.setattr(
        dbh.GL, "glViewport", lambda *args: viewport_calls.append(args)
    )
    monkeypatch.setattr(
        dbh.GL, "glBlitFramebuffer", lambda *args: blit_calls.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glUseProgram", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glUniform2f",
        lambda location, x, y: uniform_vectors.__setitem__(location, (x, y)),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glUniform1f",
        lambda location, value: uniform_scalars.__setitem__(location, value),
    )
    monkeypatch.setattr(dbh.GL, "glBindVertexArray", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glDrawArrays",
        lambda *args: draw_calls.append(args),
    )

    widget.set_mouse_state(QPointF(50.0, 25.0))
    widget.set_drag_target(True)
    widget._last_frame_elapsed_ms = widget._elapsed_timer.elapsed() - 100
    widget.paintGL()

    pixel_ratio = widget.devicePixelRatioF()
    pixel_width = round(200 * pixel_ratio)
    pixel_height = round(100 * pixel_ratio)
    target_width = pixel_width * 2
    target_height = pixel_height * 2
    assert uniform_vectors["u_resolution"] == (target_width, target_height)
    assert uniform_scalars["u_quality"] == 1.0
    assert uniform_scalars["u_debug_view"] == 0.0
    assert uniform_scalars["u_time"] >= 0.0
    assert draw_calls == [(dbh.GL.GL_TRIANGLES, 0, 3)]
    assert (0, 0, target_width, target_height) in viewport_calls
    assert viewport_calls[-1] == (0, 0, pixel_width, pixel_height)
    assert framebuffer_binds[-1] == (dbh.GL.GL_FRAMEBUFFER, 41)
    assert blit_calls == [
        (
            71,
            (target_width, target_height),
            41,
            (pixel_width, pixel_height),
            uniform_scalars["u_time"],
            0,
        )
    ]
    widget._scene_program = 0
    widget._vertex_array = 0
    widget._supersample_framebuffer = 0
    widget._supersample_renderbuffer = 0
    widget.deleteLater()


def test_paint_gl_reallocates_supersample_target_after_runtime_dpr_change(
    qapp, monkeypatch
):
    monkeypatch.setattr(dbh.GL, "glUniform4f", lambda *args: None)
    class DprProbeWidget(BlackHoleGLWidget):
        test_dpr = 1.0

        def devicePixelRatioF(self):
            return self.test_dpr

    widget = DprProbeWidget()
    widget.resize(100, 50)
    widget._scene_program = 7
    widget._vertex_array = 8
    widget._scene_uniform_locations = {
        name: name for name in widget._SCENE_UNIFORM_NAMES
    }
    generated_framebuffers = iter((71, 73))
    generated_renderbuffers = iter((72, 74))
    storage_calls = []
    deleted_framebuffers = []
    deleted_renderbuffers = []
    resolution_uploads = []
    monkeypatch.setattr(widget._postprocess, "render", lambda *args: None)

    monkeypatch.setattr(widget, "defaultFramebufferObject", lambda: 41)
    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glClear", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDisable", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL, "glGenFramebuffers", lambda count: next(generated_framebuffers)
    )
    monkeypatch.setattr(
        dbh.GL, "glGenRenderbuffers", lambda count: next(generated_renderbuffers)
    )
    monkeypatch.setattr(dbh.GL, "glBindFramebuffer", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBindRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL, "glRenderbufferStorage", lambda *args: storage_calls.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glFramebufferRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glCheckFramebufferStatus",
        lambda *args: dbh.GL.GL_FRAMEBUFFER_COMPLETE,
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteFramebuffers",
        lambda count, handles: deleted_framebuffers.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteRenderbuffers",
        lambda count, handles: deleted_renderbuffers.extend(handles),
    )
    monkeypatch.setattr(dbh.GL, "glViewport", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glUseProgram", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glUniform2f",
        lambda location, x, y: resolution_uploads.append((location, x, y)),
    )
    monkeypatch.setattr(dbh.GL, "glUniform1f", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBindVertexArray", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDrawArrays", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBlitFramebuffer", lambda *args: None)

    widget.paintGL()
    widget.test_dpr = 2.0
    widget.paintGL()

    assert storage_calls == [
        (dbh.GL.GL_RENDERBUFFER, dbh.GL.GL_RGBA16F, 200, 100),
        (dbh.GL.GL_RENDERBUFFER, dbh.GL.GL_RGBA16F, 400, 200),
    ]
    assert deleted_framebuffers == [71]
    assert deleted_renderbuffers == [72]
    assert widget._supersample_size == (400, 200)
    assert ("u_resolution", 200.0, 100.0) in resolution_uploads
    assert ("u_resolution", 400.0, 200.0) in resolution_uploads
    widget._scene_program = 0
    widget._vertex_array = 0
    widget._supersample_framebuffer = 0
    widget._supersample_renderbuffer = 0
    widget.deleteLater()


def test_supersample_target_reuses_exact_physical_size_then_replaces_stale_handles(
    qapp, monkeypatch
):
    widget = BlackHoleGLWidget()
    generated_framebuffers = iter((71, 73))
    generated_renderbuffers = iter((72, 74))
    storage_calls = []
    deleted_framebuffers = []
    deleted_renderbuffers = []

    monkeypatch.setattr(
        dbh.GL, "glGenFramebuffers", lambda count: next(generated_framebuffers)
    )
    monkeypatch.setattr(
        dbh.GL, "glGenRenderbuffers", lambda count: next(generated_renderbuffers)
    )
    monkeypatch.setattr(dbh.GL, "glBindFramebuffer", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBindRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL, "glRenderbufferStorage", lambda *args: storage_calls.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glFramebufferRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glCheckFramebufferStatus",
        lambda *args: dbh.GL.GL_FRAMEBUFFER_COMPLETE,
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteFramebuffers",
        lambda count, handles: deleted_framebuffers.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteRenderbuffers",
        lambda count, handles: deleted_renderbuffers.extend(handles),
    )

    widget._ensure_supersample_target(200, 100, 41)
    widget._ensure_supersample_target(200, 100, 41)
    widget._ensure_supersample_target(300, 200, 41)

    assert storage_calls == [
        (dbh.GL.GL_RENDERBUFFER, dbh.GL.GL_RGBA16F, 400, 200),
        (dbh.GL.GL_RENDERBUFFER, dbh.GL.GL_RGBA16F, 600, 400),
    ]
    assert deleted_framebuffers == [71]
    assert deleted_renderbuffers == [72]
    assert widget._supersample_framebuffer == 73
    assert widget._supersample_renderbuffer == 74
    assert widget._supersample_size == (600, 400)
    widget._supersample_framebuffer = 0
    widget._supersample_renderbuffer = 0
    widget.deleteLater()


def test_incomplete_supersample_target_cleans_partial_handles_restores_qt_state_and_reports(
    qapp, monkeypatch
):
    widget = BlackHoleGLWidget()
    widget.resize(200, 100)
    widget._scene_program = 7
    widget._vertex_array = 8
    widget._scene_uniform_locations = {
        name: name for name in widget._SCENE_UNIFORM_NAMES
    }
    errors = []
    restore_requests = []
    deleted_framebuffers = []
    deleted_renderbuffers = []
    framebuffer_binds = []
    viewport_calls = []

    widget.fatal_error.connect(errors.append)
    widget.system_cursor_restore_requested.connect(
        lambda: restore_requests.append(True)
    )
    monkeypatch.setattr(widget, "defaultFramebufferObject", lambda: 41)
    monkeypatch.setattr(dbh.GL, "glGenFramebuffers", lambda count: 71)
    monkeypatch.setattr(dbh.GL, "glGenRenderbuffers", lambda count: 72)
    monkeypatch.setattr(
        dbh.GL, "glBindFramebuffer", lambda *args: framebuffer_binds.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glBindRenderbuffer", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glRenderbufferStorage", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glFramebufferRenderbuffer", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glCheckFramebufferStatus",
        lambda *args: dbh.GL.GL_FRAMEBUFFER_INCOMPLETE_ATTACHMENT,
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteFramebuffers",
        lambda count, handles: deleted_framebuffers.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteRenderbuffers",
        lambda count, handles: deleted_renderbuffers.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL, "glViewport", lambda *args: viewport_calls.append(args)
    )
    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glClear", lambda *args: None)

    widget.paintGL()
    widget.paintGL()
    qapp.processEvents()

    pixel_ratio = widget.devicePixelRatioF()
    assert deleted_framebuffers == [71]
    assert deleted_renderbuffers == [72]
    assert widget._supersample_framebuffer == 0
    assert widget._supersample_renderbuffer == 0
    assert widget._supersample_size == (0, 0)
    assert framebuffer_binds[-1] == (dbh.GL.GL_FRAMEBUFFER, 41)
    assert viewport_calls[-1] == (
        0,
        0,
        round(200 * pixel_ratio),
        round(100 * pixel_ratio),
    )
    assert errors == [
        "OpenGL rendering failed: supersample framebuffer is incomplete (0x8cd6)"
    ]
    assert restore_requests == [True]
    widget._scene_program = 0
    widget._vertex_array = 0
    widget.deleteLater()


class _SignalProbe:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self):
        for slot in tuple(self._slots):
            slot()


class _ContextProbe:
    def __init__(self):
        self.aboutToBeDestroyed = _SignalProbe()
        self._format = configure_surface_format()

    def format(self):
        return self._format

    def isValid(self):
        return True


def _patch_gl_initialization(
    monkeypatch,
    program_ids,
    vertex_array_ids,
    texture_ids=None,
):
    if texture_ids is None:
        texture_ids = iter((901, 902, 903, 904))
    monkeypatch.setattr(dbh, "compileShader", lambda *args: 1)
    monkeypatch.setattr(dbh, "compileProgram", lambda *args: next(program_ids))
    monkeypatch.setattr(dbh.GL, "glGenVertexArrays", lambda count: next(vertex_array_ids))
    monkeypatch.setattr(dbh.GL, "glGenTextures", lambda count: next(texture_ids))
    monkeypatch.setattr(dbh.GL, "glBindVertexArray", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glGetUniformLocation", lambda *args: 0)
    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDisable", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glEnable", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBlendFunc", lambda *args: None)


@pytest.mark.parametrize("failure_stage", ("compile", "texture", "uniform"))
def test_cursor_pipeline_initialization_failure_is_soft_and_releases_partials(
    qapp,
    monkeypatch,
    failure_stage,
):
    widget = BlackHoleGLWidget()
    context = _ContextProbe()
    program_calls = 0
    deleted_programs = []
    deleted_textures = []
    pipeline_errors = []
    fatal_errors = []
    restore_requests = []

    def compile_program(*args):
        nonlocal program_calls
        program_calls += 1
        if program_calls == 1:
            return 101
        if failure_stage == "compile":
            raise RuntimeError("cursor compile exploded")
        return 111

    monkeypatch.setattr(widget, "context", lambda: context)
    monkeypatch.setattr(dbh, "compileShader", lambda *args: 1)
    monkeypatch.setattr(dbh, "compileProgram", compile_program)
    monkeypatch.setattr(dbh.GL, "glGenVertexArrays", lambda count: 201)
    monkeypatch.setattr(dbh.GL, "glBindVertexArray", lambda *args: None)
    monkeypatch.setattr(
        dbh.GL,
        "glGenTextures",
        lambda count: 0 if failure_stage == "texture" else 501,
    )
    monkeypatch.setattr(
        dbh.GL,
        "glGetUniformLocation",
        lambda program, name: (
            -1
            if failure_stage == "uniform"
            and program == 111
            and name == "u_cursor_texture"
            else 0
        ),
    )
    monkeypatch.setattr(dbh.GL, "glClearColor", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDisable", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glEnable", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glBlendFunc", lambda *args: None)
    monkeypatch.setattr(dbh.GL, "glDeleteProgram", deleted_programs.append)
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteTextures",
        lambda count, handles: deleted_textures.extend(handles),
    )
    widget.cursor_pipeline_failed.connect(pipeline_errors.append)
    widget.fatal_error.connect(fatal_errors.append)
    widget.system_cursor_restore_requested.connect(
        lambda: restore_requests.append(True)
    )

    widget.initializeGL()
    qapp.processEvents()

    assert widget._gl_initialized is True
    assert widget._scene_program == 101
    assert widget._vertex_array == 201
    assert widget.cursor_pipeline_ready is False
    assert widget._cursor_program == 0
    assert widget._cursor_texture == 0
    assert len(pipeline_errors) == 1
    assert failure_stage in pipeline_errors[0]
    assert fatal_errors == []
    assert restore_requests == []
    if failure_stage == "compile":
        assert deleted_programs == []
        assert deleted_textures == []
    elif failure_stage == "texture":
        assert deleted_programs == [111]
        assert deleted_textures == []
    else:
        assert deleted_programs == [111]
        assert deleted_textures == [501]
    widget._reset_gl_handles()
    widget.deleteLater()


def test_scene_initialization_failure_restores_synchronously_and_releases_program(
    qapp,
    monkeypatch,
):
    widget = BlackHoleGLWidget()
    context = _ContextProbe()
    deleted_programs = []
    restore_requests = []
    fatal_errors = []
    monkeypatch.setattr(widget, "context", lambda: context)
    monkeypatch.setattr(dbh, "compileShader", lambda *args: 1)
    monkeypatch.setattr(dbh, "compileProgram", lambda *args: 101)
    monkeypatch.setattr(
        dbh.GL,
        "glGenVertexArrays",
        lambda count: (_ for _ in ()).throw(RuntimeError("VAO exploded")),
    )
    monkeypatch.setattr(dbh.GL, "glDeleteProgram", deleted_programs.append)
    widget.system_cursor_restore_requested.connect(
        lambda: restore_requests.append(True)
    )
    widget.fatal_error.connect(fatal_errors.append)

    widget.initializeGL()

    assert restore_requests == [True]
    assert fatal_errors == []
    assert deleted_programs == [101]
    assert widget._scene_program == 0
    assert widget._vertex_array == 0
    qapp.processEvents()
    assert fatal_errors == ["OpenGL initialization failed: VAO exploded"]
    widget.deleteLater()


def test_each_recreated_context_connects_cleanup_and_deletes_its_gl_objects(
    qapp, monkeypatch
):
    widget = BlackHoleGLWidget()
    first_context = _ContextProbe()
    second_context = _ContextProbe()
    current = {"context": first_context}
    deleted_programs = []
    deleted_vertex_arrays = []
    deleted_textures = []
    deleted_framebuffers = []
    deleted_renderbuffers = []

    class CurrentContextProbe:
        @staticmethod
        def currentContext():
            return current["context"]

    monkeypatch.setattr(dbh, "QOpenGLContext", CurrentContextProbe)
    monkeypatch.setattr(widget, "context", lambda: current["context"])
    monkeypatch.setattr(widget, "makeCurrent", lambda: None)
    monkeypatch.setattr(widget, "doneCurrent", lambda: None)
    monkeypatch.setattr(dbh.GL, "glDeleteProgram", deleted_programs.append)
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteVertexArrays",
        lambda count, arrays: deleted_vertex_arrays.extend(arrays),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteTextures",
        lambda count, handles: deleted_textures.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteFramebuffers",
        lambda count, handles: deleted_framebuffers.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteRenderbuffers",
        lambda count, handles: deleted_renderbuffers.extend(handles),
    )
    _patch_gl_initialization(
        monkeypatch,
        iter((101, 111, 102, 112)),
        iter((201, 202)),
        iter((501, 502)),
    )

    widget.initializeGL()
    widget._supersample_framebuffer = 301
    widget._supersample_renderbuffer = 401
    widget._supersample_size = (400, 200)
    first_context.aboutToBeDestroyed.emit()
    current["context"] = second_context
    widget.initializeGL()
    widget._supersample_framebuffer = 302
    widget._supersample_renderbuffer = 402
    widget._supersample_size = (600, 400)

    assert len(second_context.aboutToBeDestroyed._slots) == 1
    second_context.aboutToBeDestroyed.emit()
    assert deleted_programs == [101, 111, 102, 112]
    assert deleted_vertex_arrays == [201, 202]
    assert deleted_textures == [501, 502]
    assert deleted_framebuffers == [301, 302]
    assert deleted_renderbuffers == [401, 402]
    assert widget._supersample_framebuffer == 0
    assert widget._supersample_renderbuffer == 0
    assert widget._supersample_size == (0, 0)
    widget.deleteLater()


def test_cleanup_with_wrong_current_context_resets_without_deleting_gl_handles(
    qapp, monkeypatch
):
    widget = BlackHoleGLWidget()
    owned_context = _ContextProbe()
    wrong_context = _ContextProbe()
    widget._scene_program = 101
    widget._cursor_program = 102
    widget._vertex_array = 201
    widget._cursor_texture = 501
    widget._cursor_pipeline_ready = True
    widget._supersample_framebuffer = 301
    widget._supersample_renderbuffer = 401
    widget._supersample_size = (400, 200)
    widget._resource_context = owned_context
    widget._cleanup_context = owned_context
    deleted = []
    make_current_calls = []
    done_current_calls = []

    class WrongCurrentContextProbe:
        @staticmethod
        def currentContext():
            return wrong_context

    monkeypatch.setattr(dbh, "QOpenGLContext", WrongCurrentContextProbe)
    monkeypatch.setattr(widget, "context", lambda: owned_context)
    monkeypatch.setattr(
        widget, "makeCurrent", lambda: make_current_calls.append(True)
    )
    monkeypatch.setattr(
        widget, "doneCurrent", lambda: done_current_calls.append(True)
    )
    monkeypatch.setattr(
        dbh.GL, "glDeleteProgram", lambda handle: deleted.append(handle)
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteVertexArrays",
        lambda count, handles: deleted.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteFramebuffers",
        lambda count, handles: deleted.extend(handles),
    )
    monkeypatch.setattr(
        dbh.GL,
        "glDeleteRenderbuffers",
        lambda count, handles: deleted.extend(handles),
    )

    widget._cleanup_gl()

    assert make_current_calls == [True]
    assert done_current_calls == []
    assert deleted == []
    assert widget._scene_program == 0
    assert widget._cursor_program == 0
    assert widget._vertex_array == 0
    assert widget._cursor_texture == 0
    assert widget.cursor_pipeline_ready is False
    assert widget._supersample_framebuffer == 0
    assert widget._supersample_renderbuffer == 0
    assert widget._supersample_size == (0, 0)
    assert widget._resource_context is None
    assert widget._cleanup_context is None
    widget.deleteLater()


def test_initialize_gl_queues_readable_compiler_errors(qapp, monkeypatch):
    widget = BlackHoleGLWidget()
    context = _ContextProbe()
    errors = []
    restore_requests = []
    monkeypatch.setattr(widget, "context", lambda: context)
    monkeypatch.setattr(
        dbh,
        "compileShader",
        lambda *args: (_ for _ in ()).throw(RuntimeError("compiler log: bad token")),
    )
    widget.fatal_error.connect(errors.append)
    widget.system_cursor_restore_requested.connect(
        lambda: restore_requests.append(True)
    )

    widget.initializeGL()

    assert errors == []
    assert restore_requests == [True]
    qapp.processEvents()
    assert errors == ["OpenGL initialization failed: compiler log: bad token"]
    widget.deleteLater()


def test_real_gl_widget_initializes_resizes_and_cleans_up():
    probe = r'''
from OpenGL import GL
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from desktop_black_hole import BlackHoleGLWidget, LensGeometry
from windows_cursor import CursorSnapshot

app = QApplication([])
widget = BlackHoleGLWidget()
assert widget.format().samples() == 0
errors = []
widget.fatal_error.connect(errors.append)
widget.setAttribute(Qt.WA_DontShowOnScreen, True)
widget.resize(120, 80)
widget.show()
for _ in range(20):
    app.processEvents()
    if widget.isValid():
        break
assert widget.isValid(), errors
assert widget._scene_program > 0
assert widget._cursor_program > 0
assert widget._vertex_array > 0
assert widget._cursor_texture > 0
assert widget.cursor_pipeline_ready
assert all(location >= 0 for location in widget._scene_uniform_locations.values())
assert all(location >= 0 for location in widget._cursor_uniform_locations.values())

widget.resize(200, 100)
for _ in range(20):
    app.processEvents()
pixel_ratio = widget.devicePixelRatioF()
pixel_width = round(200 * pixel_ratio)
pixel_height = round(100 * pixel_ratio)
assert widget._supersample_framebuffer > 0
assert widget._supersample_renderbuffer > 0
assert widget._supersample_size == (pixel_width * 2, pixel_height * 2)
assert len(widget._postprocess.targets) == 5
assert widget._postprocess.blur_program > 0
assert widget._postprocess.resolve_program > 0
frame = widget.grabFramebuffer()
assert (frame.width(), frame.height()) == (pixel_width, pixel_height)
corner_alpha = (
    frame.pixelColor(0, 0).alpha(),
    frame.pixelColor(frame.width() - 1, 0).alpha(),
    frame.pixelColor(0, frame.height() - 1).alpha(),
    frame.pixelColor(frame.width() - 1, frame.height() - 1).alpha(),
)
assert corner_alpha == (0, 0, 0, 0)
# At this deliberately tiny non-3:2 size the central pixel touches the thin
# foreground disk's filtered footprint. Probe opaque shadow on both sides.
for direction in (-1, 1):
    core = frame.pixelColor(frame.width() // 2,
        frame.height() // 2 + direction * max(3, round(frame.height() * 0.08)))
    assert core.alpha() >= 250
    assert max(core.red(), core.green(), core.blue()) <= 4
assert frame.format() == QImage.Format_ARGB32_Premultiplied
pixel_bytes = bytes(frame.constBits())
stride = frame.bytesPerLine()
for y in range(frame.height()):
    for x in range(frame.width()):
        offset = y * stride + x * 4
        blue, green, red, alpha = pixel_bytes[offset:offset + 4]
        assert max(red, green, blue) <= alpha

ready = []
proxy_errors = []
widget.cursor_texture_ready.connect(ready.append)
widget.cursor_proxy_failed.connect(
    lambda request_id, message: proxy_errors.append((request_id, message))
)
snapshot = CursorSnapshot(
    source_handle=77,
    width=8,
    height=8,
    hotspot_x=4,
    hotspot_y=4,
    pixels_bgra=bytes((255, 255, 255, 255)) * 64,
)
geometry = LensGeometry.from_physical_size(pixel_width, pixel_height)
pointer = (geometry.center_x + geometry.radius, geometry.center_y)
widget.arm_cursor_proxy(77, snapshot, pointer, geometry)
for _ in range(20):
    app.processEvents()
    if ready:
        break
assert ready == [77]
assert proxy_errors == []
assert widget.activate_cursor_proxy(77)
widget.update()
for _ in range(10):
    app.processEvents()
assert widget._active_cursor_request_id == 77
assert proxy_errors == []
widget.grabFramebuffer()
widget.makeCurrent()
framebuffer = int(GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING))
viewport = tuple(int(value) for value in GL.glGetIntegerv(GL.GL_VIEWPORT))
assert framebuffer == widget.defaultFramebufferObject()
assert int(GL.glGetIntegerv(GL.GL_SAMPLE_BUFFERS)) == 0
assert int(GL.glGetIntegerv(GL.GL_SAMPLES)) == 0
assert viewport == (0, 0, pixel_width, pixel_height)
GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, widget._supersample_framebuffer)
assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE
from array import array
hdr = array("f", bytes(GL.glReadPixels(
    0, 0, pixel_width * 2, pixel_height * 2, GL.GL_RGBA, GL.GL_FLOAT,
)))
assert max(hdr) > 1.0, "scene radiance must survive above display white"
assert all(0.0 <= alpha <= 1.0 for alpha in hdr[3::4])
GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, widget.defaultFramebufferObject())
error = GL.glGetError()
widget.doneCurrent()
assert error == GL.GL_NO_ERROR

old_postprocess = widget._postprocess
widget._cleanup_gl()
assert old_postprocess.targets == []
assert old_postprocess.blur_program == 0
assert old_postprocess.resolve_program == 0
assert widget._scene_program == 0
assert widget._cursor_program == 0
assert widget._vertex_array == 0
assert widget._cursor_texture == 0
assert widget._supersample_framebuffer == 0
assert widget._supersample_renderbuffer == 0
assert widget._supersample_size == (0, 0)
widget._cleanup_gl()
widget.close()
app.processEvents()
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_gl_widget_initialization_avoids_numpy_handler_warning():
    probe = r'''
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from desktop_black_hole import BlackHoleGLWidget

app = QApplication([])
widget = BlackHoleGLWidget()
errors = []
widget.fatal_error.connect(errors.append)
widget.setAttribute(Qt.WA_DontShowOnScreen, True)
widget.resize(120, 80)
widget.show()
for _ in range(20):
    app.processEvents()
    if widget.isValid() and widget._gl_initialized:
        break
assert widget.isValid(), errors
assert widget._gl_initialized, errors
assert widget._vertex_array > 0
widget.close()
app.processEvents()
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OpenGL.arrays.numpymodule.NumpyHandler" not in result.stderr


def test_real_opengl_context_compiles_and_links_shader_program():
    probe = r'''
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader
from desktop_black_hole import FRAGMENT_SHADER_SOURCE, VERTEX_SHADER_SOURCE

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
    compileShader(VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
    compileShader(FRAGMENT_SHADER_SOURCE, GL.GL_FRAGMENT_SHADER),
)
assert program > 0
GL.glDeleteProgram(program)
context.doneCurrent()
'''
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr












def _peak_alpha_radius(frame):
    radial_bins = {}
    center_x = frame.width * 0.5
    center_y = frame.height * 0.5
    for y in range(frame.height):
        for x in range(frame.width):
            alpha = frame.pixels[(y * frame.width + x) * 4 + 3]
            radius = round(math.hypot(x + 0.5 - center_x, y + 0.5 - center_y) * 2) / 2
            total, count = radial_bins.get(radius, (0, 0))
            radial_bins[radius] = (total + alpha, count + 1)
    return max(radial_bins, key=lambda radius: radial_bins[radius][0] / radial_bins[radius][1])

























def test_real_render_preserves_the_near_disk_across_the_horizon():
    width, height, pixels = _render_shader()
    bright_center_columns = {
        x
        for y in range(35, 45)
        for x in range(42, 78)
        if _pixel(pixels, width, x, y)[3] >= 48
        and _pixel_luminance(_pixel(pixels, width, x, y)) >= 70
    }

    assert len(bright_center_columns) >= 12
    assert min(bright_center_columns) <= 52
    assert max(bright_center_columns) >= 68
    center = _pixel(pixels, width, width // 2, height // 2)
    assert center[3] >= 250
    assert _pixel_luminance(center) <= 3


def test_real_render_has_balanced_upper_and_lower_lensed_disk_arcs():
    width, height, pixels = _render_shader()

    def count_bright(x0, x1, y0, y1):
        return sum(
            _pixel(pixels, width, x, y)[3] >= 40
            and _pixel_luminance(_pixel(pixels, width, x, y)) >= 70
            for y in range(y0, y1)
            for x in range(x0, x1)
        )

    upper_arc = count_bright(24, 96, 46, 73)
    lower_arc = count_bright(24, 96, 7, 34)
    assert upper_arc >= 170
    assert lower_arc >= 170
    # The seven-degree camera and positive roll move energy out of the old
    # horizontal boxes; both sides remain substantial while the fixed-frame
    # continuity metric above owns the precise raised-arc geometry.
    assert upper_arc >= lower_arc * 0.25












def test_scaled_size_clamps_and_keeps_three_to_two_ratio():
    assert scaled_size(100).toTuple() == (240, 160)
    assert scaled_size(450).toTuple() == (450, 300)
    assert scaled_size(900).toTuple() == (600, 400)


def test_clamp_position_recovers_fully_offscreen_window():
    screens = [QRect(0, 0, 1920, 1080)]
    assert clamp_position(QPoint(4000, 3000), scaled_size(360), screens) == QPoint(1560, 840)


def test_clamp_position_recovers_a_one_pixel_screen_overlap():
    screens = [QRect(0, 0, 1920, 1080)]
    assert clamp_position(
        QPoint(1919, 1079), scaled_size(360), screens
    ) == QPoint(1560, 840)


def test_clamp_position_preserves_a_visible_position():
    screens = [QRect(0, 0, 1920, 1080)]
    assert clamp_position(QPoint(100, 120), scaled_size(360), screens) == QPoint(100, 120)


def test_frame_interval_uses_60_fps_for_any_interaction():
    assert frame_interval_ms(False, False, False) == IDLE_INTERVAL_MS
    assert frame_interval_ms(True, False, False) == ACTIVE_INTERVAL_MS
    assert frame_interval_ms(False, True, False) == ACTIVE_INTERVAL_MS
    assert frame_interval_ms(False, False, True) == ACTIVE_INTERVAL_MS


def test_extract_local_paths_filters_remote_urls_and_duplicates_lexically(tmp_path):
    first = tmp_path / "one.txt"
    second = tmp_path / "folder"
    mime = QMimeData()
    mime.setUrls([
        QUrl.fromLocalFile(str(first)),
        QUrl("https://example.com/not-local"),
        QUrl.fromLocalFile(str(first)),
        QUrl.fromLocalFile(str(second)),
    ])
    assert extract_local_paths(mime) == [
        os.path.abspath(os.path.normpath(str(first))),
        os.path.abspath(os.path.normpath(str(second))),
    ]


def test_extract_local_paths_rejects_nul_hosted_and_unc_file_urls():
    mime = QMimeData()
    mime.setUrls([
        QUrl("file:///C:/bad%00name.txt"),
        QUrl("file://server/share/hosted.txt"),
        QUrl("file:////server/share/unc.txt"),
    ])

    assert extract_local_paths(mime) == []


def test_settings_round_trip_and_clamp(tmp_path):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    save_window_state(settings, QPoint(50, 70), scaled_size(450), True)

    assert load_window_state(settings, [QRect(0, 0, 1920, 1080)]) == (
        QPoint(50, 70),
        scaled_size(450),
        True,
    )


def test_settings_recovery_centers_invalid_positions_and_clamps_offscreen_ones(
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    screen = QRect(0, 0, 1920, 1080)
    settings.setValue("window/width", 450)
    settings.setValue("window/position", "not a point")
    settings.sync()

    assert load_window_state(settings, [screen])[:2] == (
        QPoint(735, 390),
        QSize(450, 300),
    )

    settings.setValue("window/position", QPoint(4000, 3000))
    settings.sync()

    assert load_window_state(settings, [screen])[:2] == (
        QPoint(1470, 780),
        QSize(450, 300),
    )


def test_desktop_window_has_required_flags_and_drop_support(
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)

    assert window.windowFlags() & Qt.FramelessWindowHint
    assert window.windowFlags() & Qt.Tool
    assert not window.windowFlags() & Qt.WindowStaysOnTopHint
    assert window.testAttribute(Qt.WA_TranslucentBackground)
    assert window.testAttribute(Qt.WA_NoSystemBackground)
    assert not window.autoFillBackground()
    assert window.acceptDrops()


def test_mouse_events_move_window_and_persist_on_release(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.move(100, 120)
    local = QPointF(20, 30)
    start_global = QPointF(120, 150)

    qapp.sendEvent(
        window,
        QMouseEvent(
            QEvent.MouseButtonPress,
            local,
            start_global,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ),
    )
    qapp.sendEvent(
        window,
        QMouseEvent(
            QEvent.MouseMove,
            QPointF(55, 55),
            QPointF(155, 175),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ),
    )

    assert window.pos() == QPoint(135, 145)
    assert window._moving

    qapp.sendEvent(
        window,
        QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(55, 55),
            QPointF(155, 175),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ),
    )

    assert not window._moving
    assert settings.value("window/position", type=QPoint) == QPoint(135, 145)


def test_wheel_scaling_keeps_center_ratio_and_persists(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.resize(360, 240)
    window.move(100, 120)
    original_center = window.geometry().center()
    event = QWheelEvent(
        QPointF(180, 120),
        QPointF(280, 240),
        QPoint(),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )

    qapp.sendEvent(window, event)

    assert window.size() == QSize(390, 260)
    assert window.geometry().center() == original_center
    assert settings.value("window/width", type=int) == 390


def _send_wheel_event(qapp, window, angle_delta_y):
    event = QWheelEvent(
        QPointF(window.width() / 2, window.height() / 2),
        QPointF(window.geometry().center()),
        QPoint(),
        QPoint(0, angle_delta_y),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    qapp.sendEvent(window, event)


def test_wheel_accumulates_two_partial_deltas_into_one_notch(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.resize(360, 240)

    _send_wheel_event(qapp, window, 60)
    assert window.width() == 360

    _send_wheel_event(qapp, window, 60)
    assert window.width() == 390
    assert settings.value("window/width", type=int) == 390


def test_wheel_preserves_remainders_for_multi_notch_and_negative_deltas(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.resize(360, 240)

    _send_wheel_event(qapp, window, 180)
    assert window.width() == 390
    _send_wheel_event(qapp, window, 60)
    assert window.width() == 420

    _send_wheel_event(qapp, window, 240)
    assert window.width() == 480

    _send_wheel_event(qapp, window, -60)
    assert window.width() == 480
    _send_wheel_event(qapp, window, -60)
    assert window.width() == 450
    assert settings.value("window/width", type=int) == 450


def test_pointer_events_forward_shader_coordinates_and_clear_on_leave(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.resize(360, 240)
    event = QMouseEvent(
        QEvent.MouseMove,
        QPointF(270, 60),
        QPointF(270, 60),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )

    qapp.sendEvent(window, event)

    assert window._mouse_position == QPointF(0.75, 0.5)
    assert window._mouse_uniforms(360, 240) == (270.0, 180.0, 1.0)

    qapp.sendEvent(window, QEvent(QEvent.Leave))

    assert window._mouse_position is None


def test_drag_events_set_pointer_and_60_fps_before_any_mouse_move(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.resize(360, 240)
    local_path = tmp_path / "hover.txt"
    local_path.write_text("hover", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(local_path))])

    enter = QDragEnterEvent(
        QPoint(270, 60),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    qapp.sendEvent(window, enter)

    assert enter.isAccepted()
    assert window._mouse_position == QPointF(0.75, 0.5)
    assert window._mouse_uniforms(360, 240) == (270.0, 180.0, 1.0)
    assert window.frame_interval == ACTIVE_INTERVAL_MS

    move = QDragMoveEvent(
        QPoint(90, 180),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    qapp.sendEvent(window, move)

    assert move.isAccepted()
    assert window._mouse_position == QPointF(-0.75, -0.5)
    assert window._mouse_uniforms(360, 240) == (90.0, 60.0, 1.0)
    assert window.frame_interval == ACTIVE_INTERVAL_MS

    leave = QDragLeaveEvent()
    qapp.sendEvent(window, leave)
    assert window._drag_target == 0.0
    assert window._mouse_position is None
    assert window.frame_interval == IDLE_INTERVAL_MS

    qapp.sendEvent(window, enter)
    drop = QDropEvent(
        QPointF(270, 60),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    qapp.sendEvent(window, drop)
    assert window._drag_target == 0.0
    assert window._mouse_position is None
    assert window.frame_interval == IDLE_INTERVAL_MS


def test_local_file_drag_is_accepted_printed_and_never_mutated(
    qapp,
    desktop_window_factory,
    tmp_path,
    capsys,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    untouched_path = tmp_path / "keep.txt"
    untouched_path.write_text("keep-me", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(untouched_path))])
    copy_or_move = Qt.CopyAction | Qt.MoveAction
    enter = QDragEnterEvent(
        QPoint(10, 10),
        copy_or_move,
        mime,
        Qt.LeftButton,
        Qt.ShiftModifier,
    )

    assert enter.proposedAction() == Qt.MoveAction

    qapp.sendEvent(window, enter)

    assert enter.isAccepted()
    assert enter.dropAction() == Qt.CopyAction
    assert window._drag_target == 1.0

    leave = QDragLeaveEvent()
    qapp.sendEvent(window, leave)
    assert window._drag_target == 0.0

    qapp.sendEvent(window, enter)
    drop = QDropEvent(
        QPointF(10, 10),
        copy_or_move,
        mime,
        Qt.LeftButton,
        Qt.ShiftModifier,
    )

    assert drop.proposedAction() == Qt.MoveAction

    qapp.sendEvent(window, drop)

    assert drop.isAccepted()
    assert drop.dropAction() == Qt.CopyAction
    assert window._drag_target == 0.0
    assert capsys.readouterr().out == f"{untouched_path.resolve()}\n"
    assert untouched_path.read_text(encoding="utf-8") == "keep-me"


def test_move_only_file_drag_is_rejected_without_output_or_mutation(
    qapp,
    desktop_window_factory,
    tmp_path,
    capsys,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    untouched_path = tmp_path / "move-only.txt"
    untouched_path.write_text("do-not-move", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(untouched_path))])
    enter = QDragEnterEvent(
        QPoint(10, 10),
        Qt.MoveAction,
        mime,
        Qt.LeftButton,
        Qt.ShiftModifier,
    )

    qapp.sendEvent(window, enter)

    assert not enter.isAccepted()
    assert window._drag_target == 0.0

    drop = QDropEvent(
        QPointF(10, 10),
        Qt.MoveAction,
        mime,
        Qt.LeftButton,
        Qt.ShiftModifier,
    )
    qapp.sendEvent(window, drop)

    assert not drop.isAccepted()
    assert window._drag_target == 0.0
    assert capsys.readouterr().out == ""
    assert untouched_path.read_text(encoding="utf-8") == "do-not-move"


def test_remote_drag_is_rejected_and_always_clears_drag_target(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.set_drag_target(True)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/remote.txt")])
    enter = QDragEnterEvent(
        QPoint(10, 10),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )

    qapp.sendEvent(window, enter)

    assert not enter.isAccepted()
    assert window._drag_target == 0.0

    window.set_drag_target(True)
    drop = QDropEvent(
        QPointF(10, 10),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.dropEvent(drop)

    assert not drop.isAccepted()
    assert window._drag_target == 0.0


def test_always_on_top_toggle_preserves_geometry_reshows_and_persists(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.setGeometry(100, 120, 450, 300)
    window.show()
    qapp.processEvents()
    geometry = window.geometry()

    window._set_always_on_top(True)
    qapp.processEvents()

    assert window.isVisible()
    assert window.geometry() == geometry
    assert window.windowFlags() & Qt.WindowStaysOnTopHint
    assert settings.value("window/always_on_top", type=bool)


def test_desktop_layer_toggle_preserves_geometry_and_top_level_tool(
    qapp, desktop_window_factory, tmp_path, monkeypatch,
):
    settings = QSettings(str(tmp_path / "layer.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.setGeometry(100, 120, 450, 300)
    window.show()
    qapp.processEvents()
    geometry = QRect(window.geometry())
    restored = []
    monkeypatch.setattr(window, "_restore_system_cursor", lambda: restored.append(True))
    window._set_desktop_layer(True)
    qapp.processEvents()
    assert restored
    assert window.isVisible() and window.geometry() == geometry
    assert window.isWindow() and window.parentWidget() is None
    assert window.windowType() == Qt.Tool
    assert window.windowFlags() & Qt.FramelessWindowHint
    assert window.windowFlags() & Qt.WindowStaysOnBottomHint
    assert not window.windowFlags() & Qt.WindowStaysOnTopHint
    assert window.testAttribute(Qt.WA_TranslucentBackground)
    assert window.acceptDrops()
    assert settings.value("window/desktop_layer", type=bool)
    assert not settings.value("window/always_on_top", type=bool)
    window._set_desktop_layer(False)
    qapp.processEvents()
    assert window.isVisible() and window.geometry() == geometry
    assert not window.windowFlags() & (Qt.WindowStaysOnTopHint | Qt.WindowStaysOnBottomHint)
    assert not settings.value("window/desktop_layer", type=bool)


def test_desktop_layer_and_topmost_are_mutually_exclusive_and_remembered(
    desktop_window_factory, tmp_path,
):
    settings = QSettings(str(tmp_path / "layer.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    assert not window._desktop_layer
    window._set_always_on_top(True)
    window._set_desktop_layer(True)
    assert not window._always_on_top and window._desktop_layer
    restored = desktop_window_factory(settings)
    assert restored._desktop_layer and not restored._always_on_top
    assert restored.windowFlags() & Qt.WindowStaysOnBottomHint
    window._set_always_on_top(True)
    assert window._always_on_top and not window._desktop_layer
    assert not window.windowFlags() & Qt.WindowStaysOnBottomHint
    assert not settings.value("window/desktop_layer", type=bool)
    assert settings.value("window/always_on_top", type=bool)
    window._set_always_on_top(False)
    assert not window.windowFlags() & (Qt.WindowStaysOnTopHint | Qt.WindowStaysOnBottomHint)
    assert not window.isVisible()  # Changing an unshown window never shows it.


def test_conflicting_persisted_layers_prefer_desktop_mode(
    desktop_window_factory, tmp_path,
):
    settings = QSettings(str(tmp_path / "layer.ini"), QSettings.IniFormat)
    settings.setValue("window/desktop_layer", True)
    settings.setValue("window/always_on_top", True)
    window = desktop_window_factory(settings)
    assert window._desktop_layer and not window._always_on_top
    window._persist_window_state()
    assert not settings.value("window/always_on_top", type=bool)


def test_desktop_layer_menu_switches_modes_and_allows_return_to_normal(
    qapp, desktop_window_factory, tmp_path,
):
    settings = QSettings(str(tmp_path / "layer.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    captured = []

    def capture():
        menu = qapp.activePopupWidget()
        captured.extend(menu.actions())
        menu.close()

    QTimer.singleShot(0, capture)
    qapp.sendEvent(window, QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(20, 20), QPoint(120, 140)))
    actions = {action.text(): action for action in captured}
    top = actions["始终置顶"]
    bottom = actions["固定在桌面层（置底）"]
    top.trigger()
    assert top.isChecked() and not bottom.isChecked()
    assert window._always_on_top and not window._desktop_layer
    bottom.trigger()
    assert bottom.isChecked() and not top.isChecked()
    assert window._desktop_layer and not window._always_on_top
    bottom.trigger()
    assert not bottom.isChecked() and not top.isChecked()
    assert not window._desktop_layer and not window._always_on_top


def test_desktop_layer_keeps_resize_and_copy_only_file_drop(
    qapp, desktop_window_factory, tmp_path, capsys,
):
    settings = QSettings(str(tmp_path / "layer.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window._set_desktop_layer(True)
    window.resize(360, 240)
    _send_wheel_event(qapp, window, 120)
    assert window.size() == QSize(390, 260)
    assert window.windowFlags() & Qt.WindowStaysOnBottomHint
    source = tmp_path / "untouched.txt"
    source.write_text("unchanged", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction | Qt.MoveAction,
                      mime, Qt.LeftButton, Qt.NoModifier)
    window.dropEvent(drop)
    assert drop.isAccepted() and drop.dropAction() == Qt.CopyAction
    assert str(source) in capsys.readouterr().out
    assert source.read_text(encoding="utf-8") == "unchanged"


def test_right_click_menu_exposes_all_desktop_actions(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    captured_actions = []

    def capture_and_close_menu():
        menu = qapp.activePopupWidget()
        assert menu is not None
        captured_actions.extend(menu.actions())
        menu.close()

    QTimer.singleShot(0, capture_and_close_menu)
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse,
        QPoint(20, 20),
        QPoint(120, 140),
    )

    qapp.sendEvent(window, event)

    assert [action.text() for action in captured_actions] == [
        "鼠标引力效果",
        "桌面背景扭曲",
        "齐马蓝伴星（状态灯）",
        "Codex：未连接",
        "状态来源",
        "始终置顶",
        "固定在桌面层（置底）",
        "渲染画质",
        "恢复默认大小",
        "在当前屏幕居中",
        "语言 / Language",
        "开机启动（当前用户）",
        "退出",
    ]
    actions = {action.text(): action for action in captured_actions}
    assert actions["鼠标引力效果"].isCheckable()
    assert actions["鼠标引力效果"].isChecked()
    assert actions["桌面背景扭曲"].isCheckable()
    assert not actions["桌面背景扭曲"].isChecked()
    assert actions["始终置顶"].isCheckable()
    assert not actions["始终置顶"].isChecked()
    assert actions["固定在桌面层（置底）"].isCheckable()
    assert not actions["固定在桌面层（置底）"].isChecked()
    quality_actions = actions["渲染画质"].menu().actions()
    assert [action.text() for action in quality_actions] == ["标准", "高清", "电影级"]
    assert [action.isChecked() for action in quality_actions] == [False, True, False]
    assert "空闲：外环慢飞" in actions["齐马蓝伴星（状态灯）"].toolTip()
    assert "尚未安装本机 Codex 状态接入" in actions["Codex：未连接"].toolTip()
    assert actions["Codex：未连接"].isEnabled()


def test_scene_quality_persists_and_debug_shortcuts_leave_window_flags_unchanged(
    qapp, desktop_window_factory, tmp_path,
):
    settings = QSettings(str(tmp_path / "quality.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    original_flags = window.windowFlags()
    assert window.scene_quality == dbh.SCENE_QUALITY_HIGH
    window.set_scene_quality(2)
    assert settings.value("effects/scene_quality", type=int) == 2
    restored = desktop_window_factory(settings)
    assert restored.scene_quality == dbh.SCENE_QUALITY_CINEMATIC
    QTest.keyClick(window, Qt.Key_7)
    assert window.debug_view == 7
    QTest.keyClick(window, Qt.Key_0)
    assert window.debug_view == 0
    assert window.windowFlags() == original_flags
    window.set_scene_quality(-100)
    assert window.scene_quality == dbh.SCENE_QUALITY_STANDARD
    window.set_scene_quality("invalid")
    assert window.scene_quality == dbh.SCENE_QUALITY_HIGH


def test_context_menu_size_and_center_actions_update_geometry_and_settings(
    qapp,
    desktop_window_factory,
    tmp_path,
):
    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.setGeometry(100, 120, 450, 300)
    screen = qapp.primaryScreen().availableGeometry()

    def trigger_actions_and_close_menu():
        menu = qapp.activePopupWidget()
        assert menu is not None
        actions = {action.text(): action for action in menu.actions()}
        actions["恢复默认大小"].trigger()
        actions["在当前屏幕居中"].trigger()
        menu.close()

    QTimer.singleShot(0, trigger_actions_and_close_menu)
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse,
        QPoint(20, 20),
        QPoint(120, 140),
    )

    qapp.sendEvent(window, event)

    expected_position = QPoint(
        screen.left() + (screen.width() - 360) // 2,
        screen.top() + (screen.height() - 240) // 2,
    )
    assert window.size() == QSize(360, 240)
    assert window.pos() == expected_position
    assert settings.value("window/position", type=QPoint) == expected_position
    assert settings.value("window/width", type=int) == 360


def test_close_persists_geometry_stops_timer_and_skips_invalid_gl_cleanup(
    qapp,
    desktop_window_factory,
    tmp_path,
    monkeypatch,
):
    class InvalidContext:
        @staticmethod
        def isValid():
            return False

    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window.move(135, 145)
    window.resize(450, 300)
    window._cleanup_context = InvalidContext()
    cleanup_calls = []
    monkeypatch.setattr(window, "_cleanup_gl", lambda: cleanup_calls.append(True))

    qapp.sendEvent(window, QCloseEvent())

    assert not window._frame_timer.isActive()
    assert cleanup_calls == []
    assert settings.value("window/position", type=QPoint) == QPoint(135, 145)
    assert settings.value("window/width", type=int) == 450


def test_close_invokes_gl_cleanup_for_an_initialized_valid_context(
    qapp,
    desktop_window_factory,
    tmp_path,
    monkeypatch,
):
    class ValidContext:
        @staticmethod
        def isValid():
            return True

    settings = QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)
    window = desktop_window_factory(settings)
    window._cleanup_context = ValidContext()
    cleanup_calls = []
    monkeypatch.setattr(window, "_cleanup_gl", lambda: cleanup_calls.append(True))

    qapp.sendEvent(window, QCloseEvent())

    assert cleanup_calls == [True]
    assert not window._frame_timer.isActive()
