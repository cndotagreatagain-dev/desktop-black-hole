"""Static geodesic-derived desktop map and transient display-color texture."""
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader
from PySide6.QtCore import QRect

from black_hole_shaders import FULLSCREEN_VERTEX_SHADER_SOURCE
from desktop_capture import desktop_transform
from gargantua_scene_shader import SCENE_FRAGMENT_SHADER_SOURCE


class DesktopLensRenderer:
    def __init__(self):
        self.program = 0
        self.map_texture = 0
        self.framebuffer = 0
        self.desktop_texture = 0
        self.map_key = None
        self.image_size = None
        self.frame_key = None

    def close(self):
        if self.program:
            GL.glDeleteProgram(self.program)
        if self.framebuffer:
            GL.glDeleteFramebuffers(1, [self.framebuffer])
        for texture in (self.map_texture, self.desktop_texture):
            if texture:
                GL.glDeleteTextures(1, [texture])
        self.__init__()

    @staticmethod
    def _configure_texture():
        for name in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, GL.GL_LINEAR)
        for name in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, GL.GL_CLAMP_TO_EDGE)

    def prepare(self, frame, widget_rect, source_size, quality):
        if not self.program:
            source = SCENE_FRAGMENT_SHADER_SOURCE.replace(
                "#version 330 core", "#version 330 core\n#define OUTPUT_DESKTOP_MAP 1", 1)
            self.program = int(compileProgram(
                compileShader(FULLSCREEN_VERTEX_SHADER_SOURCE, GL.GL_VERTEX_SHADER),
                compileShader(source, GL.GL_FRAGMENT_SHADER),
            ))
        GL.glActiveTexture(GL.GL_TEXTURE3)
        if not self.map_texture:
            self.map_texture = int(GL.glGenTextures(1))
            self.framebuffer = int(GL.glGenFramebuffers(1))
        key = (*source_size, quality)
        if key != self.map_key:
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.map_texture)
            self._configure_texture()
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA16F, *source_size, 0,
                            GL.GL_RGBA, GL.GL_FLOAT, None)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.framebuffer)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                      GL.GL_TEXTURE_2D, self.map_texture, 0)
            if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("Desktop geodesic map framebuffer is incomplete")
            GL.glDisable(GL.GL_BLEND)
            GL.glViewport(0, 0, *source_size)
            GL.glUseProgram(self.program)
            GL.glUniform2f(GL.glGetUniformLocation(self.program, "u_resolution"), *source_size)
            GL.glUniform1f(GL.glGetUniformLocation(self.program, "u_quality"), quality)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            self.map_key = key
        if not self.desktop_texture:
            self.desktop_texture = int(GL.glGenTextures(1))
        GL.glActiveTexture(GL.GL_TEXTURE4)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.desktop_texture)
        if self.frame_key != (frame.serial, frame.timestamp):
            image = frame.image
            size = (image.width(), image.height())
            self._configure_texture()
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            # RGBA8888 is top-down, with no row padding for four-byte pixels.
            if size != self.image_size:
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, *size, 0,
                                GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, image.constBits())
                self.image_size = size
            else:
                GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, *size,
                                   GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, image.constBits())
            self.frame_key = (frame.serial, frame.timestamp)
        return desktop_transform(QRect(*widget_rect), frame.rect)
