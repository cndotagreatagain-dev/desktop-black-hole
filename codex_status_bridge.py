"""Metadata-only local Codex hook. No Qt, network, model calls or context output.

Codex may supply conversation content on stdin; only an explicit allowlist is
saved. The helper is also deployed as a standalone file outside this checkout.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

SCHEMA = 1
MAX_INPUT = 8 * 1024 * 1024
MAX_STATE = 64 * 1024
MAX_TURNS = 64
MAX_CHILDREN = 64
EVENTS = ("SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "Interrupt",
          "SessionEnd", "SubagentStart", "SubagentStop")


def status_directory(codex_root: Path | None = None) -> Path:
    root = codex_root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return root / "black-hole-status" / "v1"


def _short(value, limit=160):
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def metadata(payload: dict) -> dict | None:
    name, session = payload.get("hook_event_name"), _short(payload.get("session_id"))
    if name not in EVENTS or not session:
        return None
    result = {"event": name, "session": session}
    for source, target in (("turn_id", "turn"), ("agent_id", "agent")):
        if value := _short(payload.get(source)):
            result[target] = value
    if payload.get("source") in ("startup", "resume", "clear", "compact"):
        result["source"] = payload["source"]
    # Paths are only used for bounded, read-only lifecycle reconciliation.
    for source, target in (("transcript_path", "transcript"),
                           ("agent_transcript_path", "agent_transcript")):
        if value := _short(payload.get(source), 2048):
            result[target] = value
    return result


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    return api


def process_identity(pid: int) -> dict | None:
    """A reused PID cannot revive old work: verify its creation timestamp too."""
    if sys.platform != "win32" or not isinstance(pid, int) or pid <= 0:
        return None
    api = _kernel()
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in stamps)):
            return None
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        if not api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            return None
        birth = (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
        return {"pid": pid, "birth": str(birth), "name": Path(name.value).name.lower()}
    finally:
        api.CloseHandle(handle)


def find_owner() -> dict | None:
    """Locate the real Codex ancestor, not the short-lived Python/shell helper."""
    if sys.platform != "win32":
        return None

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    api = _kernel()
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        return None
    parents = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = api.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
            ok = api.Process32NextW(handle, ctypes.byref(entry))
    finally:
        api.CloseHandle(handle)
    pid, seen = os.getpid(), set()
    for _ in range(16):
        if pid in seen or pid not in parents:
            break
        seen.add(pid)
        parent, name = parents[pid]
        if name == "codex.exe":
            return process_identity(pid)
        pid = parent
    return None


def read_small(path: Path) -> dict:
    with path.open("rb") as source:
        raw = source.read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise ValueError("Oversized status record")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid status record")
    return value


def atomic_json(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    if len(raw) > MAX_STATE:
        raise ValueError("Oversized status record")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.015)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def record_lock(path: Path):
    """OS lock, automatically released if a writer is killed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 0.6
        while True:
            handle.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Status writer busy")
                time.sleep(0.01)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reduce_event(previous: dict, event: dict, owner: dict | None, stamp: int) -> dict:
    value = copy.deepcopy(previous)
    value.update(version=SCHEMA, session=event["session"], owner=owner)
    value.setdefault("registered_ns", stamp)
    value["updated_ns"] = max(value.get("updated_ns", 0), stamp)
    value["last_event"] = event["event"]
    turns = value.setdefault("turns", {})
    if event.get("transcript"):
        value["transcript"] = event["transcript"]
    name = event["event"]
    if name == "SessionEnd":
        value["ended"] = True
        value["ended_ns"] = stamp
        return value
    if value.get("ended"):
        if (name == "SessionStart" and event.get("source") in ("startup", "resume")
                and stamp > value.get("ended_ns", stamp) + 3_000_000_000):
            return reduce_event({}, event, owner, stamp)
        return value  # A delayed background hook must not revive a dead session.
    if name == "SessionStart":
        return value  # Startup/resume/compaction is not evidence of idle.
    turn_id = event.get("turn")
    if not turn_id:
        value["uncertain"] = True
        return value
    if turn_id not in turns:
        if len(turns) >= MAX_TURNS:
            completed = sorted((t.get("updated_ns", 0), key) for key, t in turns.items()
                               if t.get("state") in ("complete", "interrupted")
                               and not any(c.get("state") != "complete"
                                           for c in t.get("children", {}).values()))
            if completed:
                del turns[completed[0][1]]
            else:
                value["uncertain"] = True
                return value
        turns[turn_id] = {"state": "unknown", "children": {}, "updated_ns": stamp}
    turn = turns[turn_id]
    turn["updated_ns"] = max(turn.get("updated_ns", 0), stamp)
    if name in ("SubagentStart", "SubagentStop"):
        agent = event.get("agent")
        if not agent:
            value["uncertain"] = True
            return value
        children = turn.setdefault("children", {})
        if agent not in children and len(children) >= MAX_CHILDREN:
            value["uncertain"] = True
            return value
        child = children.setdefault(agent, {"state": "busy"})
        if event.get("agent_transcript"):
            child["transcript"] = event["agent_transcript"]
        if name == "SubagentStop" and child["state"] != "complete":
            child["state"], child["stop_ns"] = "stopping", stamp
    elif name == "Interrupt":
        turn["state"] = "interrupted"
    elif name == "Stop":
        if turn["state"] not in ("interrupted", "complete"):
            turn["state"], turn["stop_ns"] = "stopping", stamp
    elif turn["state"] not in ("stopping", "interrupted", "complete"):
        turn["state"] = "busy"
    return value


def report(payload: dict, directory: Path, owner: dict | None, stamp: int | None = None) -> bool:
    event = metadata(payload)
    if event is None:
        return False
    stamp = time.time_ns() if stamp is None else stamp
    key = hashlib.sha256(json.dumps([event["session"], owner], sort_keys=True).encode()).hexdigest()
    path = directory / "sessions" / (key + ".json")
    with record_lock(path.with_suffix(".lock")):
        try:
            previous = read_small(path)
        except FileNotFoundError:
            previous = {}
        except (ValueError, OSError):
            previous = {"uncertain": True}
        atomic_json(path, reduce_event(previous, event, owner, stamp))
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) <= MAX_INPUT:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                report(payload, args.directory, find_owner())
    except Exception:
        # Never fail a Codex turn or add text to its model context.
        pass
    if sys.stdout is not None:
        sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
