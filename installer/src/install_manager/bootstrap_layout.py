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
    if root.exists() and not root.is_dir():
        raise FileExistsError('The target is populated; existing files will not be adopted or overwritten')
    if root.exists() and any(root.iterdir()) and not resume:
        if not _is_managed_data_only_root(root, delivery):
            raise FileExistsError('The target is populated; existing files will not be adopted or overwritten')
    if resume and not root.is_dir():
        raise FileNotFoundError('The previous installation folder no longer exists. It cannot be resumed as-is. Start setup again in this empty location.')
    parent = root
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < 2 * 1024**3:
        raise OSError('At least 2 GiB of free space is required for Core installation')
    return root


def _is_managed_data_only_root(root: Path, delivery: Path) -> bool:
    """Accept a pre-Core Import library, never an arbitrary populated target."""
    try:
        if {item.name for item in root.iterdir()} != {'Data'}:
            return False
        data = root / 'Data'
        allowed_areas = {'Downloads', 'Models', 'Tasks', 'Packages', 'Tools', 'State'}
        if not data.is_dir() or any(item.name not in allowed_areas for item in data.iterdir()):
            return False
        from .managed_resources import (approved_resource_specs, load_resource_library,
                                        resource_library_path)
        library = load_resource_library(root)
        if not resource_library_path(root).is_file():
            return False
        specs = {item.identity: item for item in approved_resource_specs(delivery, root)}
        for record in library.get('resources', ()):
            spec = specs.get(str(record.get('identity', '')))
            if spec is None or Path(str(record.get('path', ''))).resolve() != spec.destination.resolve():
                return False
        downloads = (data / 'Downloads').resolve()
        library_path = resource_library_path(root).resolve()
        file_specs = {item.destination.resolve() for item in specs.values() if not item.archive_members}
        directory_specs = tuple(item.destination.resolve() for item in specs.values() if item.archive_members)
        for current, directories, files in os.walk(data, followlinks=False):
            for name in directories + files:
                entry = Path(current) / name
                if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                    return False
            for name in files:
                path = (Path(current) / name).resolve()
                if (path == library_path or path in file_specs or downloads in path.parents or
                        any(parent == path.parent or parent in path.parents for parent in directory_specs)):
                    continue
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def layout(root: Path) -> dict[str, Path]:
    data = root / 'Data'
    return {'runtime': root / 'Shared/Runtimes/python-3.14.6',
            'venv': root / 'Apps/LIC-Lite/candidate-1/venv',
            'application': root / 'Apps/LIC-Lite/candidate-1/package',
            'models': root / 'Shared/Models', 'cache': root / 'Cache',
            'state': root / 'State', 'logs': root / 'Logs', 'probes': root / 'Validation',
            # Core's qualified paths above remain stable. New optional resources
            # are centralized below one manager-owned Data root.
            'data': data, 'data_downloads': data / 'Downloads',
            'data_models': data / 'Models', 'data_tasks': data / 'Tasks',
            'data_packages': data / 'Packages', 'data_tools': data / 'Tools',
            'data_state': data / 'State'}


def reject_reparse_entries(root: Path) -> None:
    """Resume never follows a newly inserted link into unrelated user resources."""
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            entry = Path(current) / name
            if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                raise ValueError('Installation contains a reparse entry requiring review: ' + str(entry))
