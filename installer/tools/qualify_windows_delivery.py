"""Repeatable standard-token delivery harness. Use only disposable roots and proof-cache copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import winreg


def registry_snapshot():
    result = {}
    def visit(hive, key, depth=0):
        name = str(hive) + '/' + key
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ) as handle:
                values = []
                index = 0
                while True:
                    try:
                        value = winreg.EnumValue(handle, index)
                        values.append(repr(value))
                        index += 1
                    except OSError:
                        break
                result[name] = hashlib.sha256(json.dumps(sorted(values)).encode()).hexdigest()
                if depth < 5:
                    index = 0
                    while True:
                        try:
                            child = winreg.EnumKey(handle, index)
                            index += 1
                        except OSError:
                            break
                        visit(hive, key + '\\' + child, depth + 1)
        except OSError as error:
            result[name] = type(error).__name__
    for hive, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                      (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, r'Software\Python'),
                      (winreg.HKEY_LOCAL_MACHINE, r'Software\Python'),
                      (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Python')):
        visit(hive, key)
    with winreg.OpenKey(winreg.HKEY_USERS, '') as users:
        index = 0
        while True:
            try:
                user = winreg.EnumKey(users, index)
                index += 1
            except OSError:
                break
            if user.startswith('S-1-5-21-') and not user.endswith('_Classes'):
                visit(winreg.HKEY_USERS, user + r'\Environment')
                visit(winreg.HKEY_USERS, user + r'\Software\Python')
    return result


def sanitized_environment(root):
    system = os.environ.get('SystemRoot', r'C:\Windows')
    environment = {'SystemRoot': system, 'WINDIR': system, 'COMSPEC': str(Path(system) / 'System32/cmd.exe'),
                   'PATH': str(Path(system) / 'System32') + os.pathsep + system}
    for key in ('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'HF_HOME', 'TORCH_HOME'):
        path = root / 'Context' / key
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment.update(PYTHONHOME=str(root / 'does-not-exist'), PYTHONPATH=str(root / 'Poison'),
                       VIRTUAL_ENV=str(root / 'does-not-exist'))
    assert not any(name in environment for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR',
                                                     'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'))
    return environment


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--delivery', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--cache', type=Path)
    p.add_argument('--model', type=Path)
    p.add_argument('--full', action='store_true')
    p.add_argument('--network-runtime', action='store_true',
                   help='Force the first runtime artifact through real HTTPS; other seeds remain reusable')
    p.add_argument('--network-model', action='store_true',
                   help='Acquire the pinned model over live HTTPS instead of copying the proof snapshot')
    p.add_argument('--model-failure-resume', action='store_true',
                   help='Inject a missing candidate file at Ensure model, then restore it and resume')
    p.add_argument('--motw', action='store_true', help='Add Internet-zone ADS to the disposable copy; no policy changes')
    args = p.parse_args()
    root = args.root.resolve()
    if root.exists():
        raise FileExistsError('Harness requires a fresh disposable root')
    if root == args.delivery.resolve() or args.delivery.resolve() in root.parents:
        raise ValueError('Acceptance must run outside the build/delivery input')
    root.mkdir(parents=True)
    before = registry_snapshot()
    (root / 'registry-before.json').write_text(json.dumps(before, indent=2))
    shutil.copytree(args.delivery, root / 'Delivered')
    executable = root / 'Delivered/LIC Install Manager.exe'
    if args.motw:
        Path(str(executable) + ':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n', encoding='utf-8')
    env = sanitized_environment(root)
    poison = root / 'Poison/install_manager'
    poison.mkdir(parents=True)
    (poison / '__init__.py').write_text("raise RuntimeError('Source/import leakage detected')\n")
    (root / 'lic_model_probe.py').write_text("raise RuntimeError('CWD probe leakage detected')\n")
    groups = subprocess.check_output([str(Path(env['SystemRoot']) / 'System32/whoami.exe'), '/groups'], text=True)
    (root / 'token.txt').write_text(groups)
    if 'S-1-16-8192' not in groups or 'S-1-5-32-544' in groups:
        raise RuntimeError('Acceptance requires a medium-integrity nonadministrator token')
    assert shutil.which('python', path=env['PATH']) is None
    assert shutil.which('py', path=env['PATH']) is None
    def run(arguments):
        return subprocess.run([str(executable), *arguments], cwd=root, env=env, check=True, timeout=60)
    run(['--root', str(root / 'Self Test'), '--self-test'])
    evidence = json.loads((root / 'Self Test/delivery-check.json').read_text())
    if not evidence['frozen'] or any(str(root / 'Delivered') not in p for p in evidence['sys_path']):
        raise AssertionError('frozen import paths escaped delivery directory')
    if any(path and not Path(path).is_relative_to(root / 'Delivered') for path in evidence['modules'].values()):
        raise AssertionError('implementation module escaped delivered artifact')
    if (not evidence['tls']['check_hostname'] or evidence['tls']['verify_mode'] != 2
            or evidence['tls']['cert_store_stats']['x509_ca'] < 100
            or any(evidence['tls']['external_trust_environment'].values())
            or not Path(evidence['tls']['ca_bundle']).is_relative_to(root / 'Delivered')):
        raise AssertionError('Frozen TLS context did not load the delivered strict CA trust bundle')
    run(['--root', str(root / 'UI Test'), '--ui-probe'])
    ui = json.loads((root / 'UI Test/ui.json').read_text())
    assert ui['visible'] and not ui['consent_default'] and not ui['installation_started']
    result = {'standard_user': True, 'isolation': 'Sanitized disposable root under existing nonadministrator sandbox account; not a VM or fresh account',
              'no_system_python_on_path': True, 'no_source_imports': True, 'ui': ui,
              'activation_preflight_passed': False, 'activated': False,
              'motw_createprocess_test': args.motw,
              'smartscreen_shell_reputation_test': 'Not exercised by direct CreateProcess; external release gate'}
    if args.full:
        if not args.cache or (not args.network_model and not args.model):
            raise ValueError('Full qualification requires a proof cache and either a model copy or --network-model')
        if args.network_model and args.model_failure_resume:
            raise ValueError('The controlled model failure uses a local proof snapshot, not live network content')
        # Copies are outside the checkout. The executable never reads development paths.
        shutil.copytree(args.cache, root / 'Seed Cache')
        if args.network_runtime:
            shutil.rmtree(root / 'Seed Cache/verified/pythoncore-win-x64/3.14.6', ignore_errors=True)
        model_seed = None
        if not args.network_model:
            model_seed = root / 'Seed Model' / args.model.name
            shutil.copytree(args.model, model_seed)
        install = root / 'Install é'
        command = [str(executable), '--root', str(install), '--accept',
                   '--cache-source', str(root / 'Seed Cache')]
        if model_seed is not None:
            command.extend(['--model-source', str(model_seed)])
        process = subprocess.Popen(command, cwd=root, env=env)
        journal = install / 'State/operations/bootstrap.json'
        deadline = time.monotonic() + 120
        interrupted = False
        while process.poll() is None and time.monotonic() < deadline:
            if journal.exists():
                try:
                    data = json.loads(journal.read_text())
                    steps = {s['name']: s['status'] for s in data['steps']}
                    if steps['acquire_runtime'] == 'completed' and steps['acquire_dependencies'] == 'running':
                        process.terminate()
                        process.wait(timeout=30)
                        interrupted = True
                        break
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(.02)
        if not interrupted:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
            raise RuntimeError('Could not qualify intended interruption boundary; do not silently rerun inference')
        shutil.copyfile(journal, root / 'interrupted-journal.json')
        interrupted_data = json.loads((root / 'interrupted-journal.json').read_text())
        runtime_evidence = next(s['evidence'] for s in interrupted_data['steps']
                                if s['name'] == 'acquire_runtime')
        if args.network_runtime and (runtime_evidence['reused'] or runtime_evidence['attempts'] < 1):
            raise AssertionError('Runtime did not traverse fresh HTTPS acquisition')
        sentinel = install / 'unrelated-keep.txt'
        sentinel.write_text('Preserve this unrelated file')
        if args.model_failure_resume:
            held = root / 'held-model-file'
            missing = model_seed / 'config.json'
            shutil.move(missing, held)
            failure = subprocess.run(command + ['--resume'], cwd=root, env=env,
                                     check=False, timeout=1800)
            if failure.returncode == 0:
                raise AssertionError('Controlled model failure unexpectedly succeeded')
            failed = json.loads(journal.read_text())
            states = {step['name']: step['status'] for step in failed['steps']}
            if (states['ensure_model'] != 'failed' or failed['final_validation'] is not None
                    or any(states[name] != 'completed' for name in
                           ('acquire_runtime', 'acquire_dependencies', 'extract_runtime',
                            'create_venv', 'install_dependencies', 'stage_lic'))):
                raise AssertionError('Model failure did not preserve the expected M1.5 resume boundary')
            shutil.move(held, missing)
        subprocess.run(command + ['--resume'], cwd=root, env=env, check=True, timeout=1800)
        data = json.loads(journal.read_text())
        assert data['status'] == 'succeeded' and sentinel.read_text() == 'Preserve this unrelated file'
        final = data['final_validation']
        assert final['activation_preflight_passed'] and not final['activated']
        subprocess.run([str(executable), '--root', str(install), '--activate'], cwd=root, env=env,
                       check=True, timeout=300)
        active_path = install / 'State/installations/lic-lite.json'
        active = json.loads(active_path.read_text(encoding='utf-8'))
        activation_journal = json.loads((install / 'State/operations/activation.json').read_text(encoding='utf-8'))
        if (active['state'] != 'active' or activation_journal['status'] != 'succeeded'
                or not Path(active['manager']).is_file() or not Path(active['python']).is_file()
                or not Path(active['application'], 'app.py').is_file()):
            raise AssertionError('Activation did not publish a complete managed launch contract')
        # A second invocation exercises the installed manager, not the delivered copy.
        subprocess.run([active['manager'], '--root', str(install), '--activate'], cwd=root, env=env,
                       check=True, timeout=300)
        result.update(activation_preflight_passed=True, activated=True, interrupted_then_resumed=True,
                      state='active', journal=str(journal), activation_journal=str(install / 'State/operations/activation.json'),
                      installed_manager=active['manager'], manager_restart=True,
                      start_menu_shortcut=active['choices']['start_menu'], desktop_shortcut=active['choices']['desktop'],
                      launch_contract={'python': active['python'], 'cwd': active['application'], 'module': 'app.py'},
                      unrelated_preserved=True)
        result['runtime_acquired_over_https'] = bool(args.network_runtime)
        result['model_acquired_over_https'] = bool(args.network_model)
        result['model_failure_then_resumed'] = bool(args.model_failure_resume)
    after = registry_snapshot()
    (root / 'registry-after.json').write_text(json.dumps(after, indent=2))
    result['registry_unchanged'] = before == after
    if before != after:
        raise AssertionError('Global environment/Python registry observations changed')
    (root / 'acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
