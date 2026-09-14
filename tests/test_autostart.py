from contextlib import nullcontext

import pytest
from PySide6.QtCore import QSettings

import autostart


class FakeRegistry:
    HKEY_CURRENT_USER = 'current-user'
    KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = 1, 2, 3

    def __init__(self):
        self.values = {'AnotherApp': ('do-not-touch', self.REG_SZ)}
        self.writes = []

    def OpenKey(self, root, path, reserved, access):
        assert root == self.HKEY_CURRENT_USER and path == autostart.RUN_KEY
        return nullcontext('key')

    CreateKeyEx = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append(('set', name))
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        self.writes.append(('delete', name))
        del self.values[name]


@pytest.fixture
def manager(monkeypatch, tmp_path):
    registry = FakeRegistry()
    monkeypatch.setattr(autostart, 'winreg', registry)
    monkeypatch.setattr(autostart, 'startup_command', lambda: '"D:\\My Apps\\DesktopBlackHole.exe"')
    settings = QSettings(str(tmp_path / 'startup.ini'), QSettings.IniFormat)
    return autostart.Autostart(settings), registry


def test_default_read_is_off_and_never_writes(manager):
    service, registry = manager
    assert not service.enabled()
    assert registry.writes == []


def test_toggle_only_changes_owned_current_user_value(manager):
    service, registry = manager
    service.set_enabled(True)
    assert service.enabled()
    assert service.settings.value(autostart.OWNED_COMMAND) == autostart.startup_command()
    service.set_enabled(False)
    assert not service.enabled()
    assert registry.values == {'AnotherApp': ('do-not-touch', registry.REG_SZ)}
    service.set_enabled(False)
    assert len(registry.writes) == 2


def test_refuses_unowned_name_collision(manager):
    service, registry = manager
    registry.values[autostart.VALUE_NAME] = ('other-program.exe', registry.REG_SZ)
    for value in [False, True]:
        with pytest.raises(ValueError, match='unrelated'):
            service.set_enabled(value)
    assert registry.writes == []


def test_removing_saved_old_location_and_enabling_new_location(manager, monkeypatch):
    service, registry = manager
    service.set_enabled(True)
    monkeypatch.setattr(autostart, 'startup_command', lambda: '"D:\\Moved\\DesktopBlackHole.exe"')
    assert service.enabled()
    service.set_enabled(False)
    service.set_enabled(True)
    assert registry.values[autostart.VALUE_NAME][0] == autostart.startup_command()


def test_permission_failure_preserves_ownership_state(manager, monkeypatch):
    service, registry = manager
    def denied(*args):
        raise PermissionError('denied')
    monkeypatch.setattr(registry, 'CreateKeyEx', denied)
    with pytest.raises(PermissionError):
        service.set_enabled(True)
    assert not service.settings.contains(autostart.OWNED_COMMAND)
    assert registry.writes == []


def test_frozen_command_quotes_space_without_shell(monkeypatch, tmp_path):
    executable = tmp_path / 'My Apps' / 'DesktopBlackHole.exe'
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart.sys, 'executable', str(executable))
    monkeypatch.setattr(autostart.sys, 'frozen', True, raising=False)
    assert autostart.startup_command() == '"' + str(executable.resolve()) + '"'


def test_source_command_uses_pythonw_and_absolute_launcher(monkeypatch, tmp_path):
    executable = tmp_path / 'python.exe'
    executable.touch()
    windowed = tmp_path / 'pythonw.exe'
    windowed.touch()
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart.sys, 'executable', str(executable))
    monkeypatch.setattr(autostart.sys, 'frozen', False, raising=False)
    command = autostart.startup_command()
    assert str(windowed) in command and 'launcher.pyw' in command
    assert 'cmd.exe' not in command
