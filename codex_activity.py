"""Read only the explicitly bound local Codex rollout's activity metadata.

This is a local-log adapter, not a streaming app-server connection. Message
events mean recorded output activity, not a guarantee of live token streaming.
No conversation text is retained, logged, sent, or written back.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import time

MAX_READ = 1024 * 1024
STALE_SECONDS = 300.0

@dataclass(frozen=True)
class Activity:
    state: str = "unknown"
    label: str = "Codex：未连接"

def find_session(thread_id: str | None, codex_root: Path) -> Path | None:
    if not thread_id or not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", thread_id
    ):
        return None
    try:
        candidates = list((codex_root / "sessions").glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
    except OSError:
        return None

class RolloutActivityReader:
    def __init__(self, path: Path | None):
        self.path = path
        self.offset = 0
        self.identity = None
        self.pending = b""
        self.state = "unknown"
        self.last_event = 0.0
        self.output_until = 0.0
        self.active_turn = False

    def accept(self, record: dict, now: float) -> None:
        kind = record.get("type")
        p = record.get("payload") or {}
        if not isinstance(p, dict):
            return
        timestamp = record.get("timestamp")
        try:
            stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        except (AttributeError, TypeError, ValueError):
            return
        name = p.get("type")
        activity = None
        if kind == "event_msg":
            if name == "task_started":
                self.active_turn = True
                activity = "busy"
            elif name in ("task_complete", "turn_aborted"):
                self.active_turn = False
                activity = "idle"
            elif name == "item_completed":
                item = p.get("item") or {}
                item_type = item.get("type") if isinstance(item, dict) else None
                if item_type == "AgentMessage":
                    self.output_until = max(self.output_until, stamp + 1.6)
                    activity = "busy" if self.active_turn else "idle"
                elif item_type in ("Reasoning", "CommandExecution", "McpToolCall", "FileChange"):
                    self.active_turn = True
                    activity = "busy"
        elif kind == "response_item":
            if name == "message" and p.get("role") == "assistant":
                self.output_until = max(self.output_until, stamp + 1.6)
                if p.get("phase") in ("final_answer", "final"):
                    self.active_turn = False
                    activity = "idle"
                else:
                    self.active_turn = True
                    activity = "busy"
            elif name in ("reasoning", "function_call", "custom_tool_call"):
                self.active_turn = True
                activity = "busy"
        if activity:
            self.state = activity
            self.last_event = max(self.last_event, stamp)

    def poll(self, now: float | None = None) -> Activity:
        now = time.time() if now is None else now
        if self.path is None:
            return Activity()
        try:
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            restart = self.identity != identity or stat.st_size < self.offset
            if restart:
                self.identity = identity
                self.offset = max(0, stat.st_size-MAX_READ)
                self.pending = b""
                self.state = "unknown"
                self.active_turn = False
                self.last_event = self.output_until = 0.0
            if stat.st_size-self.offset > MAX_READ:
                self.offset = stat.st_size-MAX_READ
                self.pending = b""
                restart = True
            if stat.st_size > self.offset:
                with self.path.open("rb") as source:
                    source.seek(self.offset)
                    chunk = source.read(MAX_READ)
                    self.offset += len(chunk)
                if restart and self.offset-len(chunk)>0:
                    chunk = chunk.partition(b"\n")[2]
                parts = (self.pending+chunk).split(b"\n")
                self.pending = parts.pop()
                if len(self.pending)>MAX_READ//2:
                    self.pending=b""
                for line in parts:
                    try:
                        record=json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(record,dict):
                        self.accept(record,now)
        except OSError:
            return Activity("unknown","Codex：状态来源不可用")
        if self.state == "unknown" or (self.active_turn and now-self.last_event>STALE_SECONDS):
            return Activity("unknown","Codex：状态待确认")
        if now < self.output_until:
            return Activity("output","Codex：刚有回复")
        return Activity(self.state, "Codex：处理中" if self.state=="busy" else "Codex：空闲")

class ActivityMonitor:
    """One bounded worker: no rollout parsing or blocking file reads in paintGL."""
    def __init__(self, path: Path | None):
        self.reader=RolloutActivityReader(path)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="codex-activity")
        self.future=None
        self.current=Activity()
        self.closed=False

    def poll(self) -> Activity:
        if self.closed:
            return Activity()
        if self.future is not None and self.future.done():
            try:
                self.current=self.future.result()
            except Exception:
                self.current=Activity("unknown","Codex：状态读取暂不可用")
            self.future=None
        if self.future is None:
            self.future=self.executor.submit(self.reader.poll)
        return self.current

    def close(self) -> None:
        self.closed=True
        self.executor.shutdown(wait=False,cancel_futures=True)
