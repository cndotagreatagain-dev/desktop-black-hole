from PySide6.QtCore import QSettings, QPoint, QTimer, QCoreApplication, QEvent
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from codex_status import GlobalActivity
from desktop_black_hole import DesktopBlackHole
from ui_language import activity_detail, translate


def test_status_labels_and_details_are_localized():
    for label, expected in [('Codex：处理中', 'Codex: Busy'),
                            ('DSH：空闲', 'DSH: Idle'),
                            ('总状态：状态待确认', 'Overall status: Status unconfirmed'),
                            ('状态灯：未启用来源', 'Status light: No sources enabled')]:
        assert translate(label, 'en') == expected
        assert translate(label, 'zh') == label
    state = GlobalActivity('busy', 'Codex：处理中', '中文说明', 2, 1, 0)
    assert 'Connected sources: 2 | Busy: 1' in activity_detail(state, 'en')
    assert activity_detail(state, 'zh') == '中文说明'


def test_default_english_menu_switch_and_persistence(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'language.ini'), QSettings.IniFormat)
    window = DesktopBlackHole(settings=settings)
    errors = []
    try:
        assert window._language == 'en'
        def inspect_menu():
            menu = app.activePopupWidget()
            try:
                actions = {a.text(): a for a in menu.actions()}
                assert 'Cursor gravity' in actions
                assert 'Codex: Disconnected' in actions
                language = actions['Language / 语言'].menu()
                assert [a.isChecked() for a in language.actions()] == [True, False]
                language.actions()[1].trigger()
            except Exception as error:
                errors.append(error)
            finally:
                menu.close()
        QTimer.singleShot(0, inspect_menu)
        app.sendEvent(window, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(10, 10), QPoint(100, 100)))
        assert not errors, errors
        assert window._language == 'zh'
        assert window._activity_label == 'Codex：未连接'
        restored = DesktopBlackHole(settings=settings)
        try:
            assert restored._language == 'zh'
            restored.set_language('en')
            restored.set_language('invalid')
            assert restored._language == 'en'
            assert settings.value('ui/language') == 'en'
        finally:
            restored.close()
            restored.deleteLater()
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_invalid_saved_language_defaults_to_english(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'invalid.ini'), QSettings.IniFormat)
    settings.setValue('ui/language', 'unsupported')
    window = DesktopBlackHole(settings=settings)
    try:
        assert window._language == 'en'
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
