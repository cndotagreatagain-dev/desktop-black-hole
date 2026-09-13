import json
from dataclasses import replace

import pytest
from codex_status import GlobalActivity
from dsh_status import DshStatusReader, aggregate_sources

NOW = 2000000
BIRTH = 1900000000


def record(path, **changes):
    value = dict(version=1, source='dsh', pid=123, started_ms=BIRTH,
                 updated_ms=NOW*1000, agents=2, running=0, unknown=0)
    value.update(changes)
    path.write_text(json.dumps(value))


def reader(path, **kwargs):
    return DshStatusReader(path, clock=lambda: NOW,
        identity=kwargs.get('identity', lambda pid: {
            'pid': pid, 'birth': str((BIRTH+11644473600000)*10000), 'name': 'node.exe'}))


def test_missing_dsh_stays_disconnected_without_creating_files(tmp_path):
    directory = tmp_path/'absent'
    assert reader(directory).poll().state == 'unknown'
    assert not directory.exists()


def test_event_busy_idle_and_unchanged_cache(tmp_path):
    path = tmp_path/'a.json'
    record(path, running=1)
    source = reader(tmp_path)
    assert source.poll().state == 'busy'
    assert source.poll().state == 'busy'
    assert source.record_reads == 1
    record(path, running=0, updated_ms=NOW*1000+1)
    assert source.poll().state == 'idle'
    assert source.record_reads == 2


@pytest.mark.parametrize('changes', [dict(updated_ms=(NOW-36)*1000),
    dict(updated_ms=(NOW+10)*1000), dict(version=99), dict(running=3),
    dict(unknown=1), dict(pid=True), dict(agents='2')])
def test_stale_or_bad_record_never_reports_idle(tmp_path, changes):
    record(tmp_path/'a.json', **changes)
    assert reader(tmp_path).poll().state == 'unknown'


def test_dead_and_reused_pid_never_revives_busy(tmp_path):
    record(tmp_path/'a.json', running=1)
    assert reader(tmp_path, identity=lambda pid: None).poll().connected == 0
    record(tmp_path/'a.json', started_ms=BIRTH-30000, running=1)
    assert reader(tmp_path).poll().connected == 0


def test_torn_and_oversized_records_are_bounded(tmp_path):
    path=tmp_path/'a.json'
    for content in ('{', 'x'*70000, '[]'):
        path.write_text(content)
        assert reader(tmp_path).poll().state == 'unknown'


def test_multiple_instances_keep_busy_even_if_another_finishes(tmp_path):
    record(tmp_path/'a.json', running=0)
    record(tmp_path/'b.json', running=1, pid=124)
    state=reader(tmp_path).poll()
    assert (state.state, state.connected, state.busy) == ('busy', 2, 1)


def test_aggregate_busy_dominates_and_unknown_prevents_false_idle():
    idle=GlobalActivity('idle', 'DSH：空闲', connected=1)
    busy=GlobalActivity('busy', 'Codex：处理中', connected=1, busy=1)
    unknown=GlobalActivity()
    assert aggregate_sources([idle]) is idle
    assert aggregate_sources([idle,busy]).state == 'busy'
    assert aggregate_sources([unknown,busy]).state == 'busy'
    assert aggregate_sources([idle,unknown]).state == 'unknown'
    assert aggregate_sources([idle,replace(idle,label='Codex：空闲')]).state == 'idle'
    assert aggregate_sources([]).state == 'unknown'


def test_real_node_writer_matches_windows_process_identity(tmp_path):
    import os, shutil, subprocess, time
    from pathlib import Path
    node=shutil.which("node")
    if os.name != "nt" or node is None:
        pytest.skip("Windows Node runtime needed for process identity integration")
    module=(Path(__file__).resolve().parents[1]/"integrations/dsh-status/index.mjs").as_uri()
    code=f"""
        import {{attach}} from {json.dumps(module)};
        const cleanups=[];
        attach({{agents:{{list:()=>[{{status:'running'}}]}},on:()=>{{}},
          effect:f=>cleanups.push(f()),logger:{{warn:console.error}}}}, process.argv[1]);
        process.stdin.once('data',async()=>{{for(const f of cleanups) await f();process.exit(0);}});
        setTimeout(()=>process.exit(2),6000);
    """
    child=subprocess.Popen([node,'--input-type=module','-e',code,str(tmp_path)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        source=DshStatusReader(tmp_path)
        for _ in range(80):
            result=source.poll()
            if result.state=="busy": break
            time.sleep(.025)
        assert result.state=="busy"
        child.communicate(b"stop",timeout=3)
        assert child.returncode==0
        assert source.poll().state=="unknown"
    finally:
        if child.poll() is None:
            child.communicate(b"stop",timeout=8)
