"""Build an explicit onedir qualification payload using a pinned, isolated toolchain."""
import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from install_manager.acquisition import sha256_file
from install_manager.bootstrap import verify_extracted
from install_manager.artifacts import load_artifact_descriptor
from install_manager.dependency_lock import load_dependency_lock
from install_manager.recipe import load_recipe
from install_manager.release_preflight import payload_preflight, scan_content, validate_notices
from install_manager.verification import verify_package


# Readiness probes run as a small isolated package.  Keep every module required
# by its package initializer in the delivered probe payload.
PROBE_FILES = ('__init__.py', 'product.py', 'probe_guard.py',
               'installed_integrity_probe.py', 'lic_model_probe.py')


def encoded(data):
    return (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def deterministic_zip(root, output):
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(root.parent).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())


def stage_wheel_notices(wheels, destination: Path) -> list[str]:
    """Keep the license material actually carried by each offline Core wheel."""
    staged = []
    destination.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            candidates = [item for item in archive.infolist()
                          if not item.is_dir() and ('license' in item.filename.lower()
                                                     or item.filename.lower().endswith('/copying'))]
            if not candidates:
                raise ValueError('offline Core wheel has no packaged license text: ' + wheel.name)
            # A wheel may carry several texts. Preserve each distinct member rather than guessing one.
            for item in candidates:
                name = item.filename.replace('/', '__')
                target = destination / (wheel.stem + '__' + name)
                target.write_bytes(archive.read(item))
                staged.append(target.name)
    return staged


def build_tool_wheels(lock: dict) -> list[Path]:
    """Return the exact, locally verified wheels used only by the build interpreter."""
    cache = ROOT / 'build/tool-cache/verified'
    wheels = []
    for item in lock['wheels']:
        wheel = cache / item['name'] / item['version'] / item['filename']
        if not wheel.is_file() or wheel.stat().st_size != item['size'] or sha256_file(wheel) != item['sha256']:
            raise ValueError('missing or invalid exact build tool wheel: ' + item['filename'])
        wheels.append(wheel)
    return wheels


def stage_build_interpreter(runtime_root: Path, output: Path, lock: dict) -> tuple[Path, list[Path]]:
    """Copy the approved runtime and add verified build-only wheels outside the payload."""
    staged = output / 'build-interpreter'
    shutil.copytree(runtime_root, staged)
    wheels = build_tool_wheels(lock)
    subprocess.run([sys.executable, '-I', '-B', '-m', 'pip', 'install', '--no-index', '--no-deps',
                    '--no-cache-dir', '--target', str(staged / 'Lib/site-packages'), *map(str, wheels)], check=True)
    expected = {item['name']: item['version'] for item in lock['wheels']}
    probe = ('import importlib.metadata,json; print(json.dumps({name: importlib.metadata.version(name) '
             'for name in ' + repr(sorted(expected)) + '}, sort_keys=True))')
    actual = json.loads(subprocess.check_output([str(staged / 'python.exe'), '-I', '-B', '-c', probe], text=True))
    if actual != expected:
        raise ValueError('staged build interpreter has wrong build tools: ' + repr(actual))
    return staged, wheels


