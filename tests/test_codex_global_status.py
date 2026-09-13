from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

import codex_status_bridge as bridge
from codex_status import GlobalStatusReader, GlobalActivityMonitor, MAX_TAIL

NOW = 1_800_000_000_000_000_000
OWNER = {"pid": 1234, "birth": "5678", "name": "codex.exe"}


@pytest.fixture
def root(tmp_path):
    directory = bridge.status_directory(tmp_path)
    bridge.atomic_json(directory / "installation.json", {"version": 1})
    return tmp_path


def emit(root, name, *, session="session-a", turn="turn-a", owner=OWNER, stamp=NOW, **extra):
    payload = {"hook_event_name": name, "session_id": session, "turn_id": turn, **extra}
    return bridge.report(payload, bridge.status_directory(root), owner, stamp)


def reader(root, owner=OWNER):
    return GlobalStatusReader(root, identity=lambda pid: owner if pid == owner["pid"] else None)


def rollout(root, name="rollout-test.jsonl"):
    path = root / "sessions" / "2026" / "09" / "10" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def log(path, event, turn="turn-a", stamp=NOW + 1_000_000_000):
    record = {"type": "event_msg", "timestamp": datetime.fromtimestamp(stamp/1e9, timezone.utc).isoformat(),
              "payload": {"type": event, "turn_id": turn}}
    with path.open("ab") as output:
        output.write(json.dumps(record).encode() + b"\n")


def test_uninstalled_and_installed_but_untrusted_are_not_idle(tmp_path, root):
    fresh = tmp_path / "not-installed"
    assert reader(fresh).poll().label == "Codex：未连接"
    state = reader(root).poll()
    assert state.state == "unknown"
    assert state.label == "Codex：等待接入确认"
    assert "/hooks" in state.detail


def test_long_thinking_stays_busy_and_unchanged_files_are_not_reread(root):
    emit(root, "UserPromptSubmit", prompt="PRIVATE TEXT")
    source = reader(root)
    assert source.poll(0).state == "busy"
    reads, size = source.record_reads, source.record_bytes
    for now in (2, 4, 30, 300, 3600, 18000):
        assert source.poll(now).state == "busy"
    assert (source.record_reads, source.record_bytes) == (reads, size)
    assert source.tail_reads == 0
    assert "PRIVATE" not in repr(source.cache)


def test_stop_requires_real_completion_and_result_survives_widget_restart(root):
    path = rollout(root)
    emit(root, "UserPromptSubmit", transcript_path=str(path))
    emit(root, "Stop", stamp=NOW+500_000_000, transcript_path=str(path))
    source = reader(root)
    assert source.poll(0).state == "unknown"
    log(path, "task_complete")
    assert source.poll(2).state == "idle"
    assert reader(root).poll(0).state == "idle"
    assert source.tail_reads == 2


def test_one_task_completes_while_another_still_runs(root):
    path = rollout(root)
    emit(root, "UserPromptSubmit", transcript_path=str(path))
    emit(root, "UserPromptSubmit", session="session-b", turn="turn-b")
    emit(root, "Stop", transcript_path=str(path))
    log(path, "task_complete")
    result = reader(root).poll(0)
    assert result.state == "busy"
    assert result.connected == 2 and result.busy == 1


def test_overlapping_turns_and_delayed_stop_cannot_clear_new_work(root):
    path = rollout(root)
    emit(root, "UserPromptSubmit", transcript_path=str(path))
    emit(root, "UserPromptSubmit", turn="turn-b", stamp=NOW+3_000_000_000)
    emit(root, "Stop", stamp=NOW+4_000_000_000)
    log(path, "task_complete")
    assert reader(root).poll().state == "busy"


def test_delayed_begin_cannot_undo_interrupt(root):
    emit(root, "Interrupt")
    emit(root, "UserPromptSubmit", stamp=NOW-1)
    assert reader(root).poll().state == "idle"


def test_delayed_begin_cannot_undo_confirmed_completion(root):
    path = rollout(root)
    emit(root, "Stop", transcript_path=str(path))
    log(path, "task_complete")
    assert reader(root).poll().state == "idle"
    emit(root, "UserPromptSubmit", stamp=NOW-1)
    assert reader(root).poll().state == "idle"


def test_compaction_and_session_start_do_not_clear_busy(root):
    emit(root, "UserPromptSubmit")
    emit(root, "SessionStart", source="compact")
    emit(root, "PreCompact")
    assert reader(root).poll().state == "busy"


def test_new_session_start_is_unknown_not_ready(root):
    emit(root, "SessionStart", turn=None, source="startup")
    assert reader(root).poll().state == "unknown"


def test_process_exit_and_pid_reuse_do_not_leave_a_connected_lamp(root):
    emit(root, "UserPromptSubmit")
    source = GlobalStatusReader(root, identity=lambda pid: None)
    assert source.poll().label == "Codex：未连接"
    source.identity = lambda pid: {**OWNER, "birth": "different"}
    assert source.poll().label == "Codex：未连接"
    source.identity = lambda pid: OWNER
    assert source.poll().state == "busy"


