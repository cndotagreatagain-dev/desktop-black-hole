import sys

import pytest
from PySide6.QtWidgets import QApplication

import dsh_setup


def test_snippet_is_absolute_and_quotes_apostrophes(tmp_path):
    path = tmp_path / "Owner's 中文 Apps" / "index.mjs"
    snippet = dsh_setup.patch_snippet(path)
    assert "Owner''s 中文 Apps" in snippet
    assert "\\" not in snippet
    assert snippet.count("id: black-hole-status") == 1
    with pytest.raises(ValueError):
        dsh_setup.patch_snippet(dsh_setup.Path("relative/index.mjs"))


@pytest.mark.parametrize("char", ["\n", "\r", "\t", "\x00", "\x85", "\u2028", "\u2029"])
def test_snippet_rejects_yaml_control_characters(tmp_path, char):
    with pytest.raises(ValueError):
        dsh_setup.patch_snippet(tmp_path / ("bad" + char + "path") / "index.mjs")


def test_packaged_path_and_custom_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DesktopBlackHole.exe"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "custom-dsh"))
    assert dsh_setup.plugin_path() == tmp_path / "dsh-status" / "index.mjs"
    assert dsh_setup.web_patch_path() == tmp_path / "custom-dsh/profiles/web/cordis.patch.yml"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_dialog_never_changes_config_or_clipboard_until_copy(monkeypatch, tmp_path, language):
    app = QApplication.instance() or QApplication([])
    config = tmp_path / "cordis.patch.yml"
    original = b"- insert:\n    - id: another-plugin\n      name: example\n"
    config.write_bytes(original)
    monkeypatch.setattr(dsh_setup, "web_patch_path", lambda: config)
    class Clipboard:
        value = "keep this clipboard"
        def text(self):
            return self.value
        def setText(self, value):
            self.value = value
    clipboard = Clipboard()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    dialog = dsh_setup.DshSetupDialog(language=language)
    try:
        assert dialog.target.isReadOnly() and dialog.snippet.isReadOnly()
        assert dialog.copy_button.isEnabled()
        assert clipboard.text() == "keep this clipboard"
        dialog.copy_button.click()
        assert clipboard.text() == dialog.snippet.toPlainText()
        assert "id: black-hole-status" in clipboard.text()
        dialog.reject()
        assert config.read_bytes() == original
        assert list(tmp_path.iterdir()) == [config]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_missing_plugin_cannot_be_copied(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(dsh_setup, "plugin_path", lambda: tmp_path / "missing.mjs")
    monkeypatch.setattr(dsh_setup, "web_patch_path", lambda: tmp_path / "unused.yml")
    dialog = dsh_setup.DshSetupDialog()
    try:
        assert not dialog.copy_button.isEnabled()
        dialog.reject()
        assert not list(tmp_path.iterdir())
    finally:
        dialog.deleteLater()
