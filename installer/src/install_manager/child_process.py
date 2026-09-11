"""Launch managed interpreters without inheriting frozen bootloader DLL search paths."""
import os
import subprocess
import sys
import threading

_guard = threading.Lock()


def run(*args, **kwargs):
    with _guard:
        if 'env' in kwargs:
            kwargs['env'] = {k: v for k, v in kwargs['env'].items() if k not in ('TCL_LIBRARY', 'TK_LIBRARY')}
        frozen = os.name == 'nt' and getattr(sys, 'frozen', False)
        # Managed package/probe helpers are background work.  Preserve their
        # captured output, but never create a distracting console window.
        if os.name == 'nt' and not kwargs.get('creationflags'):
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            kwargs.setdefault('startupinfo', startup)
        if frozen:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.run(*args, **kwargs)
        finally:
            if frozen:
                ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
