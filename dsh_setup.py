"""Read-only DSH onboarding. Never reads or modifies host configuration."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)


def plugin_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "dsh-status" / "index.mjs"
    return Path(__file__).resolve().parent / "integrations" / "dsh-status" / "index.mjs"


def web_patch_path() -> Path:
    configured = os.environ.get("DSH_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".dsh"
    return root.absolute() / "profiles" / "web" / "cordis.patch.yml"


def patch_snippet(path: Path) -> str:
    """Encode one local module path as a YAML scalar, never a shell command."""
    if not path.is_absolute():
        raise ValueError("The plugin path must be absolute.")
    value = path.as_posix()
    if any(ord(char) < 32 or ord(char) == 127 or char in "\x85\u2028\u2029" for char in value):
        raise ValueError("The plugin path contains unsupported control characters.")
    value = value.replace("'", "''")
    return "- insert:\n    - id: black-hole-status\n      name: '" + value + "'\n"


class DshSetupDialog(QDialog):
    def __init__(self, parent=None, language="en"):
        super().__init__(parent)
        zh = language == "zh"
        self.setWindowTitle("DSH 接入向导" if zh else "DSH setup guide")
        self.resize(650, 580)
        layout = QVBoxLayout(self)

        def label(text):
            widget = QLabel(text)
            widget.setTextFormat(Qt.PlainText)
            widget.setWordWrap(True)
            layout.addWidget(widget)
            return widget

        label(("勾选 DSH 只开启状态读取，不会安装或启动 DSH。此向导仅显示说明，不读取或修改配置。"
               if zh else "Enabling DSH only turns on the status reader; it does not install or start DSH. "
               "This guide displays instructions without reading or changing configuration."))
        label("1. 先安装并启动一次 DSH web，再备份下面的配置文件：" if zh else
              "1. Install and start DSH web once, then back up this configuration file:")
        self.target = QPlainTextEdit(str(web_patch_path()))
        self.target.setReadOnly(True)
        self.target.setMaximumHeight(65)
        layout.addWidget(self.target)
        label(("使用自定义 DSH_HOME 时，两边必须指向同一目录。这里针对 web profile；其他 profile 请参阅随包说明。"
               if zh else "If DSH uses a custom DSH_HOME, both apps must use the same location. "
               "This path is for the web profile; see the included guide for other profiles."))
        label(("2. 确认尚未在 profile、home patch 或 --patch 中加载 black-hole-status。"
               "原配置只有 [] 时，才用下方内容替换；已有其他配置时，只合并这一项并保留原内容。"
               if zh else "2. Check that black-hole-status is not already loaded through a profile, home patch or --patch. "
               "Replace [] only when it is the entire empty configuration. Otherwise merge this one item and preserve existing entries."))
        module = plugin_path()
        try:
            snippet = patch_snippet(module)
            available = module.is_file()
        except (OSError, ValueError):
            snippet, available = "", False
        self.snippet = QPlainTextEdit(snippet)
        self.snippet.setReadOnly(True)
        self.snippet.setMinimumHeight(115)
        layout.addWidget(self.snippet)
        if not available:
            label("找不到可用的插件文件，请完整解压发行包。" if zh else
                  "The plugin file is missing or its path is unsupported. Extract the complete release bundle.")
        label(("3. 保存后，支持 live patch 的 web profile 会重新加载；否则重启 DSH。"
               "在黑洞中勾选 DSH，发送一个请求，观察“处理中 → 空闲”。未连接不等于空闲。\n\n"
               "插件路径必须保持有效。卸载时仅删除本插件条目；没有其他条目后才保留 []，不要清空整个文件。"
               if zh else "3. Save the file. A web profile with live patches reloads it; otherwise restart DSH. "
               "Enable the DSH source in the widget, send a request, and check Busy → Idle. Disconnected does not mean idle.\n\n"
               "Keep the plugin at this path. To uninstall, remove only this plugin entry; use [] only if no entries remain. Do not leave an empty file."))
        buttons = QDialogButtonBox()
        self.copy_button = QPushButton("复制配置片段" if zh else "Copy configuration snippet")
        self.copy_button.setEnabled(available)
        self.copy_button.clicked.connect(self.copy_snippet)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        close = buttons.addButton("关闭" if zh else "Close", QDialogButtonBox.RejectRole)
        close.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def copy_snippet(self):
        QApplication.clipboard().setText(self.snippet.toPlainText())


def show_dsh_setup(parent):
    DshSetupDialog(parent, getattr(parent, "_language", "en")).exec()
