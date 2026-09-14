"""Explicit, per-user Windows logon startup. Never enabled during app startup."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DesktopBlackHole"
OWNED_COMMAND = "startup/owned_command"


def startup_command() -> str:
    if sys.platform != "win32":
        raise OSError("Windows logon startup is unavailable on this platform.")
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        args = [str(executable)]
    else:
        executable = executable.with_name("pythonw.exe")
        args = [str(executable), str(Path(__file__).resolve().parent / "launcher.pyw")]
    if not all(Path(value).is_file() for value in args):
        raise OSError("The windowed launcher is missing. Keep the complete app folder.")
    command = subprocess.list2cmdline(args)
    if len(command) > 260 or any(ord(char) < 32 for char in command):
        raise ValueError("The startup command is too long or contains unsupported characters.")
    return command


class Autostart:
    def __init__(self, settings):
        self.settings = settings

    def _read(self):
        if winreg is None:
            raise OSError("Windows logon startup is unavailable on this platform.")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
                value, kind = winreg.QueryValueEx(key, VALUE_NAME)
                if kind != winreg.REG_SZ or not isinstance(value, str):
                    raise ValueError("The startup entry is not owned by this app.")
                return value
        except FileNotFoundError:
            return None

    def _checked_entry(self):
        command = startup_command()
        current = self._read()
        previous = self.settings.value(OWNED_COMMAND, "", type=str)
        if current is not None and current not in {command, previous or command}:
            raise ValueError("An unrelated startup entry uses the same name; it was not changed.")
        return current, command

    def enabled(self) -> bool:
        current, _ = self._checked_entry()
        return current is not None

    def set_enabled(self, enabled: bool):
        current, command = self._checked_entry()
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
            self.settings.setValue(OWNED_COMMAND, command)
        elif current is not None:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
            self.settings.remove(OWNED_COMMAND)


def add_startup_action(menu, parent):
    """Creating/opening a menu only reads this app's one registry value."""
    from PySide6.QtWidgets import QMessageBox
    from ui_language import translate

    language = getattr(parent, "_language", "en")
    manager = Autostart(parent._settings)
    action = menu.addAction("开机启动（当前用户）")
    action.setCheckable(True)
    action.setToolTip("默认关闭；仅在当前用户登录 Windows 时启动，无需管理员权限。移动程序后请重新开关一次。")
    try:
        action.setChecked(manager.enabled())
    except (OSError, ValueError):
        action.setEnabled(False)
        action.setToolTip("开机启动不可用：启动器缺失、路径过长、权限不足或存在同名非本程序启动项。")

    def change(enabled):
        try:
            manager.set_enabled(enabled)
        except (OSError, ValueError):
            # Do not expose another program's command or silently report success.
            try:
                action.setChecked(manager.enabled())
            except (OSError, ValueError):
                action.setChecked(False)
            QMessageBox.warning(parent, translate("开机启动", language), translate(
                "无法更改开机启动。请检查启动器路径与权限；同名的其他启动项不会被覆盖。", language))
    action.triggered.connect(change)
    return action
