"""Fixed LIC integration probe, invoked only in an isolated disposable subprocess."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

# -I excludes cwd/PYTHONPATH: only this reviewed manager package and staged LIC are added.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_manager.probe_guard import ProbeGuard


def caption(snapshot: Path, state: Path) -> dict:
    import torch
    from PIL import Image, ImageDraw
    import florence_analyzer as florence

    image = Image.new('RGB', (256, 256), 'white')
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((24, 64, 112, 192), fill='red')
    drawing.ellipse((144, 80, 232, 168), fill='blue')
    fixture = state / 'synthetic-shapes.png'
    image.save(fixture)
    device, dtype = florence.choose_device_and_dtype()
    if not device.startswith('cuda'):
        raise RuntimeError('the qualified LIC profile requires CUDA')
    processor, model = florence.load_florence(str(snapshot), device, dtype, allow_model_download=False)
    calls = []
    original_generate = model.generate

    def one_generate(*args, **kwargs):
        calls.append(True)
        if len(calls) != 1:
            raise RuntimeError('more than one caption inference was requested')
        return original_generate(*args, **kwargs)

    model.generate = one_generate
    result = florence.run_florence_task(model, processor, image, florence.CAPTION_TASK, device, dtype)
    if not isinstance(result, str) or not result.strip() or len(calls) != 1:
        raise RuntimeError('Florence did not return one nonempty caption')
    return {'passed': True, 'caption': result, 'inference_count': len(calls),
            'task': florence.CAPTION_TASK, 'device': device,
            'model_class': type(model).__module__, 'processor_class': type(processor).__module__,
            'snapshot': str(snapshot), 'input_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'input_description': '256x256 white canvas, red rectangle on left, blue circle on right',
            'torch': torch.__version__}


def readiness(snapshot: Path, state: Path) -> dict:
    import tkinter as tk
    from tkinter import messagebox
    from unittest.mock import patch
    import app
    import catalog
    import florence_analyzer
    import provider_setup
    import settings_manager

    settings_path = settings_manager.get_settings_path().resolve()
    if state not in settings_path.parents or settings_path.exists():
        raise RuntimeError('readiness requires fresh disposable settings')
    status = provider_setup.inspect_florence_cache()
    if not status.model_ready or status.snapshot_path.resolve() != snapshot:
        raise RuntimeError('LIC did not resolve the managed Florence snapshot')
    database = state / 'dataset_tools.db'
    with catalog.Catalog(database) as empty_catalog:
        count = empty_catalog.connection.execute('SELECT count(*) FROM files').fetchone()[0]
    if count:
        raise RuntimeError('disposable catalog was not empty')
    root = tk.Tk()
    root.withdraw()
    application = None
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    try:
        # Any dialog is a failure; no invisible prompt may hang or receive fabricated consent.
        with patch.multiple(messagebox, **{name: unexpected_prompt for name in
                ('showinfo', 'showwarning', 'showerror', 'askquestion', 'askokcancel',
                 'askyesno', 'askyesnocancel', 'askretrycancel')}):
            application = app.DatasetToolsApp(root)
            root.withdraw()
            root.update_idletasks()
            root.update()
            idle = application.worker_thread is None and not application.dataset_readiness.is_running
            if not idle or errors:
                raise RuntimeError('LIC readiness failed or unexpectedly started work')
            display = application.current_work_var.get()
            application._on_close()
        if not settings_path.is_file() or not application._closing:
            raise RuntimeError('LIC did not shut down cleanly')
    finally:
        if application is not None and not application._closing:
            application._finish_close()
        elif application is None:
            root.destroy()
    return {'passed': True, 'entry_point': str(Path(app.__file__).resolve()),
            'settings_path': str(settings_path), 'empty_catalog': str(database),
            'catalog_rows': count, 'model_resolved': str(status.snapshot_path),
            'florence_repository': florence_analyzer.MODEL_NAME,
            'florence_revision': florence_analyzer.MODEL_REVISION,
            'no_analysis_started': idle, 'current_work': display,
            'tk_initialized': True, 'clean_shutdown': True,
            'scope': 'real application constructor and withdrawn Tk; first-launch notice UI not exercised'}


def core_readiness(state: Path) -> dict:
    """Launch the real baseline UI with provider packages and models absent."""
    import tkinter as tk
    from tkinter import messagebox
    from unittest.mock import patch
    import app
    import catalog
    import settings_manager

    settings_path = settings_manager.get_settings_path().resolve()
    if state not in settings_path.parents or settings_path.exists():
        raise RuntimeError('core readiness requires fresh disposable settings')
    database = state / 'dataset_tools.db'
    with catalog.Catalog(database) as empty_catalog:
        count = empty_catalog.connection.execute('SELECT count(*) FROM files').fetchone()[0]
    if count:
        raise RuntimeError('disposable catalog was not empty')
    root = tk.Tk()
    root.withdraw()
    application = None
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    try:
        with patch.multiple(messagebox, **{name: unexpected_prompt for name in
                ('showinfo', 'showwarning', 'showerror', 'askquestion', 'askokcancel',
                 'askyesno', 'askyesnocancel', 'askretrycancel')}):
            application = app.DatasetToolsApp(root)
            root.withdraw()
            root.update_idletasks()
            root.update()
            idle = application.worker_thread is None and not application.dataset_readiness.is_running
            if not idle or errors:
                raise RuntimeError('LIC core readiness failed or unexpectedly started work')
            display = application.current_work_var.get()
            application._on_close()
        if not settings_path.is_file() or not application._closing:
            raise RuntimeError('LIC did not shut down cleanly')
    finally:
        if application is not None and not application._closing:
            application._finish_close()
        elif application is None:
            root.destroy()
    return {'passed': True, 'entry_point': str(Path(app.__file__).resolve()),
            'settings_path': str(settings_path), 'empty_catalog': str(database),
            'catalog_rows': count, 'optional_provider_required': False,
            'no_analysis_started': idle, 'current_work': display,
            'tk_initialized': True, 'clean_shutdown': True,
            'scope': 'provider-neutral application constructor and withdrawn Tk'}


def unexpected_prompt(*args, **kwargs):
    raise RuntimeError('unexpected LIC dialog during readiness')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('caption', 'readiness', 'core-readiness'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    state, output = args.state.resolve(), args.output.resolve()
    if state not in output.parents:
        raise ValueError('probe report escapes disposable state root')
    guard = ProbeGuard(state)
    guard.install()
    sys.path.insert(0, str(args.source.resolve()))
    started = time.monotonic()
    try:
        if args.action == 'core-readiness':
            result = core_readiness(state)
        else:
            if args.snapshot is None:
                raise ValueError('the Florence probes require --snapshot')
            result = (caption if args.action == 'caption' else readiness)(args.snapshot.resolve(), state)
    except Exception as error:
        result = {'passed': False, 'error': f'{type(error).__name__}: {error}'}
    result.update(functional_passed=result['passed'], network_attempts=guard.network_attempts, denied_writes=guard.denied_writes,
                  executable=str(Path(sys.executable).resolve()), prefix=str(Path(sys.prefix).resolve()),
                  elapsed_seconds=round(time.monotonic()-started, 3))
    result['passed'] = result['passed'] and not guard.network_attempts and not guard.denied_writes
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
