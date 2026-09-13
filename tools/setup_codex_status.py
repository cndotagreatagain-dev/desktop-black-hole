"""Install/remove only this widget's local Codex command hooks, backup first.

This never trusts hooks, changes approval settings, reads credentials or starts
an agent. The user must review the installed commands through Codex /hooks.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_status_bridge import EVENTS, SCHEMA, atomic_json, status_directory


def load_config(root: Path) -> dict:
    for name in ("config.toml", "requirements.toml"):
        path = root / name
        if path.is_file():
            with path.open("rb") as source:
                config = tomllib.load(source)
            features = config.get("features", {})
            if features.get("hooks", features.get("codex_hooks", True)) is False:
                raise ValueError("Codex hooks are disabled; no security setting was changed.")
            if config.get("allow_managed_hooks_only") is True:
                raise ValueError("Only managed hooks are permitted; installation stopped.")
    path = root / "hooks.json"
    if not path.exists():
        return {"hooks": {}}
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Existing hook configuration is too large for safe automatic merging.")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError("Existing hooks.json has an unsupported shape; it was not changed.")
    return value


def remove_owned(config: dict, previous: dict) -> dict:
    """Match complete prior handler objects, never a broad substring or event."""
    owned = previous.get("handlers", {})
    for event in list(config.get("hooks", {})):
        groups = config["hooks"][event]
        if not isinstance(groups, list):
            continue
        retained = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                retained.append(group)
                continue
            handlers = [handler for handler in group["hooks"] if handler != owned.get(event)]
            if handlers == group["hooks"]:
                retained.append(group)
            elif handlers:
                retained.append({**group, "hooks": handlers})
        if retained:
            config["hooks"][event] = retained
        else:
            config["hooks"].pop(event)
    return config


def install(root: Path, python: Path, *, uninstall=False) -> dict:
    root = root.resolve()
    directory = status_directory(root)
    manifest_path = directory / "installation.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    config = load_config(root)
    config = remove_owned(config, previous)
    backup = directory / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if not uninstall:
        python = python.resolve(strict=True)
        if python.name.lower() != "python.exe":
            raise ValueError("Select a real python.exe, not the Windows launcher or pythonw.exe.")
        source = Path(__file__).resolve().parents[1] / "codex_status_bridge.py"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        # A content-addressed path makes a changed helper a new trust definition.
        helper = directory / "bridge" / ("black-hole-status-" + digest[:16] + ".py")
        command = subprocess.list2cmdline([str(python), "-I", str(helper),
                                          "--directory", str(directory)])
        handlers = {}
        for event in EVENTS:
            handler = {"type": "command", "command": command,
                       "timeout": 3, "async": event != "SessionEnd"}
            handlers[event] = handler
            groups = config.setdefault("hooks", {}).setdefault(event, [])
            if not isinstance(groups, list):
                raise ValueError("Unsupported existing hook group; not changed.")
            groups.append({"hooks": [handler]})
        manifest = {"version": SCHEMA, "handlers": handlers, "helper": str(helper),
                    "sha256": digest, "python": str(python),
                    "installed_at": datetime.now().isoformat(timespec="seconds")}
        if (previous.get("handlers") == handlers and helper.is_file()
                and hashlib.sha256(helper.read_bytes()).hexdigest() == digest
                and load_config(root) == config):
            return {"installed": True, "changed": False, "directory": str(directory),
                    "trust_required": "Review in Codex /hooks; this tool never grants trust."}
    backup.mkdir(parents=True, exist_ok=False)
    hook_path = root / "hooks.json"
    if hook_path.exists():
        shutil.copy2(hook_path, backup / "hooks.json")
    if manifest_path.exists():
        shutil.copy2(manifest_path, backup / "installation.json")
    if uninstall:
        atomic_json(hook_path, config)
        if manifest_path.exists():
            manifest_path.unlink()
        return {"installed": False, "backup": str(backup), "retained": "unrelated hooks and status history"}
    helper.parent.mkdir(parents=True, exist_ok=True)
    if helper.exists() and hashlib.sha256(helper.read_bytes()).hexdigest() != digest:
        raise ValueError("Installed helper hash mismatch; refusing to overwrite.")
    if not helper.exists():
        shutil.copy2(source, helper)
    atomic_json(hook_path, config)
    atomic_json(manifest_path, manifest)
    return {"installed": True, "changed": True, "backup": str(backup),
            "directory": str(directory), "helper": str(helper), "events": list(EVENTS),
            "trust_required": "Review in Codex /hooks; this tool never grants trust."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-root", type=Path, default=None)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    root = args.codex_root or status_directory().parent.parent
    print(json.dumps(install(root, args.python, uninstall=args.uninstall), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
