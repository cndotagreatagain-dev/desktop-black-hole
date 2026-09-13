"""Explicit opt-in deployment of the frozen, Python-free Codex hook helper."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from codex_status_bridge import EVENTS, SCHEMA, atomic_json, status_directory
from tools.setup_codex_status import load_config, remove_owned


def bundle_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('信使文件夹不能包含符号链接。')
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(b'\0')
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024*1024), b''):
                    digest.update(block)
    return digest.hexdigest()


def install_bundle(root: Path, bundle: Path, *, uninstall=False) -> dict:
    root, bundle = root.resolve(), bundle.resolve()
    directory = status_directory(root)
    manifest_path = directory/'installation.json'
    previous = json.loads(manifest_path.read_text(encoding='utf8')) if manifest_path.exists() else {}
    original = load_config(root)
    config = remove_owned(original, previous)
    if not uninstall:
        if not (bundle/'BlackHoleStatus.exe').is_file() or not (bundle/'_internal').is_dir():
            raise ValueError('找不到完整的信使组件，请解压整个发布文件夹。')
        digest = bundle_digest(bundle)
        destination = directory/'bridge'/('black-hole-status-'+digest[:16])
        helper = destination/'BlackHoleStatus.exe'
        command = subprocess.list2cmdline([str(helper), '--directory', str(directory)])
        handlers = {}
        for event in EVENTS:
            handler = dict(type='command', command=command, timeout=3, **{'async': event != 'SessionEnd'})
            handlers[event] = handler
            groups = config.setdefault('hooks', {}).setdefault(event, [])
            if not isinstance(groups, list):
                raise ValueError('现有钩子格式不支持安全合并，没有修改。')
            groups.append({'hooks': [handler]})
        if destination.exists():
            if bundle_digest(destination) != digest:
                raise ValueError('已安装的信使内容异常，拒绝覆盖。')
            if previous.get('handlers') == handlers and load_config(root) == config:
                return dict(installed=True, changed=False)
        manifest = dict(version=SCHEMA, handlers=handlers, helper=str(helper), sha256=digest,
                        runtime='bundled', installed_at=datetime.now().isoformat(timespec='seconds'))
    backup = directory/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, exist_ok=False)
    for name, path in (('hooks.json', root/'hooks.json'), ('installation.json', manifest_path)):
        if path.exists():
            shutil.copy2(path, backup/name)
    if uninstall:
        atomic_json(root/'hooks.json', config)
        if manifest_path.exists():
            manifest_path.unlink()
        return dict(installed=False, backup=str(backup))
    if not destination.exists():
        shutil.copytree(bundle, destination)
    if bundle_digest(destination) != digest:
        raise ValueError('信使复制校验失败，没有启用钩子。')
    atomic_json(root/'hooks.json', config)
    atomic_json(manifest_path, manifest)
    return dict(installed=True, changed=True, backup=str(backup))


def show_setup(parent, uninstall=False):
    from PySide6.QtWidgets import QMessageBox
    english = getattr(parent, '_language', 'en') == 'en'
    root = status_directory().parent.parent
    title = '移除 Codex 接入' if uninstall else '安装 Codex 接入（无需 Python）'
    text = (f'目标：{root}\n\n只修改本黑洞拥有的 8 个状态钩子，并先备份。'
            '\n不读取密钥，不调用模型，不修改审批规则，不自动信任钩子。'
            '\n移除时保留备份和旧信使文件，以便恢复。' if uninstall else
            f'目标：{root}\n\n将安装本地状态信使和 8 个状态钩子，并先备份。'
            '\n仅记录进程及工作状态，不调用模型。'
            '\n安装后需你在 Codex /hooks 中审查并信任，随后重启 Codex。'
            '\n当前已连接的用户不必重新安装。')
    if english:
        title = 'Remove Codex integration' if uninstall else 'Install Codex integration (no Python required)'
        text = (f'Target: {root}\n\n'
                + ('Remove only the eight status hooks owned by this app. Back up first; keep old helper files for recovery.'
                   if uninstall else
                   'Install the local status helper and eight hooks. Back up first. Review and trust these commands in Codex /hooks, then restart Codex. If already connected, reinstalling is not necessary.')
                + '\nNo model calls, key access, approval-rule changes, or automatic hook trust.')
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    accept_label = ('Remove' if uninstall else 'Install') if english else ('确认移除' if uninstall else '确认安装')
    accept = box.addButton(accept_label, QMessageBox.AcceptRole)
    box.addButton('Cancel' if english else '取消', QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is not accept:
        return
    try:
        result = install_bundle(root, Path(sys.executable).parent/'status-helper', uninstall=uninstall)
        message = ('已移除本黑洞的钩子，其他钩子和旧文件保留。请重启 Codex。' if uninstall else
                   '接入文件已就绪。请到 Codex 的 /hooks 中审查 black-hole-status 相关命令，信任后重启。')
        if english:
            message = ('This app\'s hooks were removed. Other hooks and old files were preserved. Restart Codex.' if uninstall else
                       'Integration files are ready. Review the black-hole-status commands in Codex /hooks, trust them if appropriate, then restart Codex.')
        if result.get('backup'):
            message += ('\nBackup: ' if english else '\n备份：')+result['backup']
        QMessageBox.information(parent, title, message)
    except Exception as error:
        message = str(error)
        if english:
            message = {
                '信使文件夹不能包含符号链接。': 'The helper folder must not contain symbolic links.',
                '找不到完整的信使组件，请解压整个发布文件夹。': 'Helper components are missing. Extract the complete release folder.',
                '现有钩子格式不支持安全合并，没有修改。': 'The existing hook format cannot be merged safely. No changes were made.',
                '已安装的信使内容异常，拒绝覆盖。': 'The installed helper differs from the expected bundle. Refusing to overwrite it.',
                '信使复制校验失败，没有启用钩子。': 'Helper verification failed. Hooks were not enabled.',
            }.get(message, message)
        QMessageBox.warning(parent, title, message)
