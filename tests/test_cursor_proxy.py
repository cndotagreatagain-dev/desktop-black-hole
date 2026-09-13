from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF

from black_hole_shaders import (
    CURSOR_DISTORTION_HALF_WIDTH_RATIO,
    CURSOR_INTERACTION_INNER_RATIO,
    CURSOR_INTERACTION_OUTER_RATIO,
    PROJECTED_LENS_RADIUS,
)
from desktop_black_hole import (
    CursorProxyController,
    LensGeometry,
    ProxyCursorState,
    ProxyTransition,
    cursor_candidate_intersects_annulus,
    cursor_rect_intersects_annulus,
    qt_position_to_gl_pixels,
    should_proxy_cursor,
)
from windows_cursor import CursorSnapshot


def _snapshot(
    *,
    width: int = 16,
    height: int = 16,
    hotspot_x: int = 0,
    hotspot_y: int = 0,
) -> CursorSnapshot:
    return CursorSnapshot(
        source_handle=1,
        width=width,
        height=height,
        hotspot_x=hotspot_x,
        hotspot_y=hotspot_y,
        pixels_bgra=bytes(width * height * 4),
    )


def _geometry() -> LensGeometry:
    return LensGeometry.from_physical_size(400, 300)


def test_controller_requires_armed_matching_generation_before_hiding() -> None:
    controller = CursorProxyController()

    assert controller.state is ProxyCursorState.SYSTEM_VISIBLE
    assert controller.request_id == 0

    stale = controller.upload_succeeded(0)
    assert stale == ProxyTransition(request_id=0)
    assert controller.state is ProxyCursorState.SYSTEM_VISIBLE

    armed = controller.arm()
    assert armed == ProxyTransition(request_id=1)
    assert controller.state is ProxyCursorState.PROXY_ARMED

    ready = controller.upload_succeeded(armed.request_id)
    assert ready == ProxyTransition(request_id=1, hide_system=True)
    assert controller.state is ProxyCursorState.PROXY_VISIBLE


def test_stale_generation_is_neutral_and_can_never_hide() -> None:
    controller = CursorProxyController()
    first = controller.arm()
    second = controller.arm()

    transition = controller.upload_succeeded(first.request_id)

    assert transition == ProxyTransition(request_id=second.request_id)
    assert not transition.hide_system
    assert not transition.restore_system
    assert controller.state is ProxyCursorState.PROXY_ARMED


def test_deactivate_always_invalidates_and_restores_visible_proxy_once() -> None:
    controller = CursorProxyController()
    armed = controller.arm()
    controller.upload_succeeded(armed.request_id)

    restored = controller.deactivate()
    repeated = controller.deactivate()

    assert restored == ProxyTransition(request_id=2, restore_system=True)
    assert repeated == ProxyTransition(request_id=3)
    assert controller.request_id == 3
    assert controller.state is ProxyCursorState.SYSTEM_VISIBLE


def test_deactivate_invalidates_pending_work_without_restoration() -> None:
    controller = CursorProxyController()
    pending = controller.arm()

    transition = controller.deactivate()

    assert transition == ProxyTransition(request_id=pending.request_id + 1)
    assert not transition.restore_system
    assert controller.state is ProxyCursorState.SYSTEM_VISIBLE


def test_visible_proxy_must_be_deactivated_before_rearming() -> None:
    controller = CursorProxyController()
    armed = controller.arm()
    controller.upload_succeeded(armed.request_id)

    with pytest.raises(RuntimeError, match="deactivate"):
        controller.arm()

    assert controller.request_id == armed.request_id
    assert controller.state is ProxyCursorState.PROXY_VISIBLE


def test_matching_ready_after_eligibility_loss_remains_armed() -> None:
    controller = CursorProxyController()
    armed = controller.arm()

    transition = controller.upload_succeeded(
        armed.request_id,
        still_eligible=False,
    )

    assert transition == ProxyTransition(request_id=armed.request_id)
    assert controller.state is ProxyCursorState.PROXY_ARMED