def test_unknown_process_identity_cannot_claim_connection(root):
    emit(root, "UserPromptSubmit", owner=None)
    state = reader(root).poll()
    assert state.state == "unknown" and state.connected == 0


def test_session_end_blocks_delayed_hooks_but_later_resume_can_reconnect(root):
    emit(root, "UserPromptSubmit")
    emit(root, "SessionEnd")
    emit(root, "UserPromptSubmit", turn="delayed", stamp=NOW+1)
    assert reader(root).poll().label == "Codex：未连接"
    emit(root, "SessionStart", source="resume", stamp=NOW+10_000_000_000)
    emit(root, "UserPromptSubmit", turn="new", stamp=NOW+11_000_000_000)
    assert reader(root).poll().state == "busy"


def test_child_keeps_total_busy_after_parent_interrupt(root):
    emit(root, "UserPromptSubmit")
    emit(root, "SubagentStart", agent_id="child")
    emit(root, "Interrupt")
    assert reader(root).poll().state == "busy"
    path = rollout(root, "rollout-child.jsonl")
    log(path, "task_complete", "child-own-turn")
    emit(root, "SubagentStop", agent_id="child", agent_transcript_path=str(path))
    assert reader(root).poll().state == "idle"


def test_completed_parent_with_unconfirmed_child_does_not_show_idle(root):
    emit(root, "SubagentStop", agent_id="child")
    emit(root, "Interrupt")
    assert reader(root).poll().state == "unknown"


def test_malformed_and_oversized_records_are_unknown(root):
    emit(root, "Interrupt")
    state_file = next((bridge.status_directory(root)/"sessions").glob("*.json"))
    state_file.write_bytes(b"{broken")
    assert reader(root).poll().state == "unknown"
    state_file.write_bytes(b"x" * (bridge.MAX_STATE + 1))
    assert reader(root).poll().state == "unknown"


def test_metadata_allowlist_and_hashed_filenames(root):
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "../../outside",
               "turn_id": "turn", "prompt": "PRIVATE", "last_assistant_message": "PRIVATE",
               "tool_input": {"password": "PRIVATE"}, "cwd": "PRIVATE"}
    assert bridge.report(payload, bridge.status_directory(root), OWNER, NOW)
    files = list((bridge.status_directory(root)/"sessions").glob("*.json"))
    assert len(files) == 1 and len(files[0].stem) == 64
    assert "PRIVATE" not in files[0].read_text()
    assert not bridge.report({"hook_event_name":"Unknown", "session_id":"a"},
                             bridge.status_directory(root), OWNER, NOW)


def test_concurrent_writers_do_not_lose_distinct_turns(root):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: emit(root, "UserPromptSubmit", turn=f"turn-{i}"), range(24)))
    assert all(results)
    value = bridge.read_small(next((bridge.status_directory(root)/"sessions").glob("*.json")))
    assert len(value["turns"]) == 24


def test_completion_confirmation_never_reads_outside_codex_session_dirs(root, tmp_path):
    secret = tmp_path / "auth.jsonl"
    secret.write_text("PRIVATE")
    emit(root, "Stop", transcript_path=str(secret))
    source = reader(root)
    assert source.poll().state == "unknown"
    assert source.tail_reads == 0


def test_large_transcript_tail_is_bounded_and_only_lifecycle_metadata_cached(root):
    path = rollout(root)
    path.write_bytes(b"PRIVATE" * MAX_TAIL + b"\n")
    log(path, "task_complete")
    emit(root, "Stop", transcript_path=str(path))
    source = reader(root)
    assert source.poll(0).state == "idle"
    assert source.tail_bytes <= MAX_TAIL
    assert "PRIVATE" not in repr(source.tail_cache)
    before = source.tail_reads
    source.poll(30)
    assert source.tail_reads == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows verbatim drive paths")
def test_windows_verbatim_log_path_confirms_real_completion(root):
    path = rollout(root)
    raw_path = "\\\\?\\" + str(path.resolve())
    emit(root, "UserPromptSubmit", transcript_path=raw_path)
    emit(root, "Stop", transcript_path=raw_path)
    log(path, "task_complete")
    source = reader(root)
    assert source.poll(0).state == "idle"
    assert source.tail_reads == 1
    assert source._tail(str(path)) == source._tail(raw_path)
    assert source.tail_reads == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows namespace guards")
def test_windows_verbatim_paths_cannot_escape_session_directories(root):
    path = rollout(root)
    secret = root / "private.jsonl"
    secret.write_text("PRIVATE")
    source = reader(root)
    raw_paths = ["\\\\?\\" + str(secret.resolve()),
                 "\\\\?\\" + str(path.parent) + "\\..\\..\\..\\..\\private.jsonl",
                 "\\\\?\\UNC\\server\\share\\sessions\\test.jsonl",
                 "\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\test.jsonl",
                 "\\\\.\\C:\\sessions\\test.jsonl"]
    for raw in raw_paths:
        assert source._tail(raw) == []
    assert source.tail_reads == 0


