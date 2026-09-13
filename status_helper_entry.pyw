"""Windowed helper: recover inherited pipes, never allocate a console."""
import ctypes
from ctypes import wintypes
import io
import msvcrt
import os
import sys


def inherited_stream(which, write=False):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetStdHandle.argtypes = [wintypes.DWORD]
    api.GetStdHandle.restype = wintypes.HANDLE
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.GetFileType.argtypes = [wintypes.HANDLE]
    api.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = api.GetStdHandle(which & 0xffffffff)
    if not handle or handle == ctypes.c_void_p(-1).value or api.GetFileType(handle) != 3:
        return None  # Accept inherited pipes only; no interactive console read.
    current, duplicate = api.GetCurrentProcess(), wintypes.HANDLE()
    if not api.DuplicateHandle(current, handle, current, ctypes.byref(duplicate), 0, False, 2):
        return None
    fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_WRONLY if write else os.O_RDONLY))
    return io.TextIOWrapper(os.fdopen(fd, 'wb' if write else 'rb'), encoding='utf8')


if __name__ == '__main__':
    try:
        sys.stdin = inherited_stream(-10)
        sys.stdout = inherited_stream(-11, True)
        if sys.stdin is not None:
            from codex_status_bridge import main
            main()
            if sys.stdout is not None:
                sys.stdout.flush()
    except Exception:
        pass  # Never block a Codex turn or leak payloads to logs.
