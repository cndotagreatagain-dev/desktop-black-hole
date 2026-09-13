"""Shader-level temporal contracts, after reviewing long-run production captures."""
import re
from functools import lru_cache

import pytest

from gargantua_scene_shader import SCENE_FRAGMENT_SHADER_SOURCE
from gl_test_support import SceneRenderOptions, render_scene


def _constant(name):
    return float(re.search(rf"const float {name} = ([0-9.]+);",
                           SCENE_FRAGMENT_SHADER_SOURCE).group(1))


@lru_cache(maxsize=16)
def _flow_field(time):
    # Render the actual GLSL material functions in a co-rotating polar domain.
    # This isolates accumulated radial winding from lensing and tone mapping.
    prefix = SCENE_FRAGMENT_SHADER_SOURCE.split("vec3 thermalPalette(", 1)[0]
    source = prefix + r"""
void main() {
    float materialTime = u_time * PATTERN_SPEED;
    float angle = v_uv.x * TAU
        - mod(materialTime * orbitalOmega(FLOW_CARRIER_RADIUS), TAU);
    float radius = mix(3.15, 8.0, v_uv.y);
    vec4 flow = orbitalFlow(angle, radius, materialTime);
    fragColor = vec4(flow.xyz, 1.0);
}
"""
    return render_scene(SceneRenderOptions(render_size=(192, 96), render_time=time),
                        fragment_source_override=source).pixels


@pytest.mark.parametrize("cycles", (1, 32, 390))
def test_material_does_not_accumulate_shear_after_many_lifetimes(cycles):
    lifetime = _constant("FLOW_LIFETIME") / _constant("PATTERN_SPEED")
    first = _flow_field(0.7)
    late = _flow_field(0.7 + cycles * lifetime)
    # Whole lifetimes only rotate the carrier. Their co-rotating field must
    # retain its structure even at approximately one hour of application time.
    mean_error = sum(abs(a - b) for a, b in zip(first, late)) / len(first)
    assert mean_error < 0.5


@pytest.mark.parametrize("half_cycles", (1, 2, 65, 780))
def test_field_renewal_has_no_visible_phase_reset(half_cycles):
    boundary = half_cycles * _constant("FLOW_LIFETIME") / (2 * _constant("PATTERN_SPEED"))
    before, after = _flow_field(boundary - 0.001), _flow_field(boundary + 0.001)
    mean_error = sum(abs(a - b) for a, b in zip(before, after)) / len(before)
    assert mean_error < 0.8
