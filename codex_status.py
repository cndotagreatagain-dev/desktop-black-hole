"""Aggregate local Codex hook records without binding a selected task.

Small records are checked every two seconds by the UI. Rollout tails are only
reconciled for known, live hook sources, on a stop or every 30 seconds, and only
when changed. This covers stop-hook continuations without treating silence as
completion. It cannot observe cloud tasks or a different CODEX_HOME.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal

from codex_status_bridge import (MAX_STATE, MAX_TURNS, SCHEMA, atomic_json,
                                 process_identity, read_small, record_lock,
                                 status_directory)

POLL_INTERVAL_MS = 2000
RECONCILE_SECONDS = 30.0
MAX_SESSIONS = 256
MAX_TAIL = 256 * 1024


@dataclass(frozen=True)
class GlobalActivity:
    state: str = "unknown"
    label: str = "Codex：未连接"
    detail: str = "尚未安装本机 Codex 状态接入。"
    connected: int = 0
    busy: int = 0
    uncertain: int = 0


class GlobalStatusReader:
    def __init__(self, codex_root: Path, *, identity=process_identity):
        self.codex_root = codex_root.resolve()
        self.directory = status_directory(self.codex_root)
        self.identity = identity
        self.cache = {}
        self.tail_cache = {}
        self.next_reconcile = 0.0
        self.record_reads = 0
        self.record_bytes = 0
        self.tail_reads = 0
        self.tail_bytes = 0
        self.last_event_ns = 0

    def _load(self, path: Path) -> dict:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self.cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        value = read_small(path)
        self.record_reads += 1
        self.record_bytes += stat.st_size
        if value.get("version") != SCHEMA or not isinstance(value.get("turns"), dict):
            raise ValueError("Unrecognized status schema")
        if len(value["turns"]) > MAX_TURNS:
            raise ValueError("Too many turns")
        if not isinstance(value.get("updated_ns"), int):
            raise ValueError("Missing event timestamp")
        for turn in value["turns"].values():
            if not isinstance(turn, dict) or not isinstance(turn.get("children", {}), dict):
                raise ValueError("Invalid turn")
            if any(not isinstance(child, dict) for child in turn.get("children", {}).values()):
                raise ValueError("Invalid child")
        self.cache[path] = (stamp, value)
        return value

    def _tail(self, raw_path: str | None) -> list[tuple[int, str, str]]:
        if not isinstance(raw_path, str):
            return []
        # Codex on Windows supplies verbatim drive paths; CODEX_HOME usually
        # does not. Normalize that spelling only, never UNC/device namespaces.
        verbatim = "\\\\?\\"
        if os.name == "nt" and raw_path.startswith(verbatim):
            local = raw_path[len(verbatim):]
            if (len(local) < 3 or not local[0].isascii()
                    or not local[0].isalpha() or local[1:3] != ":\\"):
                return []
            raw_path = local
        path = Path(raw_path)
        # Do not accept arbitrary files, UNC shares, symlink escapes or secrets.
        if not path.is_absolute() or ".." in path.parts or path.suffix != ".jsonl":
            return []
        roots = [self.codex_root / name for name in ("sessions", "archived_sessions")]
        if not any(path.is_relative_to(root) for root in roots):
            return []
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in roots):
            return []
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self.tail_cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        start = max(0, stat.st_size - MAX_TAIL)
        with path.open("rb") as source:
            source.seek(start)
            chunk = source.read(MAX_TAIL)
        self.tail_reads += 1
        self.tail_bytes += len(chunk)
        if start:
            chunk = chunk.partition(b"\n")[2]
        events = []
        # Ignore an incomplete final line. Never retain raw transcript bytes.
        for line in chunk.split(b"\n")[:-1]:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("type") != "event_msg":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                name, turn = payload.get("type"), payload.get("turn_id")
                if name not in ("task_started", "task_complete", "turn_aborted"):
                    continue
                if not isinstance(turn, str) or not 0 < len(turn) <= 160:
                    continue
                stamp_ns = int(datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")).timestamp() * 1e9)
                events.append((stamp_ns, turn, name))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                continue
        events = sorted(events, key=lambda event: event[0])[-MAX_TURNS * 2:]
        self.tail_cache[path] = (stamp, events)
        return events

    def _reconcile(self, path: Path, value: dict) -> dict:
        """Stop is a proposal, not proof: another hook may request continuation."""
        try:
            events = self._tail(value.get("transcript"))
            child_ends = []
            for turn_id, turn in value["turns"].items():
                for child_id, child in turn.get("children", {}).items():
                    if child.get("state") != "stopping":
                        continue
                    child_events = self._tail(child.get("transcript"))
                    # Parent turn_id differs from the child's own turn_id.
                    if child_events and child_events[-1][2] in ("task_complete", "turn_aborted"):
                        end_ns = child_events[-1][0]
                        if end_ns >= child.get("stop_ns", 0) - 2_000_000_000:
                            child_ends.append((turn_id, child_id, child.get("stop_ns")))
            if not events and not child_ends:
                return value
            changes = []
            floor = value.get("registered_ns", value["updated_ns"]) - 2_000_000_000
            virtual = {key: dict(turn) for key, turn in value["turns"].items()}
            for stamp, turn_id, name in events:
                current = virtual.get(turn_id, {})
                known = current.get("state")
                if known is None and (name != "task_started" or stamp < floor):
                    continue
                wanted = "busy" if name == "task_started" else "complete"
                if stamp <= current.get("lifecycle_ns", 0):
                    continue
                if known and (known in ("complete", "interrupted")
                              or (wanted == "busy" and known == "busy")):
                    continue
                if wanted == "busy" and known == "stopping" and stamp <= current.get("stop_ns", stamp):
                    continue
                changes.append((stamp, turn_id, wanted))
                virtual[turn_id] = {**current, "state": wanted, "lifecycle_ns": stamp}
            if not changes and not child_ends:
                return value
            # Acknowledge terminal records in our own small status file only.
            # The lock and turn identity fence concurrent/delayed hook writers.
            with record_lock(path.with_suffix(".lock")):
                latest = read_small(path)
                if latest.get("ended"):
                    return latest
                for stamp, turn_id, wanted in changes:
                    turn = latest["turns"].get(turn_id)
                    if turn is None:
                        if len(latest["turns"]) >= MAX_TURNS:
                            latest["uncertain"] = True
                            continue
                        turn = {"state": "unknown", "children": {}, "updated_ns": stamp}
                        latest["turns"][turn_id] = turn
                    if (turn.get("state") in ("complete", "interrupted")
                            or stamp <= turn.get("lifecycle_ns", 0)):
                        continue
                    continuing = (wanted == "busy" and turn.get("state") == "stopping"
                                  and stamp > turn.get("stop_ns", stamp))
                    if wanted == "complete" or turn.get("state") == "unknown" or continuing:
                        turn["state"] = wanted
                        turn["lifecycle_ns"] = stamp
                        turn["updated_ns"] = max(turn.get("updated_ns", 0), stamp)
                for turn_id, child_id, stop_ns in child_ends:
                    child = latest["turns"].get(turn_id, {}).get("children", {}).get(child_id)
                    if child and child.get("state") == "stopping" and child.get("stop_ns") == stop_ns:
                        child["state"] = "complete"
                atomic_json(path, latest)
            self.cache.pop(path, None)
            return self._load(path)
        except (OSError, ValueError, TypeError, KeyError):
            return value  # Keep busy/unknown; unreadable logs never mean idle.

    def poll(self, now: float | None = None) -> GlobalActivity:
        now = time.monotonic() if now is None else now
        installed = (self.directory / "installation.json").is_file()
        if not installed:
            return GlobalActivity()
        scan = now >= self.next_reconcile
        if scan:
            self.next_reconcile = now + RECONCILE_SECONDS
        paths = []
        uncertain = 0
        try:
            with os.scandir(self.directory / "sessions") as items:
                for item in items:
                    if item.name.endswith(".json") and item.is_file(follow_symlinks=False):
                        if len(paths) >= MAX_SESSIONS:
                            uncertain += 1
                            break
                        paths.append(Path(item.path))
        except FileNotFoundError:
            pass
        except OSError:
            return GlobalActivity(detail="本地状态目录暂时无法读取。")
        connected = busy = 0
        owners = {}
        for path in paths:
            try:
                value = self._load(path)
                self.last_event_ns = max(self.last_event_ns, value["updated_ns"])
                if value.get("ended"):
                    continue
                owner = value.get("owner")
                if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int):
                    uncertain += 1
                    continue
                pid = owner["pid"]
                if pid not in owners:
                    owners[pid] = self.identity(pid)
                if owners[pid] != owner:
                    continue  # Dead or reused source process, not "ready".
                connected += 1
                pending = any(t.get("state") == "stopping" or
                              any(c.get("state") == "stopping" for c in t.get("children", {}).values())
                              for t in value["turns"].values())
                if scan or pending:
                    value = self._reconcile(path, value)
                active = ambiguous = False
                for turn in value["turns"].values():
                    states = [turn.get("state")] + [c.get("state") for c in turn.get("children", {}).values()]
                    active |= "busy" in states
                    ambiguous |= any(s not in ("busy", "complete", "interrupted") for s in states)
                if active:
                    busy += 1
                if ambiguous or value.get("uncertain") or not value["turns"]:
                    uncertain += 1
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                uncertain += 1
        self.cache = {p: entry for p, entry in self.cache.items() if p in paths}
        if len(self.tail_cache) > MAX_SESSIONS * 2:
            self.tail_cache.clear()
        scope = ("本机总状态，不绑定当前任务；不包含云端或其他电脑。\n"
                 "每 2 秒检查小状态文件；思考时保持工作状态。\n"
                 "不判断输入框是否可用，不精确区分思考与逐字回复。")
        if busy:
            return GlobalActivity("busy", "Codex：处理中", scope, connected, busy, uncertain)
        if connected and not uncertain:
            return GlobalActivity("idle", "Codex：空闲", scope, connected, 0, 0)
        if connected or uncertain:
            return GlobalActivity("unknown", "Codex：状态待确认",
                                  scope + "\n收到的状态尚不完整，或正在确认工作是否结束。",
                                  connected, 0, uncertain)
        if self.last_event_ns:
            return GlobalActivity("unknown", "Codex：未连接",
                                  "先前连接的 Codex 进程已结束或无法核实；等待新的事件。\n" + scope)
        return GlobalActivity("unknown", "Codex：等待接入确认",
                              "接入文件已安装，但尚未收到真实事件。\n"
                              "请在 Codex CLI 的 /hooks 中审查 black-hole-status-…py 相关命令。\n"
                              "确认后重启 Codex，再发送一条消息。已有未重载配置的会话不保证被覆盖。")


class GlobalActivityMonitor(QObject):
    activity_ready = Signal(object)

    def __init__(self, codex_root: Path, parent=None, *, reader=None, initial=None):
        super().__init__(parent)
        self.reader = reader if reader is not None else GlobalStatusReader(codex_root)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codex-total")
        self.future = None
        self.current = initial if initial is not None else GlobalActivity()
        self.closed = False

    def _finished(self, future):
        try:
            result = future.result()
        except Exception:
            result = GlobalActivity(label="状态待确认", detail="本地状态读取暂不可用。")
        if not self.closed:
            self.current = result
            try:
                self.activity_ready.emit(result)
            except RuntimeError:
                pass  # Qt may have destroyed the owner during worker shutdown.

    def poll(self) -> GlobalActivity:
        if not self.closed and (self.future is None or self.future.done()):
            self.future = self.executor.submit(self.reader.poll)
            self.future.add_done_callback(self._finished)
        return self.current

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
