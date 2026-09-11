"""Defense-in-depth guard for a dedicated trusted validation subprocess (not a sandbox)."""
from __future__ import annotations

import os
from pathlib import Path
import sys


class ProbeGuard:
    def __init__(self, writable_root: Path):
        self.root = writable_root.resolve()
        self.network_attempts: list[str] = []
        self.denied_writes: list[str] = []

    def _check_write(self, path) -> None:
        if isinstance(path, int) or path is None:
            return
        path = Path(os.fsdecode(path)).resolve()
        # Windows resolves os.devnull to the device namespace, not a filesystem file.
        if str(path).lower() in {os.devnull.lower(), r'\\.\nul'}:
            return
        if path != self.root and self.root not in path.parents and str(path).lower() != os.devnull.lower():
            self.denied_writes.append(str(path))
            raise PermissionError('probe write escaped its disposable state root')

    def audit(self, event: str, args: tuple) -> None:
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.sendto'):
            self.network_attempts.append(event)
            raise PermissionError('network is forbidden during offline validation')
        if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
            raise PermissionError('child execution is forbidden inside the validation probe')
        if event == 'open':
            path, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                self._check_write(path)
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime', 'os.truncate'):
            self._check_write(args[0])
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            self._check_write(args[0])
            self._check_write(args[1])
        elif event == 'sqlite3.connect' and args[0] != ':memory:':
            self._check_write(args[0])

    def install(self) -> None:
        sys.addaudithook(self.audit)


def isolated_environment(state: Path, hub: Path) -> dict[str, str]:
    """Return child-only context; never change the caller's environment or real user settings."""
    state = state.resolve()
    environment = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN',
                 'HUGGINGFACE_HUB_TOKEN', 'TRANSFORMERS_CACHE', 'PYTORCH_TRANSFORMERS_CACHE',
                 'PYTORCH_PRETRAINED_BERT_CACHE'):
        environment.pop(name, None)
    folders = {'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata', 'USERPROFILE': 'home',
               'HOME': 'home', 'TEMP': 'tmp', 'TMP': 'tmp', 'HF_HOME': 'hf',
               'HF_ASSETS_CACHE': 'hf-assets', 'XDG_CACHE_HOME': 'cache',
               'TORCH_HOME': 'torch', 'MPLCONFIGDIR': 'mpl'}
    for key, relative in folders.items():
        path = state / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment.update(HF_HUB_CACHE=str(hub.resolve()), HF_ENDPOINT='https://huggingface.co',
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                       PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                       PIP_DISABLE_PIP_VERSION_CHECK='1')
    return environment
