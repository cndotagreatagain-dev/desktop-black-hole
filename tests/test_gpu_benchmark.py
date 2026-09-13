from __future__ import annotations

from pathlib import Path

import pytest

from tools.benchmark_black_hole_gpu import (
    build_result,
    load_shader_sources,
    measure_gpu_states,
    nearest_rank_percentile,
    parse_cli,
    parse_size,
)


def test_nearest_rank_percentiles_are_deterministic():
    samples = [9.0, 1.0, 4.0, 2.0, 7.0]

    assert nearest_rank_percentile(samples, 50.0) == 4.0
    assert nearest_rank_percentile(samples, 95.0) == 9.0
    assert samples == [9.0, 1.0, 4.0, 2.0, 7.0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("360x240", (360, 240)), ("600X400", (600, 400)), ("720x480", (720, 480))],
)
def test_parse_size_accepts_benchmark_sizes(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["0x240", "360x0", "-1x240", "360x-1", "bad"])
def test_parse_size_rejects_non_positive_or_malformed_values(text):
    with pytest.raises(Exception):
        parse_size(text)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--frames", "0"],
        ["--frames", "-1"],
        ["--warmup", "0"],
        ["--warmup", "-1"],
    ],
)
def test_cli_rejects_non_positive_counts(arguments):
    with pytest.raises(SystemExit):
        parse_cli(arguments)


def test_cli_deduplicates_sizes_without_reordering(tmp_path):
    options = parse_cli(
        [
            "--project-root",
            str(tmp_path),
            "--frames",
            "5",
            "--warmup",
            "3",
            "--size",
            "360x240",
            "--size",
            "720x480",
            "--size",
            "360x240",
        ]
    )

    assert options.project_root == tmp_path.resolve()
    assert options.frames == 5
    assert options.warmup == 3
    assert options.sizes == ((360, 240), (720, 480))


def test_result_has_the_documented_json_contract():
    measured = {
        state: [1.0, 2.0, 3.0]
        for state in ("scene", "cursor", "total", "file_drag")
    }

    result = build_result((360, 240), measured, "project")

    assert set(result) == {
        "size",
        "scene_p50_ms",
        "scene_p95_ms",
        "cursor_p50_ms",
        "cursor_p95_ms",
        "total_p50_ms",
        "total_p95_ms",
        "file_drag_p50_ms",
        "file_drag_p95_ms",
        "cursor_shader_source",
    }
    assert result["size"] == "360x240"
    assert result["cursor_shader_source"] == "project"


def test_shader_loader_uses_literals_without_executing_target(tmp_path):
    marker = tmp_path / "executed.txt"
    (tmp_path / "black_hole_shaders.py").write_text(
        "FULLSCREEN_VERTEX_SHADER_SOURCE = 'vertex'\n"
        "SCENE_FRAGMENT_SHADER_SOURCE = 'scene'\n"
        "CURSOR_FRAGMENT_SHADER_SOURCE = 'cursor'\n"
        f"open({str(marker)!r}, 'w').write('bad')\n",
        encoding="utf-8",
    )

    sources = load_shader_sources(tmp_path)

    assert sources.vertex == "vertex"
    assert sources.scene_fragment == "scene"
    assert sources.cursor_fragment == "cursor"
    assert sources.cursor_shader_source == "project"
    assert not marker.exists()


def test_shader_loader_supports_baseline_aliases_and_cursor_fallback(tmp_path):
    (tmp_path / "desktop_black_hole.py").write_text(
        "VERTEX_SHADER_SOURCE = 'baseline vertex'\n"
        "FRAGMENT_SHADER_SOURCE = 'baseline scene'\n",
        encoding="utf-8",
    )

    sources = load_shader_sources(tmp_path)

    assert sources.vertex == "baseline vertex"
    assert sources.scene_fragment == "baseline scene"
    assert "u_cursor_texture" in sources.cursor_fragment
    assert sources.cursor_shader_source == "harness-fallback"


class _FakeRenderer:
    def __init__(self):
        self.draws = []
        self.closed = False
        self.finished = 0

    def draw_scene(self):
        self.draws.append("scene")

    def draw_cursor(self):
        self.draws.append("cursor")

    def draw_total(self):
        self.draws.append("total")

    def draw_file_drag(self):
        self.draws.append("file_drag")

    def finish(self):
        self.finished += 1

    def close(self):
        self.closed = True


class _FakeQueries:
    def __init__(self, fail_result_at=None):
        self.next_query = 1
        self.generated = []
        self.deleted = []
        self.active = None
        self.result_count = 0
        self.fail_result_at = fail_result_at

    def create(self):
        query = self.next_query
        self.next_query += 1
        self.generated.append(query)
        return query

    def begin(self, query):
        assert self.active is None, "timer queries must not be nested"
        self.active = query

    def end(self):
        assert self.active is not None
        self.active = None

    def result_nanoseconds(self, query):
        assert self.active is None
        assert query in self.generated
        self.result_count += 1
        if self.result_count == self.fail_result_at:
            raise RuntimeError("query failed")
        return self.result_count * 1_000_000

    def delete(self, query):
        assert self.active is None
        self.deleted.append(query)


def test_timer_queries_are_not_nested_and_are_deleted():
    renderer = _FakeRenderer()
    queries = _FakeQueries()

    measured = measure_gpu_states(
        renderer, queries, warmup=1, frames=2
    )

    assert list(measured) == ["scene", "cursor", "total", "file_drag"]
    assert renderer.draws == [
        "scene", "scene", "scene",
        "cursor", "cursor", "cursor",
        "total", "total", "total",
        "file_drag", "file_drag", "file_drag",
    ]
    assert renderer.finished == 4
    assert queries.active is None
    assert queries.generated == [1, 2, 3, 4]
    assert queries.deleted == queries.generated
    assert renderer.closed


def test_query_failure_closes_all_gl_resources():
    renderer = _FakeRenderer()
    queries = _FakeQueries(fail_result_at=2)

    with pytest.raises(RuntimeError, match="query failed"):
        measure_gpu_states(renderer, queries, warmup=1, frames=3)

    assert queries.active is None
    assert queries.deleted == queries.generated
    assert renderer.closed
