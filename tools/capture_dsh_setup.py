"""Local-only QA capture; screenshots contain machine paths, do not publish."""
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dsh_setup import DshSetupDialog


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    dialogs = []
    def capture(index=0):
        if index == 2:
            app.quit()
            return
        language = ('en', 'zh')[index]
        dialog = DshSetupDialog(language=language)
        dialogs.append(dialog)
        dialog.show()
        def save():
            dialog.grab().save(str(output / (language + '.png')))
            dialog.close()
            capture(index + 1)
        QTimer.singleShot(500, save)
    QTimer.singleShot(0, capture)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