def approved_native_origin(native: Path, runtime_root: Path, staged_runtime: Path) -> tuple[str, str]:
    """Accept only an original runtime file or an exact staged copy of one."""
    runtime_root = runtime_root.resolve()
    staged_runtime = staged_runtime.resolve()
    native = native.resolve()
    if native.is_relative_to(runtime_root):
        relative = native.relative_to(runtime_root)
    elif native.is_relative_to(staged_runtime):
        relative = native.relative_to(staged_runtime)
        approved = runtime_root / relative
        if not approved.is_file() or sha256_file(native) != sha256_file(approved):
            raise ValueError('Unapproved staged native build input: ' + str(native))
    else:
        raise ValueError('Unapproved native build input: ' + str(native))
    return relative.as_posix(), sha256_file(native)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--lic-package', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    p.add_argument('--runtime-archive', type=Path, required=True)
    p.add_argument('--trust-wheel', type=Path, required=True,
                   help='Exact pinned certifi wheel supplying the portable bootstrap CA bundle')
    p.add_argument('--core-wheel-root', type=Path,
                   help='Root containing the exact Core wheels for an offline LIC Lite package')
    p.add_argument('--offline-core', action='store_true',
                   help='Fail unless the delivered package contains every approved Core artifact')
    p.add_argument('--archive-name',
                   help='Customer ZIP filename; defaults to the historical review-build name')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Build output already exists; choose a fresh directory')
    output.mkdir(parents=True)
    lock_path = ROOT / 'tools/build-tools-windows.json'
    lock = json.loads(lock_path.read_text())
    runtime_descriptor = load_artifact_descriptor(ROOT / 'src/install_manager/recipes/python-3.14.6-windows-x64.json')
    dependency_lock = load_dependency_lock(ROOT / 'src/install_manager/recipes/base-windows-nvidia-cu130.json')
    certifi = next((wheel for wheel in dependency_lock.wheels if wheel.name == 'certifi'), None)
    if certifi is None or sha256_file(args.trust_wheel) != certifi.artifact.expected_sha256:
        raise ValueError('Trust bundle input is not the exact certifi wheel in the approved dependency lock')
    if sha256_file(args.runtime_archive) != runtime_descriptor.expected_sha256:
        raise ValueError('Build runtime archive differs from the approved official artifact')
    runtime_files_verified = verify_extracted(args.runtime_archive, args.runtime_root.resolve())
    if platform.python_version() != lock['python']:
        raise ValueError('wrong build interpreter')
    for wheel in lock['wheels']:
        if importlib.metadata.version(wheel['name']) != wheel['version']:
            raise ValueError('wrong build tool: ' + wheel['name'])
    staged_runtime, staged_wheels = stage_build_interpreter(args.runtime_root.resolve(), output, lock)
    recipe = load_recipe(ROOT / 'src/install_manager/recipes/lic.json')
    if not verify_package(args.lic_package, recipe).verified:
        raise ValueError('unapproved LIC package')
    # Scan inside the archive too. LIC has its own MIT license; it is not the manager license.
    with zipfile.ZipFile(args.lic_package) as archive:
        findings = [f for i in archive.infolist() if not i.is_dir()
                    for f in scan_content('LIC-Lite/' + i.filename, archive.read(i))]
    if findings:
        raise ValueError('LIC payload hygiene findings: ' + str(findings))
    payload = output / 'payload'
    payload.mkdir()
    shutil.copytree(ROOT / 'src/install_manager/recipes', payload / 'recipes')
    (payload / 'artifacts').mkdir()
    shutil.copyfile(args.lic_package, payload / 'artifacts/lic-lite.zip')
    if args.offline_core and args.core_wheel_root is None:
        raise ValueError('--offline-core requires --core-wheel-root')
    if args.core_wheel_root is not None:
        offline = payload / 'offline-artifacts/verified'
        required = (runtime_descriptor, *(wheel.artifact for wheel in dependency_lock.wheels))
        for descriptor in required:
            source = args.runtime_archive if descriptor.artifact_id == runtime_descriptor.artifact_id else args.core_wheel_root / descriptor.filename
            if not source.is_file() or sha256_file(source) != descriptor.expected_sha256:
                raise ValueError('missing or invalid exact offline Core artifact: ' + descriptor.filename)
            if descriptor.expected_size is not None and source.stat().st_size != descriptor.expected_size:
                raise ValueError('offline Core artifact size differs: ' + descriptor.filename)
            target = offline / descriptor.artifact_id / descriptor.version / descriptor.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    trust = payload / 'trust'
    trust.mkdir()
    with zipfile.ZipFile(args.trust_wheel) as archive:
        (trust / 'cacert.pem').write_bytes(archive.read('certifi/cacert.pem'))
    probes = payload / 'probes/install_manager'
    probes.mkdir(parents=True)
    for name in PROBE_FILES:
        shutil.copyfile(ROOT / 'src/install_manager' / name, probes / name)
    notices = payload / 'notices'
    notices.mkdir()
    with zipfile.ZipFile(args.trust_wheel) as archive:
        (notices / 'certifi-MPL-2.0.txt').write_bytes(
            archive.read(f'certifi-{certifi.version}.dist-info/licenses/LICENSE'))
    shutil.copyfile(args.runtime_root / 'LICENSE.txt', notices / 'CPython-Windows.txt')
    shutil.copyfile(args.runtime_root / 'Doc/html/_sources/license.rst.txt', notices / 'CPython-All-Notices.txt')
    shutil.copyfile(args.runtime_root / 'tcl/tk8.6/license.terms', notices / 'Tk.txt')
    shutil.copyfile(ROOT / 'tools/notices/Tcl-8.6.15.txt', notices / 'Tcl.txt')
    dist = importlib.metadata.distribution('pyinstaller')
    shutil.copyfile(dist.locate_file('pyinstaller-6.22.2.dist-info/licenses/COPYING.txt'), notices / 'PyInstaller.txt')
    core_notice_files = []
    if args.core_wheel_root is not None:
        core_notice_files = stage_wheel_notices(
            [args.core_wheel_root / wheel.artifact.filename for wheel in dependency_lock.wheels],
            notices / 'core-wheels')
        summary = [
            'LIC Install Manager 0.7.0 — Third-party notices for this Lite package',
            '',
            'This package includes the exact LIC Core wheels listed in recipes/core-windows-x64-v2.json.',
            'License texts supplied inside those wheels are preserved in notices/core-wheels/.',
            '',
            *[f'- {wheel.name} {wheel.version}: {wheel.artifact.license_id}'
              for wheel in dependency_lock.wheels],
        ]
        (payload / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    inventory = json.loads((ROOT / 'tools/third-party-inventory.json').read_text())
    validate_notices(inventory, payload)
    (payload / 'third-party-inventory.json').write_bytes(encoded(inventory))
    hygiene = payload_preflight(payload)
    if not hygiene['passed']:
        raise ValueError('Delivery payload hygiene findings: ' + str(hygiene['findings']))
    index = {'schema_version': 1, 'files': {f.relative_to(payload).as_posix(): sha256_file(f)
                                          for f in sorted(payload.rglob('*')) if f.is_file()}}
    (payload / 'payload-index.json').write_bytes(encoded(index))
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    epoch = subprocess.check_output(['git', '-C', str(ROOT), 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip()
    inputs = sorted([*ROOT.glob('src/install_manager/*.py'), *ROOT.glob('src/install_manager/recipes/**/*.json'),
                     *ROOT.glob('tools/*.py'), lock_path, ROOT / 'tools/third-party-inventory.json'])
    source_inputs = {f.relative_to(ROOT).as_posix(): sha256_file(f) for f in inputs}
    from install_manager.product import PRODUCT_VERSION
    identity = {'version': PRODUCT_VERSION, 'source_commit': commit,
                'source_input_sha256': hashlib.sha256(encoded(source_inputs)).hexdigest(),
                'source_inputs': source_inputs, 'python': platform.python_version(),
                'packaging': 'PyInstaller 6.22.2 onedir; windowed; no UPX',
                'build_tool_lock_sha256': sha256_file(lock_path), 'source_date_epoch': int(epoch),
                'customer_release_approved': False}
    (output / 'build_identity.py').write_text('IDENTITY = ' + repr(identity) + '\nINDEX_SHA256 = ' +
                                             repr(sha256_file(payload / 'payload-index.json')) + '\n', encoding='utf-8')
    command = [str(staged_runtime / 'python.exe'), '-I', '-B', '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
               '--windowed', '--noupx', '--name', 'LIC Install Manager', '--distpath', str(output / 'dist'),
               '--workpath', str(output / 'work'), '--specpath', str(output),
               '--paths', str(ROOT / 'src'), '--paths', str(output),
               '--add-data', str(payload) + ';.', '--hidden-import', 'build_identity',
               str(ROOT / 'tools/frozen_entry.py')]
    env = dict(os.environ, PYTHONHASHSEED='0', SOURCE_DATE_EPOCH=epoch, PYTHONDONTWRITEBYTECODE='1')
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    env['PATH'] = str(system / 'System32') + os.pathsep + str(system)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY', 'TCLLIBPATH'):
        env.pop(name, None)
    with (output / 'build.log').open('w') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    app = output / 'dist/LIC Install Manager'
    # Fail closed if native DLL discovery borrowed anything from a developer PATH/tool cache.
    toc = ast.literal_eval((output / 'work/LIC Install Manager/Analysis-00.toc').read_text(encoding='utf-8'))
    origins = {}
    def inspect(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and value[2] in ('BINARY', 'EXTENSION') and isinstance(value[1], str):
                relative, digest = approved_native_origin(Path(value[1]), args.runtime_root, staged_runtime)
                origins[value[0]] = {'runtime_relative_path': relative, 'sha256': digest}
            else:
                for child in value:
                    inspect(child)
    inspect(toc)
    archive = output / (args.archive_name or f'LoRA Image Curator Install Manager {PRODUCT_VERSION} - Golden Build.zip')
    deterministic_zip(app, archive)
    report = {'identity': identity, 'payload_index_sha256': sha256_file(payload / 'payload-index.json'),
              'archive_sha256': sha256_file(archive), 'archive_bytes': archive.stat().st_size,
              'files': {f.relative_to(app).as_posix(): {'sha256': sha256_file(f), 'size': f.stat().st_size}
                        for f in sorted(app.rglob('*')) if f.is_file()},
              'build_command': command, 'hygiene': hygiene, 'native_origins': origins,
              'runtime_archive_sha256': runtime_descriptor.expected_sha256,
              'build_interpreter': {'runtime_root': str(args.runtime_root.resolve()),
                                    'staged_from_runtime_sha256': sha256_file(args.runtime_root / 'python314.dll'),
                                    'staged_python314_sha256': sha256_file(staged_runtime / 'python314.dll'),
                                    'build_tool_wheels': [{'filename': wheel.name, 'sha256': sha256_file(wheel)}
                                                          for wheel in staged_wheels]},
              'trust_bundle': {'source': certifi.artifact.as_dict(),
                               'wheel_sha256': certifi.artifact.expected_sha256,
                               'cacert_sha256': sha256_file(payload / 'trust/cacert.pem')},
              'runtime_files_verified': runtime_files_verified}
    report['offline_core'] = {'enabled': args.core_wheel_root is not None,
                              'artifact_count': len(dependency_lock.wheels) + (1 if args.core_wheel_root else 0),
                              'core_wheel_notice_files': core_notice_files}
    (output / 'build-report.json').write_bytes(encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k in ('archive_sha256', 'archive_bytes')}))


if __name__ == '__main__':
    main()
