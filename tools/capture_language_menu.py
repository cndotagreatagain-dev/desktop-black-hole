"""Capture real English/Chinese menus using isolated settings, then exit."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer, QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication
from desktop_black_hole import DesktopBlackHole


def main(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = DesktopBlackHole(settings=QSettings(str(output/'settings.ini'), QSettings.IniFormat))
    errors = []
    for language in ('en', 'zh'):
        window.set_language(language)
        def capture():
            menu = app.activePopupWidget()
            try:
                if menu is None or not menu.grab().save(str(output/f'menu-{language}.png')):
                    raise RuntimeError('Menu capture failed')
            except Exception as error:
                errors.append(str(error))
            finally:
                if menu is not None:
                    menu.close()
        QTimer.singleShot(150, capture)
        app.sendEvent(window, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(10, 10), QPoint(100, 100)))
    window.close()
    if errors:
        raise RuntimeError(errors)


if __name__ == '__main__':
    main(sys.argv[1])
