from datetime import datetime, timezone
import json
from pathlib import Path

from codex_activity import ActivityMonitor, RolloutActivityReader, find_session

NOW = 1800000000.0


def record(name, stamp=NOW, kind='event_msg', **extra):
    return {'type': kind, 'timestamp': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            'payload': {'type': name, **extra}}


def append(path, *records):
    with path.open('ab') as output:
        for value in records: output.write(json.dumps(value).encode()+b'\n')


def test_task_start_output_complete_and_staleness(tmp_path):
    path = tmp_path/'bound.jsonl'
    reader = RolloutActivityReader(path)
    assert reader.poll(NOW).state == 'unknown'
    append(path, record('task_started'))
    assert reader.poll(NOW).state == 'busy'
    assert reader.poll(NOW+301).state == 'unknown'
    append(path, record('item_completed', NOW+302, item={'type':'AgentMessage','text':'PRIVATE'}))
    assert reader.poll(NOW+302).state == 'output'
    assert reader.poll(NOW+304).state == 'busy'
    append(path, record('task_complete', NOW+305))
    assert reader.poll(NOW+305).state == 'idle'
    assert reader.poll(NOW+10000).state == 'idle'
    assert 'PRIVATE' not in repr(vars(reader))


def test_response_item_format_and_partial_lines(tmp_path):
    path = tmp_path/'bound.jsonl'
    reader = RolloutActivityReader(path)
    append(path, record('reasoning', kind='response_item'))
    assert reader.poll(NOW).state == 'busy'
    value = json.dumps(record('message', NOW+3, kind='response_item', role='assistant', phase='commentary')).encode()
    with path.open('ab') as output: output.write(value[:30])
    assert reader.poll(NOW+3).state == 'busy'
    with path.open('ab') as output: output.write(value[30:]+b'\n')
    assert reader.poll(NOW+3).state == 'output'
    append(path, record('message', NOW+6, kind='response_item', role='assistant', phase='final_answer'))
    assert reader.poll(NOW+8).state == 'idle'


def test_user_facing_status_labels_are_chinese_without_changing_state_keys(tmp_path):
    assert RolloutActivityReader(None).poll(NOW).label == 'Codex：未连接'
    path = tmp_path/'status.jsonl'
    reader = RolloutActivityReader(path)
    assert reader.poll(NOW).label == 'Codex：状态来源不可用'
    append(path, record('task_started'))
    assert (reader.poll(NOW).state, reader.poll(NOW).label) == ('busy', 'Codex：处理中')
    assert reader.poll(NOW+301).label == 'Codex：状态待确认'
    append(path, record('item_completed', NOW+302, item={'type': 'AgentMessage'}))
    assert (reader.poll(NOW+302).state, reader.poll(NOW+302).label) == ('output', 'Codex：刚有回复')
    append(path, record('task_complete', NOW+305))
    assert (reader.poll(NOW+305).state, reader.poll(NOW+305).label) == ('idle', 'Codex：空闲')


def test_truncation_missing_and_malformed_records_fail_to_unknown(tmp_path):
    path = tmp_path/'bound.jsonl'
    append(path, record('task_started'))
    reader = RolloutActivityReader(path)
    assert reader.poll(NOW).state == 'busy'
    path.write_bytes(b'{bad json}\nnull\n')
    assert reader.poll(NOW).state == 'unknown'
    path.unlink()
    assert reader.poll(NOW).state == 'unknown'
    append(path, record('task_complete', NOW+1))
    assert reader.poll(NOW+1).state == 'idle'


def test_large_initial_log_is_bounded_and_uses_recent_metadata(tmp_path):
    path = tmp_path/'bound.jsonl'
    path.write_bytes(b'x'*(2*1024*1024)+b'\n')
    append(path, record('task_started'))
    reader = RolloutActivityReader(path)
    assert reader.poll(NOW).state == 'busy'
    assert len(reader.pending) <= 1024*1024//2
    assert reader.offset == path.stat().st_size


def test_only_an_explicit_valid_task_id_can_be_bound(tmp_path):
    task_id = '01a062a1-47a3-7cb1-b9f7-4c48e4d1d63f'
    directory = tmp_path/'sessions'/'2026'/'09'/'02'
    directory.mkdir(parents=True)
    path = directory/f'rollout-test-{task_id}.jsonl'
    path.touch()
    assert find_session(task_id,tmp_path) == path
    for invalid in (None,'','*','../'+task_id,'-'*36,'00000000-0000-0000-0000-000000000000'):
        assert find_session(invalid,tmp_path) is None


def test_worker_has_one_inflight_job_and_closes_cleanly(tmp_path):
    monitor = ActivityMonitor(tmp_path/'missing.jsonl')
    monitor.poll()
    first = monitor.future
    first.result(timeout=2)
    assert monitor.poll().state == 'unknown'
    assert monitor.future is not first
    monitor.close()
    assert monitor.poll().state == 'unknown'
    assert monitor.closed
