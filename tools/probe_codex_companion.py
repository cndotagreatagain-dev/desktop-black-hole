"""Capture the widget driven by real local global Codex status records.

Unlike visual phase sweeps this does not inject an activity state. Only state
metadata is saved; no conversation content or credentials are exported.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QSettings, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMenu
from desktop_black_hole import DesktopBlackHole, configure_surface_format


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--menu', action='store_true', help='Also capture the production Qt menus')
    args = parser.parse_args()
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app = QApplication([])
    result = {'error': None, 'samples': []}
    with tempfile.TemporaryDirectory(prefix='companion-live-probe-') as temp:
        settings = QSettings(str(Path(temp)/'probe.ini'), QSettings.IniFormat)
        # Do not change the user's persistent settings or desktop-capture choice.
        window = DesktopBlackHole(settings=settings, companion_default=True)
        window.resize(600, 400)
        window.set_scene_quality(1)
        window._set_cursor_lens_enabled(False)

        def fail(message):
            result['error'] = str(message)
            window.close()
            app.quit()

        def capture_menu():
            # Exercise this test window's real event handler, not another app or
            # the OS pointer. Save native Qt rasterization, not a mockup.
            def inspect_popup():
                popup = app.activePopupWidget()
                if popup is None:
                    fail('Production context menu did not open')
                    return
                result['menu'] = [
                    {'text': action.text(), 'tooltip': action.toolTip(),
                     'checked': action.isChecked(), 'enabled': action.isEnabled()}
                    for action in popup.actions()]
                if not popup.grab().save(str(args.output/'menu.png')):
                    popup.close()
                    fail('Could not save context menu')
                    return
                # Keep the submenu wrapper alive while the nested event loop
                # runs; avoid ephemeral QAction.menu() wrappers in generators.
                submenus = popup.findChildren(QMenu)
                if not submenus:
                    popup.close()
                    fail('Quality submenu not found')
                    return
                quality = submenus[0]
                result['quality_menu'] = [action.text() for action in quality.actions()]

                def inspect_quality():
                    saved = quality.grab().save(str(args.output/'quality_menu.png'))
                    quality.close()
                    popup.close()
                    if not saved:
                        fail('Could not save quality menu')

                quality.popup(popup.mapToGlobal(QPoint(popup.width(), 0)))
                QTimer.singleShot(150, inspect_quality)

            QTimer.singleShot(150, inspect_popup)
            point = QPoint(30, 30)
            app.sendEvent(window, QContextMenuEvent(
                QContextMenuEvent.Mouse, point, window.mapToGlobal(point)))

        def sample():
            reader = window._activity_monitor.reader
            result['samples'].append({
                'observed_at': time.time(),
                'state': window._companion_orbit.activity,
                'label': window._activity_label,
                'phase': window._companion_orbit.phase,
                'radius': window._companion_orbit.radius,
                'status_record_reads': reader.record_reads,
                'status_record_bytes': reader.record_bytes,
                'recovery_tail_reads': reader.tail_reads,
                'recovery_tail_bytes': reader.tail_bytes,
                'source_last_event_ns': reader.last_event_ns,
                'connected_sources': window._activity.connected,
            })
            if len(result['samples']) < 8:
                QTimer.singleShot(1000, sample)
                return
            result.update({
                'scope': 'local_global',
                'source_found': reader.last_event_ns > 0,
                'poll_interval_ms': window._activity_timer.interval(),
                'timer_active': window._activity_timer.isActive(),
                'sphere_radius': window._companion_orbit.advance(0)[3],
                'state_injected': False,
            })
            args.output.mkdir(parents=True, exist_ok=True)
            if not window.grabFramebuffer().save(str(args.output/'live.png')):
                fail('Could not save live frame')
                return
            if args.menu:
                capture_menu()
            window.close()
            result['worker_closed'] = window._activity_monitor.closed
            app.quit()

        window.fatal_error.connect(fail)
        window.show()
        QTimer.singleShot(1200, sample)
        QTimer.singleShot(20000, lambda: fail('Live probe timed out'))
        app.exec()
    if result['error']:
        raise RuntimeError(result['error'])
    (args.output/'live.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