def test_slow_reconciliation_recovers_a_missing_start_hook(root):
    path = rollout(root)
    emit(root, "Interrupt", transcript_path=str(path))
    source = reader(root)
    assert source.poll(0).state == "idle"
    log(path, "task_started", "goal-continuation", NOW+5_000_000_000)
    assert source.poll(30).state == "busy"
    log(path, "task_complete", "goal-continuation", NOW+6_000_000_000)
    assert source.poll(60).state == "idle"


def test_hook_subprocess_never_prints_context_or_imports_gui(tmp_path):
    helper = Path(bridge.__file__)
    payload = {"hook_event_name":"UserPromptSubmit", "session_id":"test", "turn_id":"test",
               "prompt":"PRIVATE"}
    result = subprocess.run([sys.executable, "-I", str(helper), "--directory", str(tmp_path)],
                            input=json.dumps(payload), capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout == "{}\n" and result.stderr == ""
    assert "PySide6" not in helper.read_text() and "requests" not in helper.read_text()
    broken = subprocess.run([sys.executable, "-I", str(helper), "--directory", str(tmp_path)],
                            input="not JSON", capture_output=True, text=True, timeout=5)
    assert broken.returncode == 0 and broken.stdout == "{}\n" and broken.stderr == ""


def test_reconciliation_sees_start_and_end_in_the_same_poll(root):
    path = rollout(root)
    emit(root, "Interrupt", transcript_path=str(path))
    log(path, "task_started", "quick-turn", NOW+5_000_000_000)
    log(path, "task_complete", "quick-turn", NOW+6_000_000_000)
    source = reader(root)
    assert source.poll(0).state == "idle"
    assert source.poll(30).state == "idle"


def test_authoritative_new_start_after_stop_resumes_busy(root):
    path = rollout(root)
    log(path, "task_started", stamp=NOW)
    emit(root, "Stop", stamp=NOW+1_000_000_000, transcript_path=str(path))
    source = reader(root)
    assert source.poll(0).state == "unknown"
    log(path, "task_started", stamp=NOW+3_000_000_000)
    assert source.poll(2).state == "busy"
    log(path, "task_complete", stamp=NOW+4_000_000_000)
    assert source.poll(30).state == "idle"


def test_many_completed_turns_do_not_fill_the_record(root):
    path = rollout(root)
    source = reader(root)
    for index in range(bridge.MAX_TURNS + 20):
        turn = f"turn-{index}"
        stamp = NOW + index * 10_000_000_000
        emit(root, "UserPromptSubmit", turn=turn, stamp=stamp, transcript_path=str(path))
        assert source.poll(index*4).state == "busy"
        emit(root, "Stop", turn=turn, stamp=stamp+1_000_000_000)
        log(path, "task_complete", turn, stamp+2_000_000_000)
        assert source.poll(index*4+2).state == "idle"
    value = bridge.read_small(next((bridge.status_directory(root)/"sessions").glob("*.json")))
    assert len(value["turns"]) == bridge.MAX_TURNS
    assert not value.get("uncertain")


def load_setup():
    spec = importlib.util.spec_from_file_location("status_setup", Path(__file__).resolve().parents[1]/"tools/setup_codex_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_preserves_other_hooks_is_idempotent_and_reversible(tmp_path):
    setup = load_setup()
    original = {"description":"user config", "hooks":{"Stop":[{"hooks":[{"type":"command", "command":"my-own-tool"}]}]}}
    hook_file = tmp_path/"hooks.json"
    hook_file.write_text(json.dumps(original))
    first = setup.install(tmp_path, Path(sys.executable))
    assert first["installed"] and first["changed"]
    installed = json.loads(hook_file.read_text())
    assert installed["hooks"]["Stop"][0] == original["hooks"]["Stop"][0]
    for event in bridge.EVENTS:
        command = installed["hooks"][event][-1]["hooks"][0]
        assert command["timeout"] == 3
        assert command["async"] == (event != "SessionEnd")
    second = setup.install(tmp_path, Path(sys.executable))
    assert not second["changed"]
    setup.install(tmp_path, Path(sys.executable), uninstall=True)
    assert json.loads(hook_file.read_text()) == original
    assert not (bridge.status_directory(tmp_path)/"installation.json").exists()


@pytest.mark.parametrize("config", ["[features]\nhooks=false\n", "allow_managed_hooks_only=true\n"])
def test_installer_never_bypasses_disabled_hooks_or_policy(tmp_path, config):
    (tmp_path/"config.toml").write_text(config)
    with pytest.raises(ValueError):
        load_setup().install(tmp_path, Path(sys.executable))
    assert not (tmp_path/"hooks.json").exists()


def test_monitor_closes_one_worker_without_a_new_poll(root):
    monitor = GlobalActivityMonitor(root)
    monitor.poll()
    monitor.future.result(timeout=3)
    monitor.close()
    before = monitor.future
    monitor.poll()
    assert monitor.closed and monitor.future is before