def test_upload_failure_only_disarms_matching_armed_request() -> None:
    controller = CursorProxyController()
    first = controller.arm()
    current = controller.arm()

    stale = controller.upload_failed(first.request_id)
    failed = controller.upload_failed(current.request_id)

    assert stale == ProxyTransition(request_id=current.request_id)
    assert controller.state is ProxyCursorState.SYSTEM_VISIBLE
    assert failed == ProxyTransition(request_id=current.request_id)
    assert not failed.hide_system
    assert not failed.restore_system


@pytest.mark.parametrize(
    "disabled_gate",
    [
        "enabled",
        "pointer_inside",
        "intersects_lens",
        "gl_pipeline_ready",
    ],
)
def test_should_proxy_cursor_requires_every_positive_gate(disabled_gate: str) -> None:
    gates = {
        "enabled": True,
        "pointer_inside": True,
        "intersects_lens": True,
        "gl_pipeline_ready": True,
        "file_drag_active": False,
        "moving_window": False,
        "menu_open": False,
        "closing": False,
    }
    assert should_proxy_cursor(**gates)

    gates[disabled_gate] = False
    assert not should_proxy_cursor(**gates)


@pytest.mark.parametrize(
    "blocking_gate",
    ["file_drag_active", "moving_window", "menu_open", "closing"],
)
def test_should_proxy_cursor_rejects_each_blocking_gate(blocking_gate: str) -> None:
    gates = {
        "enabled": True,
        "pointer_inside": True,
        "intersects_lens": True,
        "gl_pipeline_ready": True,
        "file_drag_active": False,
        "moving_window": False,
        "menu_open": False,
        "closing": False,
    }
    gates[blocking_gate] = True

    assert not should_proxy_cursor(**gates)


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_qt_position_converts_to_bottom_left_rounded_physical_pixels(
    dpr: float,
) -> None:
    logical_width = 361.0
    logical_height = 241.0
    position = QPointF(72.2, 96.4)
    physical_width = round(logical_width * dpr)
    physical_height = round(logical_height * dpr)

    actual = qt_position_to_gl_pixels(
        position,
        logical_width,
        logical_height,
        dpr,
    )

    assert actual == pytest.approx(
        (
            position.x() * physical_width / logical_width,
            (logical_height - position.y()) * physical_height / logical_height,
        )
    )


def test_qt_position_conversion_does_not_clamp_outside_pointer() -> None:
    assert qt_position_to_gl_pixels(QPointF(-3.0, 105.0), 100, 100, 1.25) == (
        -3.75,
        -6.25,
    )


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_lens_geometry_matches_shader_coordinate_contract(dpr: float) -> None:
    logical_width = 361.0
    logical_height = 241.0
    physical_width = round(logical_width * dpr)
    physical_height = round(logical_height * dpr)

    geometry = LensGeometry.from_logical_size(
        logical_width,
        logical_height,
        dpr,
    )

    assert geometry.center_x == physical_width * 0.5
    assert geometry.center_y == physical_height * 0.5
    assert geometry.radius == PROJECTED_LENS_RADIUS * physical_height * 0.5
    assert geometry.interaction_inner_radius == pytest.approx(
        geometry.radius * CURSOR_INTERACTION_INNER_RATIO
    )
    assert geometry.interaction_outer_radius == pytest.approx(
        geometry.radius * CURSOR_INTERACTION_OUTER_RATIO
    )
    assert geometry.distortion_half_width == pytest.approx(
        geometry.radius * CURSOR_DISTORTION_HALF_WIDTH_RATIO
    )
    shader_center = (
        (2.0 * geometry.center_x - physical_width) / physical_height,
        (2.0 * geometry.center_y - physical_height) / physical_height,
    )
    shader_ring_x = (
        2.0 * (geometry.center_x + geometry.radius) - physical_width
    ) / physical_height
    assert shader_center == pytest.approx((0.0, 0.0))
    assert shader_ring_x == pytest.approx(PROJECTED_LENS_RADIUS)


