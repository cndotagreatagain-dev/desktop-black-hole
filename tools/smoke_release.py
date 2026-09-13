"""Explicit QA mode of the real frozen GUI. Uses isolated settings and self-closes."""
import ctypes
import json
from pathlib import Path
import sys


def main(output):
    from PySide6.QtCore import Qt, QTimer, QSettings
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from OpenGL import GL
    from desktop_black_hole import DesktopBlackHole, configure_surface_format
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = dict(frozen=bool(getattr(sys,'frozen',False)), executable=sys.executable,
                  bundle=str(getattr(sys,'_MEIPASS','')), errors=[], frames=[])
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app=QApplication([])
    settings=QSettings(str(output/'qa-settings.ini'), QSettings.IniFormat)
    settings.setValue('status/codex_enabled',False)
    settings.setValue('status/dsh_enabled',False)
    settings.setValue('effects/cursor_lens_enabled',False)
    window=DesktopBlackHole(settings=settings, desktop_lens_default=False, companion_default=True)
    window._activity_timer.stop()
    window.set_desktop_lens_enabled(False)
    window.resize(600,400)
    window._frame_timer.stop()
    phase_index=0
    def fail(message):
        result['errors'].append(str(message)); app.quit()
    def capture():
        nonlocal phase_index
        try:
            activity='idle' if phase_index == 0 else 'busy'
            window.set_companion_activity(activity)
            for _ in range(800): window._companion_orbit.advance(1/60)
            window._companion_orbit.phase=(0.45,3.8,5.0)[phase_index]
            frame=window.grabFramebuffer()
            if frame.isNull() or not frame.save(str(output/f'{activity}-{phase_index}.png')):
                raise RuntimeError('Empty framebuffer')
            result['frames'].append(dict(activity=activity, width=frame.width(),height=frame.height()))
            window.makeCurrent()
            result['renderer']=GL.glGetString(GL.GL_RENDERER).decode()
            result['gl_version']=GL.glGetString(GL.GL_VERSION).decode()
            result['gl_error']=int(GL.glGetError())
            window.doneCurrent()
            result['console_window']=int(ctypes.windll.kernel32.GetConsoleWindow())
            result['accept_drops']=window.acceptDrops()
            result['transparent']=window.testAttribute(Qt.WA_TranslucentBackground)
            result['frameless']=bool(window.windowFlags() & Qt.FramelessWindowHint)
            phase_index += 1
            if phase_index == 3: app.quit()
            else: QTimer.singleShot(80,capture)
        except Exception as error: fail(error)
    window.fatal_error.connect(fail)
    window.show()
    QTimer.singleShot(800,capture)
    QTimer.singleShot(20000,lambda:fail('Timed out'))
    app.exec()
    window.close()
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return int(bool(result['errors']))
