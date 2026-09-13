import math
import re
from dataclasses import replace

import pytest
from companion import CompanionOrbit, IDLE_ORBIT_RADIUS, MIN_ORBIT_RADIUS, SPHERE_RADIUS
from gl_test_support import SceneRenderOptions, render_scene


@pytest.mark.parametrize('activity', ['idle', 'busy', 'output', 'attention', 'unknown'])
def test_every_state_keeps_orbiting_and_breathing(activity):
    orbit = CompanionOrbit(activity=activity)
    lights = []
    for _ in range(1200):
        previous = orbit.phase
        x, y, z, size, light = orbit.advance(1/60)
        assert 0 < (previous-orbit.phase) % math.tau < 0.025
        assert math.sqrt(x*x+(y-orbit.height)**2+z*z) == pytest.approx(orbit.radius)
        assert size == pytest.approx(0.255 * 0.70)
        assert all(math.isfinite(v) for v in (x,y,z,light))
        lights.append(light)
    assert max(lights[-300:])-min(lights[-300:]) > 0.02


def test_inner_orbit_is_faster_and_transition_does_not_teleport():
    orbit = CompanionOrbit(activity='idle')
    for _ in range(1200): orbit.advance(1/60)
    previous = orbit.phase
    orbit.advance(1/60)
    outer_speed = (previous-orbit.phase) % math.tau
    orbit.activity = 'busy'
    previous_radius = orbit.radius
    orbit.advance(1/60)
    assert abs(orbit.radius-previous_radius) < 0.1
    for _ in range(1200): orbit.advance(1/60)
    previous = orbit.phase
    orbit.advance(1/60)
    assert (previous-orbit.phase) % math.tau > 2 * outer_speed
    assert orbit.radius == pytest.approx(MIN_ORBIT_RADIUS, abs=0.001)
    assert (previous-orbit.phase) % math.tau / outer_speed == pytest.approx(22/5.0)


def test_paused_frame_and_large_frame_delta_are_bounded():
    orbit = CompanionOrbit()
    original = replace(orbit)
    orbit.advance(-1)
    assert orbit == original
    orbit.advance(100)
    assert orbit.clock == 0.1


@pytest.mark.parametrize('activity,period,light', [
    ('idle', 22.0, 1.0), ('busy', 5.0, 1.22),
    ('output', 6.8, 1.24), ('attention', 15.0, 0.94), ('unknown', 22.0, 0.55),
])
def test_settled_period_and_light(activity, period, light):
    orbit = CompanionOrbit(activity=activity)
    for _ in range(1200):
        orbit.advance(1/60)
    phase = orbit.phase
    orbit.advance(1/60)
    speed = (phase-orbit.phase) % math.tau * 60
    assert math.tau / speed == pytest.approx(period, abs=1e-7)
    assert orbit.brightness == pytest.approx(light, abs=1e-7)


def test_every_mode_and_transition_keeps_whole_sphere_outside_disk_corona():
    from gargantua_scene_shader import SCENE_FRAGMENT_SHADER_SOURCE
    outer = float(re.search(r'const float DISK_OUTER = ([0-9.]+);',
                            SCENE_FRAGMENT_SHADER_SOURCE).group(1))
    # Use the same outer corona envelope as the shader, not only the disk plane.
    assert 'DISK_OUTER * 1.04' in SCENE_FRAGMENT_SHADER_SOURCE
    modes = ('idle', 'busy', 'output', 'attention', 'unknown')
    orbit = CompanionOrbit()
    for start in modes:
        for end in modes:
            orbit.activity = start
            for _ in range(240):
                orbit.advance(1/30)
            orbit.activity = end
            for _ in range(720):
                x, y, z, size, light = orbit.advance(1/30)
                assert (math.hypot(x, z)-size > outer*1.04+0.10
                        or y-size > 0.25)
                assert MIN_ORBIT_RADIUS <= orbit.radius <= IDLE_ORBIT_RADIUS
                assert size == SPHERE_RADIUS


def test_state_switches_do_not_reset_phase_or_jump_brightness():
    orbit = CompanionOrbit(activity='idle')
    for _ in range(600):
        orbit.advance(1/60)
    for mode in ('busy', 'idle', 'unknown', 'busy'):
        pose = orbit.advance(0)
        before = replace(orbit)
        orbit.activity = mode
        assert orbit.advance(0) == pose
        next_pose = orbit.advance(1/60)
        # 5-second busy orbit travels about 0.153 world units per 60 Hz frame;
        # allow continuous radial motion during the staged outward transition.
        assert math.dist(next_pose[:3], pose[:3]) < 0.22
        assert abs(next_pose[4]-pose[4]) < 0.03
        assert abs(orbit.angular_speed-before.angular_speed) < 0.025
        for _ in range(120):
            orbit.advance(1/60)


def test_eased_motion_is_independent_of_frame_partition():
    fast = CompanionOrbit(activity='busy')
    slow = replace(fast)
    for _ in range(240):
        fast.advance(1/120)
    for _ in range(40):
        slow.advance(1/20)
    for attr in ('radius', 'phase', 'brightness', 'angular_speed',
                 'breath_phase', 'breath_rate', 'breath_amount',
                 'hover', 'height', 'inclination', 'rest_radius'):
        assert getattr(fast, attr) == pytest.approx(getattr(slow, attr), abs=1e-12)


def test_unknown_mode_falls_back_to_dim_safe_orbit():
    unknown = CompanionOrbit(activity='unknown')
    unsupported = CompanionOrbit(activity='future_status')
    for _ in range(600):
        assert unsupported.advance(1/60) == unknown.advance(1/60)
    assert unknown.brightness < 0.56
    assert unknown.radius == IDLE_ORBIT_RADIUS