def test_exact_rectangle_intersects_when_edge_crosses_annulus() -> None:
    geometry = _geometry()
    snapshot = _snapshot(width=20, height=20)
    pointer = (
        geometry.center_x + geometry.interaction_outer_radius - 10.0,
        geometry.center_y,
    )

    assert cursor_rect_intersects_annulus(pointer, snapshot, geometry)


def test_exact_rectangle_excludes_outside_but_keeps_proxy_inside_core() -> None:
    geometry = _geometry()
    snapshot = _snapshot(width=8, height=8)

    outside = (
        geometry.center_x + geometry.interaction_outer_radius + 1.0,
        geometry.center_y,
    )
    inside = (geometry.center_x, geometry.center_y)

    assert not cursor_rect_intersects_annulus(outside, snapshot, geometry)
    assert cursor_rect_intersects_annulus(inside, snapshot, geometry)


def test_noncentral_hotspot_uses_continuous_exact_gl_bounds() -> None:
    geometry = _geometry()
    snapshot = _snapshot(width=20, height=10, hotspot_x=19, hotspot_y=9)
    pointer = (
        geometry.center_x + geometry.interaction_inner_radius - 0.5,
        geometry.center_y,
    )

    assert cursor_rect_intersects_annulus(pointer, snapshot, geometry)


def test_candidate_uses_conservative_256_by_256_radial_extent() -> None:
    geometry = _geometry()
    extent = math.hypot(256, 256)
    inner = max(0.0, geometry.interaction_inner_radius - extent)
    outer = geometry.interaction_outer_radius + extent

    assert cursor_candidate_intersects_annulus(
        (geometry.center_x + inner, geometry.center_y), geometry
    )
    assert cursor_candidate_intersects_annulus(
        (geometry.center_x + outer, geometry.center_y), geometry
    )
    assert not cursor_candidate_intersects_annulus(
        (geometry.center_x + outer + 0.01, geometry.center_y), geometry
    )


def test_candidate_and_exact_snapshot_intersection_are_distinct() -> None:
    geometry = _geometry()
    pointer = (
        geometry.center_x + geometry.interaction_outer_radius + 100.0,
        geometry.center_y,
    )

    assert cursor_candidate_intersects_annulus(pointer, geometry)
    assert not cursor_rect_intersects_annulus(
        pointer,
        _snapshot(width=16, height=16),
        geometry,
    )


def test_cursor_radius_uses_the_scene_camera_and_critical_impact(monkeypatch):
    import gargantua_scene_shader as scene
    observer = math.hypot(50.0, 3.1)
    sine = 2.598076211 * math.sqrt(1.0 - 1.0 / observer) / observer
    expected = 6.4 * sine / math.sqrt(1.0 - sine * sine)
    assert PROJECTED_LENS_RADIUS == pytest.approx(expected)
    monkeypatch.setattr(scene, "SCENE_FRAGMENT_SHADER_SOURCE",
                        scene.SCENE_FRAGMENT_SHADER_SOURCE.replace(
                            "CAMERA_DISTANCE = 50.0", "CAMERA_DISTANCE = 70.0"))
    assert scene.projected_critical_radius() < expected


def test_cursor_handoff_has_an_unwarped_margin_for_all_desktop_sizes():
    for width in (240, 360, 600):
        for dpr in (1.0, 1.25, 1.5, 2.0):
            geometry = LensGeometry.from_logical_size(width, width * 2 / 3, dpr)
            outer = geometry.radius + 2.40 * geometry.distortion_half_width
            assert geometry.interaction_inner_radius == 0.0
            assert geometry.interaction_outer_radius - outer >= 10.0 * dpr
