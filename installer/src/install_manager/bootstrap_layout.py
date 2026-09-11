"""Explicit disposable user-space layout; no default ownership of existing folders."""
import os
from pathlib import Path
import shutil


def long_paths_enabled() -> bool:
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return winreg.QueryValueEx(key, 'LongPathsEnabled')[0] == 1
    except OSError:
        return False


def validate_root(root: Path, *, delivery: Path, resume=False) -> Path:
    absolute = root.expanduser().absolute()
    root = absolute.resolve()
    if root != absolute or len(str(root)) > 110 or len(root.parts) < 3:
        raise ValueError('Choose a short, direct local folder (at most 110 characters), without links')
    for parent in (absolute, *absolute.parents):
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('Installation through a reparse point is unsupported')
    if str(root).startswith('\\\\'):
        raise ValueError('Network installation roots are not qualified')
    delivery = delivery.resolve()
    if root == delivery or root in delivery.parents or delivery in root.parents:
        raise ValueError('Choose an installation root separate from the delivered program')
    for env in ('WINDIR', 'ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(env):
            protected = Path(os.environ[env]).resolve()
            if root == protected or protected in root.parents:
                raise ValueError('Select a standard-user writable location')
    if root.exists() and (not root.is_dir() or (any(root.iterdir()) and not resume)):
        raise FileExistsError('The target is populated; existing files will not be adopted or overwritten')
    if resume and not root.is_dir():
        raise FileNotFoundError('The previous installation folder no longer exists. It cannot be resumed as-is. Start setup again in this empty location.')
    parent = root
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < 2 * 1024**3:
        raise OSError('At least 2 GiB of free space is required for Core installation')
    return root


def layout(root: Path) -> dict[str, Path]:
    return {'runtime': root / 'Shared/Runtimes/python-3.14.6',
            'venv': root / 'Apps/LIC-Lite/candidate-1/venv',
            'application': root / 'Apps/LIC-Lite/candidate-1/package',
            'models': root / 'Shared/Models', 'cache': root / 'Cache',
            'state': root / 'State', 'logs': root / 'Logs', 'probes': root / 'Validation'}


def reject_reparse_entries(root: Path) -> None:
    """Resume never follows a newly inserted link into unrelated user resources."""
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            entry = Path(current) / name
            if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                raise ValueError('Installation contains a reparse entry requiring review: ' + str(entry))
