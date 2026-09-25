"""Frozen entrypoint. Only embedded metadata and fixed operations are accepted."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import ssl
import sys

from .acquisition import verified_tls_context
from .bootstrap import disclosure, execute
from .bootstrap_layout import validate_root, layout
from .component_catalog import default_install_root
from .first_launch import show, show_manager
from .managed_install import activate, launch
from .core_repair import execute as repair_activated_core
from .component_operations import execute as install_managed_component
from .probe_guard import isolated_environment
from .release_channel import verify_delivery


def main():
    from build_identity import IDENTITY, INDEX_SHA256
    delivery = Path(sys._MEIPASS)
    verify_delivery(delivery, INDEX_SHA256)
    parser = argparse.ArgumentParser(description='Install, activate and launch managed LIC Lite')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--accept', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--ui-probe', action='store_true')
    parser.add_argument('--cache-source', type=Path)
    parser.add_argument('--model-source', type=Path)
    parser.add_argument('--model-root', type=Path)
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--manage', action='store_true')
    parser.add_argument('--desktop-shortcut', action='store_true')
    parser.add_argument('--no-start-menu-shortcut', action='store_true')
    parser.add_argument('--ui-review', choices=('first-run', 'installed'))
    parser.add_argument('--ui-review-page', choices=('Install & Update', 'Details', 'Help'))
    parser.add_argument('--ui-review-state', choices=('core-not-installed', 'core-ready', 'optional-not-installed',
                                                      'optional-installed', 'optional-existing', 'active-download',
                                                      'interrupted', 'florence-parent-discovered', 'mediapipe-existing',
                                                      'ffmpeg-selected', 'recovery-matching', 'recovery-mismatch',
                                                      'recovery-restored', 'insightface-existing',
                                                      'core-ready-florence-absent', 'florence-absent',
                                                      'florence-existing', 'legacy-florence',
                                                      'core-disclosure', 'network-error',
                                                      'verification-error', 'paused'))
    args = parser.parse_args()
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('Run this qualification as a standard user, without elevation')
    proposed = args.root or default_install_root()

    def prepare(root, model_root, resume, status, cancel_requested=lambda: False):
        root = validate_root(root, delivery=delivery.parent, resume=resume)
        # Execution reserves/journals the root first. Process-local context is created in that owned root.
        return execute(delivery, root, cache_source=args.cache_source, model_source=args.model_source,
                       model_root=model_root, resume=resume, status=status, boundary=lambda step: None,
                       cancel_requested=cancel_requested)

    def install_component(component_id, root, model_root, resume, status, cancel_requested):
        return install_managed_component(
            delivery, root, component_id, model_root, cache_source=args.cache_source,
            resume=resume, status=status, cancel_requested=cancel_requested)

    def activate_installed_core(chosen, models, start, desktop):
        return activate(delivery, chosen, Path(sys.executable).parent,
                        model_root=models, start_menu=start, desktop=desktop)

    def repair_core(root, resume, status, pause_requested):
        return repair_activated_core(delivery, root, resume=resume,
                                     cache_source=args.cache_source,
                                     status=status, pause_requested=pause_requested)

    # Sanitize before any managed subprocess; these are process-local variables, not registry changes.
    system = os.environ.get('SystemRoot', r'C:\Windows')
    keep = {k: v for k, v in os.environ.items() if k.upper() in
            {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE'}}
    keep['PATH'] = str(Path(system) / 'System32') + os.pathsep + system
    keep['PYTHONDONTWRITEBYTECODE'] = '1'
    keep['PYTHONNOUSERSITE'] = '1'
    keep['TCL_LIBRARY'] = str(delivery / '_tcl_data')
    keep['TK_LIBRARY'] = str(delivery / '_tk_data')
    # Preserve only reviewed system/bootstrap variables in this process.
    for name in tuple(os.environ):
        if name.upper() not in {k.upper() for k in keep}:
            os.environ.pop(name, None)
    os.environ.update(keep)
    if args.ui_review:
        page = args.ui_review_page
        if args.ui_review == 'first-run':
            show(delivery, proposed, prepare,
                 lambda chosen, models, start, desktop: None, lambda chosen: None,
                 install_component=install_component, repair_core=repair_core,
                 review_mode=True, initial_page=page, review_scenario=args.ui_review_state,
                 initial_model_root=args.model_root)
        else:
            record = {'schema_version': 1, 'state': 'active', 'root': proposed.as_posix(),
                      'application': str(layout(proposed)['application'] / 'extracted'),
                      'python': str(layout(proposed)['venv'] / 'Scripts/python.exe'),
                      'manager': str(proposed / 'Manager/current/LIC Install Manager.exe'),
                      'model_root': str(args.model_root or layout(proposed)['models']),
                      'choices': {'start_menu': True, 'desktop': False}, 'shortcuts': {},
                      'channel': {'version': '0.28.4+m17.1'}}
            show_manager(delivery, proposed, lambda chosen: None,
                         prepare=prepare, activate=lambda chosen, models, start, desktop: None,
                         install_component=install_component, repair_core=repair_core,
                         record=record, review_mode=True, initial_page=page,
                         review_scenario=args.ui_review_state or 'core-ready')
        return 0
    if args.launch:
        launch(proposed)
        return 0
    if args.manage:
        show_manager(delivery, proposed, launch, prepare=prepare, activate=activate_installed_core,
                     install_component=install_component, repair_core=repair_core)
        return 0
    if args.self_test or args.plan or args.ui_probe:
        root = validate_root(proposed, delivery=delivery.parent)
        root.mkdir(parents=True, exist_ok=False)
        tls = verified_tls_context(delivery / 'trust/cacert.pem')
        evidence = {'frozen': bool(getattr(sys, 'frozen', False)), 'executable': sys.executable,
                    'sys_path': sys.path, 'implementation': __file__, 'build': IDENTITY,
                    'path': os.environ['PATH'], 'admin': False, 'activated': False,
                    'tls': {'ca_bundle': str(delivery / 'trust/cacert.pem'),
                            'verify_mode': int(tls.verify_mode), 'check_hostname': tls.check_hostname,
                            'cert_store_stats': tls.cert_store_stats(),
                            'openssl_default_paths': ssl.get_default_verify_paths()._asdict(),
                            'external_trust_environment': {
                                name: name in os.environ for name in
                                ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')}},
                    'plan': disclosure(delivery, root, model_root=args.model_root),
                    'modules': {n: getattr(m, '__file__', None) for n, m in sys.modules.items()
                                if n.startswith('install_manager')}}
        (root / 'delivery-check.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        if args.ui_probe:
            try:
                show(delivery, proposed.parent / 'New LIC Test', prepare,
                     lambda chosen, models, start, desktop: activate(delivery, chosen, Path(sys.executable).parent,
                                                                    model_root=models, start_menu=start, desktop=desktop),
                     lambda chosen: launch(chosen), install_component=install_component,
                     repair_core=repair_core,
                     ui_probe=root / 'ui.json', quiet=True)
            except Exception as error:
                (root / 'failure.json').write_text(json.dumps({'error': str(error)}), encoding='utf-8')
                raise
        return 0
    if args.activate:
        activate(delivery, proposed, Path(sys.executable).parent,
                 model_root=args.model_root, start_menu=not args.no_start_menu_shortcut,
                 desktop=args.desktop_shortcut)
    elif args.accept:
        prepare(proposed, args.model_root or layout(proposed)['models'], args.resume, lambda message: None)
    else:
        if (proposed / 'State/installations/lic-lite.json').is_file():
            show_manager(delivery, proposed, launch, prepare=prepare, activate=activate_installed_core,
                         install_component=install_component, repair_core=repair_core)
        else:
            show(delivery, proposed, prepare,
                 activate_installed_core,
                 lambda chosen: launch(chosen), install_component=install_component,
                 repair_core=repair_core)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
