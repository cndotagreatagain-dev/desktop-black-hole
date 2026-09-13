"""Windowed release entrypoint; plain Python can run this file too."""
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf8')

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        try:
            from tools.smoke_release import main
            code = main(sys.argv[2])
        except Exception:
            import json, traceback
            from pathlib import Path
            output=Path(sys.argv[2]); output.mkdir(parents=True,exist_ok=True)
            (output/'result.json').write_text(json.dumps(
                {'errors':[traceback.format_exc()]},indent=2),encoding='utf8')
            code=1
        raise SystemExit(code)
    try:
        from desktop_black_hole import main
        raise SystemExit(main())
    except Exception:
        import traceback
        from pathlib import Path
        directory = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')/'DesktopBlackHole'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory/'startup-error.log').write_text(traceback.format_exc(), encoding='utf8')
        except OSError:
            pass
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '黑洞启动失败。请确认显卡驱动支持 OpenGL 3.3，并保留完整的解压文件夹。\n'
            '详细信息：'+str(directory/'startup-error.log'), '桌面黑洞', 0x10)
        raise SystemExit(1)
