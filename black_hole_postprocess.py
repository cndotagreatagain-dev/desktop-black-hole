"""Floating-point bloom and premultiplied-alpha display resolve.

Scene textures are transient render targets. The optional desktop texture is a
live local screen ROI, composited behind emission without exposure processing.
The geodesic shader is the sole source of the black-hole silhouette.
"""
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader

from black_hole_shaders import FULLSCREEN_VERTEX_SHADER_SOURCE
from desktop_lens import DesktopLensRenderer


BLUR_SOURCE = r"""#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_source;
uniform vec2 u_direction;
uniform float u_extract;
vec3 sampleLight(vec2 uv) {
    vec3 c = texture(u_source, uv).rgb;
    float peak = max(c.r, max(c.g, c.b));
    return u_extract > 0.5 ? c * max(peak - 1.0, 0.0) / max(peak, 0.001) : c;
}
void main() {
    vec3 c = sampleLight(v_uv) * 0.227027;
    c += sampleLight(v_uv + u_direction * 1.384615) * 0.316216;
    c += sampleLight(v_uv - u_direction * 1.384615) * 0.316216;
    c += sampleLight(v_uv + u_direction * 3.230769) * 0.070270;
    c += sampleLight(v_uv - u_direction * 3.230769) * 0.070270;
    fragColor = vec4(c, 0.0);
}
"""

RESOLVE_SOURCE = r"""#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_scene;
uniform sampler2D u_fine;
uniform sampler2D u_wide;
uniform vec2 u_resolution;
uniform float u_time;
uniform float u_debug;
uniform float u_desktop_enabled;
uniform sampler2D u_desktop;
uniform sampler2D u_lens_map;
uniform vec4 u_desktop_transform;
vec3 aces(vec3 c) {
    return clamp((c * (2.51 * c + 0.03)) /
        (c * (2.43 * c + 0.59) + 0.14), 0.0, 1.0);
}
float hash(vec2 p) {
    vec3 q = fract(p.xyx * 0.1031);
    q += dot(q, q.yzx + 33.33);
    return fract((q.x + q.y) * q.z);
}
vec4 resolveSample(vec2 uv) {
    vec4 scene = texture(u_scene, uv);
    // Subpixel optical dispersion, after geodesic rendering.
    vec2 offset = (uv - 0.5) * 0.28 / u_resolution;
    scene.r = texture(u_scene, uv + offset).r;
    scene.b = texture(u_scene, uv - offset).b;
    float peak = max(scene.r, max(scene.g, scene.b));
    float absorber = scene.a * (1.0 - smoothstep(0.001, 0.07, peak));
    vec3 glow = (texture(u_fine, uv).rgb * 0.12
        + texture(u_wide, uv).rgb * 0.14) * (1.0 - absorber);
    vec2 p = uv * 2.0 - 1.0;
    p.x *= u_resolution.x / u_resolution.y;
    float edge = 1.0 - smoothstep(0.84, 1.02, length(p / vec2(1.46, 1.02)));
    glow *= edge;
    float glowAlpha = 1.0 - exp(-max(glow.r, max(glow.g, glow.b)) * 1.8);
    float alpha = clamp(scene.a + (1.0 - scene.a) * glowAlpha, 0.0, 1.0);
    vec3 color = pow(aces((scene.rgb + glow) / max(alpha, 0.001)), vec3(1.0 / 2.2));
    color *= vec3(1.0, 0.982, 0.928);
    color *= 1.0 - 0.07 * smoothstep(0.3, 1.0, length(p / vec2(1.46, 1.02)));
    color *= 1.0 + (hash(gl_FragCoord.xy + floor(u_time * 24.0)) - 0.5) * 0.006;
    vec4 foreground = vec4(clamp(color, 0.0, 1.0) * alpha, alpha);
    if (u_desktop_enabled > 0.5) {
        vec3 lens = texture(u_lens_map, uv).xyz;
        vec2 sourceUV = uv + lens.xy * 0.5
            * vec2(u_resolution.y / u_resolution.x, 1.0);
        vec2 desktopUV = u_desktop_transform.xy
            + vec2(sourceUV.x, 1.0 - sourceUV.y) * u_desktop_transform.zw;
        vec2 desktopPixels = vec2(textureSize(u_desktop, 0));
        vec2 border = min(desktopUV, 1.0 - desktopUV) * desktopPixels;
        float valid = smoothstep(0.0, 3.0, min(border.x, border.y));
        float coverage = clamp(lens.z, 0.0, 1.0) * valid * edge;
        // Captured desktop is already display encoded: never ACES/bloom it.
        // Existing premultiplied opacity handles black capture and disk occlusion.
        vec3 desktop = texture(u_desktop, desktopUV).rgb;
        foreground.rgb += (1.0 - foreground.a) * coverage * desktop;
        foreground.a += (1.0 - foreground.a) * coverage;
    }
    return foreground;
}
void main() {
    vec2 edgePixels = min(gl_FragCoord.xy, u_resolution - gl_FragCoord.xy);
    if (min(edgePixels.x, edgePixels.y) < 1.0) { fragColor = vec4(0.0); return; }
    if (u_debug > 0.5) { fragColor = texture(u_scene, v_uv); return; }
    // Resolve the four physical source-pixel centers independently BEFORE
    // display-space area averaging. Tone mapping an already-averaged HDR
    // highlight clips partial coverage into bright stairsteps at the subring.
    // Bloom remains HDR; this is spatial AA only, with no temporal history.
    vec4 sum = vec4(0.0);
    for (int sampleIndex = 0; sampleIndex < 4; ++sampleIndex) {
        vec2 offset = (vec2(float(sampleIndex % 2), float(sampleIndex / 2)) - 0.5)
            * 0.5 / u_resolution;
        sum += resolveSample(v_uv + offset);
    }
    fragColor = sum * 0.25;
}
"""


