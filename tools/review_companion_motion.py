"""Render equal-time companion comparisons and transitions as lossless animations.

Controlled visual states, not a live Codex connection test. No user settings are used.
Requires the existing Qt/OpenGL runtime plus Pillow for preview assembly.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from desktop_black_hole import BlackHoleGLWidget, configure_surface_format


class FrameClock:
    milliseconds = 300000

    def elapsed(self):
        return self.milliseconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--transition', action='store_true')
    parser.add_argument('--light', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])
    fps = 20
    seconds = 13 if args.transition else 11
    states = ('idle',) if args.transition else ('idle', 'busy')
    windows, clocks = [], []
    result = {'controlled_activity': True, 'fps': fps, 'seconds': seconds,
              'transition': args.transition, 'frames': [], 'errors': []}
    frames = []
    board = Image.new('RGB', (360*len(states)*2, 260*3), '#17191e')
    snapshots = [0, fps*2, fps*4, fps*6, fps*8, fps*10]
    index = 0

    def fail(message):
        result['errors'].append(str(message))
        app.quit()

    for state in states:
        window = BlackHoleGLWidget()
        window.fatal_error.connect(fail)
        window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        window.resize(360, 240)
        window.set_scene_quality(1)
        window.set_companion_enabled(True)
        window.set_companion_activity(state)
        for _ in range(1200):
            window._companion_orbit.advance(1/60)
        window._companion_orbit.phase = 0.45
        window._companion_orbit.breath_phase = 0.0
        clock = FrameClock()
        window._elapsed_timer = clock
        window._last_frame_elapsed_ms = clock.elapsed()
        window._frame_timer.stop()
        window.show()
        windows.append(window)
        clocks.append(clock)

    def capture():
        nonlocal index
        try:
            t = index/fps
            background = 'white' if args.light else '#17191e'
            preview = Image.new('RGB', (360*len(windows), 260), background)
            sample = {'time': t, 'states': []}
            for column, (window, clock) in enumerate(zip(windows, clocks)):
                if args.transition:
                    window.set_companion_activity('busy' if 2 <= t < 7 else 'idle')
                orbit = window._companion_orbit
                if index:
                    orbit.advance(1/fps)
                # Advance the disk's real shader time, without double-advancing
                # the companion in paintGL or in extra automatic Qt paints.
                clock.milliseconds = 300000 + round(t*1000)
                window._last_frame_elapsed_ms = clock.elapsed()
                image = window.grabFramebuffer().convertToFormat(QImage.Format_RGBA8888)
                rgba = Image.frombytes('RGBA', (image.width(), image.height()),
                                       bytes(image.constBits()))
                rgba = rgba.resize((360, 240), Image.Resampling.LANCZOS)
                preview.paste(rgba, (column*360, 20), rgba)
                label = f'{orbit.activity.upper()} | t={t:.2f}s'
                ImageDraw.Draw(preview).text((column*360+8, 4), label,
                                            fill='black' if args.light else 'white')
                sample['states'].append({'activity': orbit.activity, 'phase': orbit.phase,
                                         'radius': orbit.radius, 'pose': orbit.advance(0),
                                         'angular_speed': orbit.angular_speed})
            frames.append(preview)
            result['frames'].append(sample)
            if index in snapshots:
                tile = snapshots.index(index)
                board.paste(preview, ((tile%2)*preview.width, (tile//2)*260))
            index += 1
            if index % 40 == 0:
                print(f'Rendered {index}/{seconds*fps} frames', flush=True)
            if index >= seconds*fps:
                app.quit()
            else:
                QTimer.singleShot(0, capture)
        except Exception as error:
            fail(error)

    QTimer.singleShot(500, capture)
    QTimer.singleShot(120000, lambda: fail('Render timed out'))
    app.exec()
    for window in windows:
        window.close()
    if result['errors']:
        raise RuntimeError(result['errors'])
    frames[0].save(args.output/'motion.webp', save_all=True, append_images=frames[1:],
                   duration=round(1000/fps), loop=0, lossless=True, method=1)
    board.save(args.output/'contact.png')
    (args.output/'motion.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Saved {args.output}', flush=True)


if __name__ == '__main__':
    main()
