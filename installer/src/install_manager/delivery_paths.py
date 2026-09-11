"""Resolve only delivered fixed probe files, never a development checkout fallback."""
from pathlib import Path
import sys


def probe_file(name: str) -> Path:
    if name not in {'installed_integrity_probe.py', 'lic_model_probe.py'}:
        raise ValueError('unknown fixed probe')
    root = (Path(sys._MEIPASS) / 'probes' / 'install_manager'
            if getattr(sys, 'frozen', False) else Path(__file__).parent)
    path = root / name
    if not path.is_file():
        raise FileNotFoundError('Delivered validation probe is missing: ' + name)
    return path.resolve()
