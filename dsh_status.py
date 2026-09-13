"""Optional, metadata-only local DSH status reader. No network or transcripts."""
from __future__ import annotations

import os
from pathlib import Path
import time

from codex_status import GlobalActivity, GlobalActivityMonitor
from codex_status_bridge import process_identity, read_small

MAX_SOURCES = 64
HEALTH_SECONDS = 35


def status_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()/"AppData"/"Local") / "DesktopBlackHole" / "status" / "dsh-v1"


def disconnected() -> GlobalActivity:
    return GlobalActivity(label="DSH：未连接", detail=(
        "DSH 是可选来源，需在 DeepSeek Harness 中加载随附的本地状态插件。\n"
        "没有安装 DSH 不影响黑洞。启用此开关不会安装或启动 DSH。\n"
        "只读取本机插件输出的进程和忙闲计数，不读取聊天内容，不调用模型。"))


class DshStatusReader:
    def __init__(self, directory=None, *, identity=process_identity, clock=time.time):
        self.directory = Path(directory or status_directory()).resolve()
        self.identity, self.clock = identity, clock
        self.cache = {}
        self.record_reads = 0

    def poll(self) -> GlobalActivity:
        connected = busy = uncertain = 0
        try:
            paths = []
            for path in self.directory.glob("*.json"):
                paths.append(path)
                if len(paths) > MAX_SOURCES:
                    return GlobalActivity(label="DSH：状态待确认", detail="本地状态来源超过安全上限。", uncertain=1)
            for path in paths:
                try:
                    if path.is_symlink() or path.resolve().parent != self.directory:
                        raise ValueError("Status path escaped its directory")
                    stat = path.stat()
                    stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                    cached = self.cache.get(path)
                    if cached is not None and cached[0] == stamp:
                        value = cached[1]
                    else:
                        value = read_small(path)
                        self.record_reads += 1
                        self.cache[path] = (stamp, value)
                    if value.get("version") != 1 or value.get("source") != "dsh":
                        raise ValueError("Unknown protocol")
                    for key in ("pid", "started_ms", "updated_ms", "agents", "running", "unknown"):
                        if type(value.get(key)) is not int or value[key] < 0:
                            raise ValueError("Invalid metadata")
                    if value["running"] + value["unknown"] > value["agents"] or value["agents"] > 100000:
                        raise ValueError("Invalid counts")
                    # Dead/reused PIDs never revive an old idle/busy record.
                    owner = self.identity(value["pid"])
                    if owner is None:
                        continue
                    birth_ms = int(owner["birth"]) // 10000 - 11644473600000
                    # Node process.uptime() starts just after OS process creation.
                    if abs(birth_ms-value["started_ms"]) > 2000:
                        continue
                    age = self.clock() - value["updated_ms"]/1000
                    if not -2 <= age <= HEALTH_SECONDS:
                        uncertain += 1
                        continue
                    connected += 1
                    busy += int(value["running"] > 0)
                    uncertain += int(value["unknown"] > 0)
                except (OSError, ValueError, KeyError, TypeError, OverflowError):
                    uncertain += 1
            self.cache = {p: v for p, v in self.cache.items() if p in paths}
        except OSError:
            uncertain += 1
        detail = ("DSH 本机总状态：包含加载了插件的各个实例及其子任务。\n"
                  "事件变化即写入；10 秒一次健康心跳；黑洞每 2 秒检查小文件，未变化不重读。\n"
                  "不包含未加载插件、WSL、云端或其他电脑的实例；不区分思考与回复。")
        if busy:
            return GlobalActivity("busy", "DSH：处理中", detail, connected, busy, uncertain)
        if connected and not uncertain:
            return GlobalActivity("idle", "DSH：空闲", detail, connected)
        if uncertain:
            return GlobalActivity("unknown", "DSH：状态待确认", detail+"\n插件心跳过期或记录无法核实。", connected, 0, uncertain)
        return disconnected()


class DshActivityMonitor(GlobalActivityMonitor):
    def __init__(self, parent=None, directory=None):
        super().__init__(Path(), parent, reader=DshStatusReader(directory), initial=disconnected())


def aggregate_sources(sources: list[GlobalActivity]) -> GlobalActivity:
    if not sources:
        return GlobalActivity(label="状态灯：未启用来源", detail="在右键“状态来源”中选择 Codex 或 DSH。")
    if len(sources) == 1:
        return sources[0]
    state = "busy" if any(s.state == "busy" for s in sources) else (
        "idle" if all(s.state == "idle" for s in sources) else "unknown")
    title = {"busy": "处理中", "idle": "空闲", "unknown": "状态待确认"}[state]
    detail = "\n\n".join(s.label+"\n"+s.detail for s in sources)
    return GlobalActivity(state, "总状态："+title, detail,
                          sum(s.connected for s in sources), sum(s.busy for s in sources),
                          sum(s.uncertain or int(s.state == "unknown") for s in sources))
