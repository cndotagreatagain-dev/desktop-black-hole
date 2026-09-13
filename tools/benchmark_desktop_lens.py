"""GPU cost of cached desktop-map resolve, optionally uploading every scene frame.

Uses a constant test image, not screen capture. OS readback latency is reported
separately by capture_desktop_lens.py; these numbers are not desktop frame rates.
"""
import argparse
import json
import math
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from desktop_capture import DesktopFrame
from tools.benchmark_black_hole_gpu import (
    OpenGLBenchmarkRenderer, OpenGLQueryBackend, build_result,
    load_shader_sources, measure_gpu_states,
)


class DesktopBenchmark(OpenGLBenchmarkRenderer):
    def __init__(self, size, sources, mode):
        super().__init__(size, sources)
        self.mode = mode
        self.serial = 0
        if mode != "off":
            width, height = size
            margin = math.ceil(height * .30) + 4
            image = QImage(width + margin*2, height + margin*2, QImage.Format_RGBA8888)
            image.fill(QColor("#1f2b44"))
            self.image = image
            self.rect = QRect(-margin, -margin, image.width(), image.height())
            self._postprocess.desktop_geometry = (0, 0, width, height)
            self._postprocess.desktop_frame = DesktopFrame(image, self.rect, 0, 1.0)

    def _draw_scene_pass(self, drag_strength):
        if self.mode == "upload":
            self.serial += 1
            self._postprocess.desktop_frame = DesktopFrame(self.image, self.rect, self.serial, 1.0)
        super()._draw_scene_pass(drag_strength)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sources = load_shader_sources(Path(__file__).resolve().parents[1])
    results = []
    for size in ((360, 240), (600, 400)):
        for mode in ("off", "cached", "upload"):
            renderer = DesktopBenchmark(size, sources, mode)
            try:
                measured = measure_gpu_states(renderer, OpenGLQueryBackend(renderer.gl),
                                              warmup=30, frames=120)
                assert not renderer._postprocess.desktop_error
                result = build_result(size, measured, sources.cursor_shader_source)
                result["desktop_mode"] = mode
                results.append(result)
                print(json.dumps(result), flush=True)
            finally:
                renderer.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
