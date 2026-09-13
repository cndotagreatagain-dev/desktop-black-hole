"""Measure metadata polling and hook process startup with synthetic local data.

No real hooks are injected, no task is started, and no conversations are read.
The temporary state is isolated from the installed status directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_status_bridge as bridge
from codex_status import GlobalStatusReader, POLL_INTERVAL_MS


def benchmark(python: Path) -> dict:
    owner = bridge.find_owner()
    if owner is None:
        raise RuntimeError("Run from a local Codex task to measure real process-identity checks.")
    with tempfile.TemporaryDirectory(prefix="black-hole-status-benchmark-") as temporary:
        root = Path(temporary)
        directory = bridge.status_directory(root)
        bridge.atomic_json(directory / "installation.json", {"version": 1})
        for index in range(5):
            bridge.report({"hook_event_name": "UserPromptSubmit", "session_id": f"benchmark-{index}",
                           "turn_id": "synthetic-turn"}, directory, owner)
        source = GlobalStatusReader(root)
        assert source.poll(0).busy == 5
        before = (source.record_reads, source.record_bytes, source.tail_reads)
        start_cpu = time.process_time()
        start = time.perf_counter()
        for _ in range(500):
            assert source.poll(2).busy == 5
        wall_ms = (time.perf_counter() - start) * 1000 / 500
        cpu_ms = (time.process_time() - start_cpu) * 1000 / 500
        unchanged = {"record_reads": source.record_reads - before[0],
                     "record_bytes": source.record_bytes - before[1],
                     "tail_reads": source.tail_reads - before[2]}
        durations = []
        helper_dir = root / "helper-only"
        command = [str(python), "-I", str(Path(bridge.__file__).resolve()),
                   "--directory", str(helper_dir)]
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "benchmark",
                              "turn_id": "synthetic-turn"})
        for index in range(25):
            start = time.perf_counter()
            result = subprocess.run(command, input=payload, text=True, capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            elapsed = (time.perf_counter() - start) * 1000
            assert result.returncode == 0 and result.stdout == "{}\n" and not result.stderr
            if index >= 5:
                durations.append(elapsed)
        helper_records = list((helper_dir / "sessions").glob("*.json"))
        assert len(helper_records) == 1
        assert bridge.read_small(helper_records[0])["owner"] == owner
        return {"synthetic_data": True, "real_hook_delivery_test": False,
                "poll_interval_ms": POLL_INTERVAL_MS, "sessions": 5, "poll_iterations": 500,
                "unchanged_poll_mean_wall_ms": wall_ms, "unchanged_poll_mean_cpu_ms": cpu_ms,
                "unchanged_content_reads": unchanged, "helper_iterations": len(durations),
                "helper_start_and_report_median_ms": statistics.median(durations),
                "helper_start_and_report_max_ms": max(durations),
                "helper_state_file_bytes": helper_records[0].stat().st_size,
                "python": str(python),
                "limits": "Warm-cache microbenchmark; includes Python launch and real owner lookup. "
                          "Not total widget CPU/RAM, not an end-to-end Codex hook timing."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark(args.python)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