def test_busy_lifts_before_approaching_and_clears_disk_at_every_phase():
    orbit=CompanionOrbit(activity='busy')
    for mode in ('busy','idle','busy','unknown'):
        orbit.activity=mode
        for _ in range(480):
            orbit.advance(1/60)
            # Analytic worst cases around the entire orbit, not selected frames.
            outside=orbit.radius*math.cos(orbit.inclination)-SPHERE_RADIUS > 8.5*1.04+0.10
            above=orbit.height-orbit.radius*math.sin(orbit.inclination)-SPHERE_RADIUS > 0.25
            assert outside or above
    for mode in ('idle','unknown','output','attention'):
        orbit.activity=mode
        for _ in range(1200): orbit.advance(1/60)
        assert orbit.tail_uniform()[3] == 0


def test_busy_tail_visible_and_disabled_companion_has_no_tail():
    orbit=CompanionOrbit(activity='busy')
    for _ in range(1200): orbit.advance(1/60)
    orbit.phase=2.0  # Side/rear arc against a transparent background, not the bright disk.
    pose=orbit.advance(0)
    options=SceneRenderOptions(render_size=(360,240),render_time=304,
        companion=pose[:4], companion_light=pose[4])
    head=render_scene(options,production_resolve=True)
    tail=render_scene(replace(options,companion_tail=orbit.tail_uniform()),production_resolve=True)
    def blues(frame):
        return sum(frame.pixels[i+2]>frame.pixels[i+1]+10
                   and frame.pixels[i+1]>frame.pixels[i]+20 for i in range(0,len(frame.pixels),4))
    assert blues(tail) > blues(head)+10
    off=replace(options,companion=(0,0,0,0))
    assert render_scene(off).pixels == render_scene(replace(off,companion_tail=orbit.tail_uniform())).pixels


def test_idle_matches_pre_tail_shader():
    import runpy
    from pathlib import Path
    backup=Path(__file__).resolve().parents[1]/'backups/20260912-before-busy-hover-tail/gargantua_scene_shader.py'
    if not backup.exists():
        pytest.skip('Optional local baseline is not distributed in source releases')
    shader=runpy.run_path(str(backup))['SCENE_FRAGMENT_SHADER_SOURCE']
    orbit=CompanionOrbit(activity='idle')
    for _ in range(1200): orbit.advance(1/60)
    pose=orbit.advance(0)
    options=SceneRenderOptions(render_size=(360,240),render_time=300,
        companion=pose[:4],companion_light=pose[4])
    assert render_scene(options,production_resolve=True).pixels == render_scene(
        options,production_resolve=True,fragment_source_override=shader).pixels


def test_orbit_is_nearly_equatorial_and_crosses_front_and_back():
    orbit = CompanionOrbit()
    poses = []
    for index in range(64):
        orbit.phase = math.tau*index/64
        poses.append(orbit.advance(0))
    assert max(abs(p[1]) for p in poses) < orbit.radius*0.11
    assert min(p[2] for p in poses) < -orbit.radius*0.99
    assert max(p[2] for p in poses) > orbit.radius*0.99


def test_actual_geodesics_show_front_companion_over_shadow_but_occlude_rear():
    options = SceneRenderOptions(render_size=(360,240), render_time=300)
    baseline = render_scene(options, production_resolve=True)
    # Preserve the accepted idle front/rear geometry independently of busy lift.
    orbit = CompanionOrbit(activity="idle", radius=9.2, brightness=1.22)
    counts = []
    for phase in (math.pi*1.5, math.pi*0.5):
        orbit.phase = phase
        pose = orbit.advance(0)
        frame = render_scene(replace(options, companion=pose[:4],
                                     companion_light=pose[4]), production_resolve=True)
        count = 0
        # The safe outer orbit projects lower than the former in-disk orbit.
        # Use the rendered opaque-black shadow itself, not a stale fixed crop.
        for y in range(frame.height):
            for x in range(frame.width):
                i = (y*frame.width+x)*4
                r,g,b,a = frame.pixels[i:i+4]
                count += (baseline.pixels[i:i+4] == bytes((0,0,0,255))
                          and b>g+10 and g>r+20 and a>240)
        counts.append(count)
    assert counts[0] >= 3
    assert counts[1] == 0


def test_real_gl_companion_is_tiny_blue_and_does_not_recolor_the_disk():
    orbit = CompanionOrbit(activity='idle')
    for _ in range(800): pose = orbit.advance(1/60)
    orbit.phase = 0.45
    pose = orbit.advance(0)
    options = SceneRenderOptions(render_size=(360,240), render_time=300)
    baseline = render_scene(options, production_resolve=True)
    on = render_scene(replace(options, companion=pose[:4], companion_light=pose[4]), production_resolve=True)
    blue = [(i//4 % on.width, i//4 // on.width) for i in range(0,len(on.pixels),4)
            if on.pixels[i+2]>on.pixels[i+1]+10 and on.pixels[i+1]>on.pixels[i]+30]
    assert 2 <= len(blue) <= 60
    assert max(x for x,y in blue)-min(x for x,y in blue) <= 10
    assert max(y for x,y in blue)-min(y for x,y in blue) <= 10
    changed = 0
    for y in range(60,180):
        for x in range(125,235):
            i=(y*on.width+x)*4
            changed += on.pixels[i:i+4] != baseline.pixels[i:i+4]
    # Geodesics create a few secondary blue images near the critical ring;
    # forbid a whole-disk grade change, not those physically lensed pixels.
    assert changed < 120  # Under 1% of this 13200-pixel central region.
    # Debug rays remain the original black-hole diagnostics, not blue status.
    debug = replace(options, debug_view=1.0)
    assert render_scene(debug).pixels == render_scene(replace(debug, companion=pose[:4])).pixels
