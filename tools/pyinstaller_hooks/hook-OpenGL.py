"""This Qt renderer uses system OpenGL, never bundled GLUT/GLE DLLs."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['OpenGL.platform.win32'] + collect_submodules('OpenGL.arrays')
# Intentionally omit OpenGL/DLLS: those optional legacy toolkits are not used.
datas = []