class HDRPostprocess:
    def __init__(self):
        self.size = (0, 0)
        self.targets = []
        self.blur_program = 0
        self.resolve_program = 0
        self.desktop_renderer = DesktopLensRenderer()
        self.desktop_frame = None
        self.desktop_geometry = (0, 0, 1, 1)
        self.desktop_quality = 1
        self.desktop_error = ""

    def _program(self, source):
        return int(compileProgram(
            compileShader(FULLSCREEN_VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
            compileShader(source, GL.GL_FRAGMENT_SHADER),
        ))

    def _target(self, width, height):
        texture = int(GL.glGenTextures(1))
        framebuffer = int(GL.glGenFramebuffers(1))
        self.targets.append((texture, framebuffer, width, height))
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F, width, height,
                        0, GL.GL_RGBA, GL.GL_FLOAT, None)
        for parameter in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, parameter, GL.GL_LINEAR)
        for parameter in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, parameter, GL.GL_CLAMP_TO_EDGE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                  GL.GL_TEXTURE_2D, texture, 0)
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("HDR bloom framebuffer is incomplete")
        return self.targets[-1]

    def _delete_targets(self):
        targets, self.targets = self.targets, []
        self.size = (0, 0)
        for texture, framebuffer, _, _ in targets:
            GL.glDeleteTextures(1, [texture])
            GL.glDeleteFramebuffers(1, [framebuffer])

    def close(self):
        self.desktop_renderer.close()
        self.desktop_frame = None
        self._delete_targets()
        for name in ("blur_program", "resolve_program"):
            program = getattr(self, name)
            setattr(self, name, 0)
            if program:
                GL.glDeleteProgram(program)

    def _ensure(self, size):
        if not self.blur_program:
            self.blur_program = self._program(BLUR_SOURCE)
        if not self.resolve_program:
            self.resolve_program = self._program(RESOLVE_SOURCE)
        if size == self.size:
            return
        self._delete_targets()
        width, height = size
        self._target(width, height)
        for divisor in (2, 24):
            for _ in range(2):
                self._target(max(1, width // divisor), max(1, height // divisor))
        self.size = size

    def _texture(self, program, name, texture, unit):
        GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glUniform1i(GL.glGetUniformLocation(program, name), unit)

    def render(self, source, source_size, destination, output_size, time, debug):
        self._ensure(source_size)
        desktop_transform = None
        if self.desktop_frame is None and self.desktop_renderer.desktop_texture:
            self.desktop_renderer.close()
        if self.desktop_frame is not None and not debug and not self.desktop_error:
            try:
                desktop_transform = self.desktop_renderer.prepare(
                    self.desktop_frame, self.desktop_geometry, source_size, self.desktop_quality)
            except Exception as error:
                self.desktop_error = str(error)
        scene, fine_a, fine_b, wide_a, wide_b = self.targets
        GL.glDisable(GL.GL_BLEND)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, source)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, scene[1])
        GL.glBlitFramebuffer(0, 0, *source_size, 0, 0, *source_size,
                             GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        if not debug:
            program = self.blur_program
            GL.glUseProgram(program)
            for first, second in ((fine_a, fine_b), (wide_a, wide_b)):
                for source_texture, target, direction, extract in (
                    (scene[0], first, (1.0 / first[2], 0.0), 1.0),
                    (first[0], second, (0.0, 1.0 / second[3]), 0.0),
                ):
                    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target[1])
                    GL.glViewport(0, 0, target[2], target[3])
                    self._texture(program, "u_source", source_texture, 0)
                    GL.glUniform2f(GL.glGetUniformLocation(program, "u_direction"), *direction)
                    GL.glUniform1f(GL.glGetUniformLocation(program, "u_extract"), extract)
                    GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        program = self.resolve_program
        GL.glUseProgram(program)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, destination)
        GL.glViewport(0, 0, *output_size)
        for unit, (name, texture) in enumerate((
            ("u_scene", scene[0]), ("u_fine", fine_b[0]), ("u_wide", wide_b[0])
        )):
            self._texture(program, name, texture, unit)
        GL.glUniform2f(GL.glGetUniformLocation(program, "u_resolution"), *output_size)
        GL.glUniform1f(GL.glGetUniformLocation(program, "u_time"), time)
        GL.glUniform1f(GL.glGetUniformLocation(program, "u_debug"), float(debug))
        GL.glUniform1f(GL.glGetUniformLocation(program, "u_desktop_enabled"),
                       float(desktop_transform is not None))
        if desktop_transform is not None:
            self._texture(program, "u_lens_map", self.desktop_renderer.map_texture, 3)
            self._texture(program, "u_desktop", self.desktop_renderer.desktop_texture, 4)
            GL.glUniform4f(GL.glGetUniformLocation(program, "u_desktop_transform"),
                           *desktop_transform)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
