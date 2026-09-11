"""Bounded OS-owned locks; process death releases ownership, never delete a stale lock."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path


@contextmanager
def process_lock(target: Path, timeout: float = 30):
    if not 0 <= timeout <= 300:
        raise ValueError('lock timeout must be between 0 and 300 seconds')
    identity = hashlib.sha256(str(target.resolve()).casefold().encode()).hexdigest()
    if os.name != 'nt':
        raise RuntimeError('interprocess acquisition is currently qualified on Windows only')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateMutexW(None, False, 'Global\\LICInstaller-' + identity)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = False
    try:
        outcome = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        if outcome == 258:
            raise TimeoutError('Another installer owns this resource; retry after it exits: ' + str(target))
        if outcome not in (0, 128):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        owned = True
        # Abandoned ownership is not evidence of valid data. Every caller revalidates bytes/state.
        yield {'abandoned': outcome == 128}
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
