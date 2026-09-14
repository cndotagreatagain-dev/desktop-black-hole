"""Explicit QA mode of the real frozen GUI. Uses isolated settings and self-closes."""
import ctypes
import json
from pathlib import Path
import sys


def main(output):
    from PySide6.QtCore import Qt, QTimer, QSettings, QPoint
    from PySide6.QtGui import QSurfaceFormat, QContextMenuEvent
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
            if phase_index == 3:
                def inspect_menu():
                    menu=app.activePopupWidget()
                    try:
                        if menu is None:
                            raise RuntimeError('Menu did not open')
                        actions={a.text():a for a in menu.actions()}
                        result['startup_menu']=('Start with Windows (current user)' in actions)
                        sources=actions['Status sources'].menu()
                        result['dsh_setup_menu']=('DSH setup guide' in [a.text() for a in sources.actions()])
                        menu.grab().save(str(output/'menu-en.png'))
                    except Exception as error:
                        result['errors'].append(str(error))
                    finally:
                        if menu is not None: menu.close()
                QTimer.singleShot(100, inspect_menu)
                app.sendEvent(window, QContextMenuEvent(QContextMenuEvent.Mouse,
                              QPoint(10,10), QPoint(100,100)))
                from dsh_setup import DshSetupDialog, plugin_path
                from autostart import startup_command
                dialog=DshSetupDialog(window, 'en')
                dialog.show()
                app.processEvents()
                dialog.grab().save(str(output/'dsh-setup-en.png'))
                result['dsh_plugin_present']=plugin_path().is_file()
                result['dsh_copy_enabled']=dialog.copy_button.isEnabled()
                result['windowed_startup_command']=('python.exe' not in startup_command().lower())
                dialog.reject()
                if not all(result.get(key) for key in ('startup_menu','dsh_setup_menu',
                        'dsh_plugin_present','dsh_copy_enabled','windowed_startup_command')):
                    raise RuntimeError('New integration/startup UI checks failed')
                app.quit()
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
