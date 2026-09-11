"""Conservative local release hygiene checks. Findings never rewrite source or history."""
import json
from pathlib import Path
import re
import subprocess

PATTERNS = {
    'personal-windows-path': re.compile(r'[A-Za-z]:[\\/]+(?:Users|Design)[\\/][^\s"\r\n]+', re.I),
    'credential-url': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|hf_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b'),
    'assigned-secret': re.compile(r'(?i)(?:api_key|access_token|password|client_secret)\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/-]{16,}[\x22\x27]'),
}
EXCLUDED = {'reports', '.lic-repair', 'logs', 'state', 'cache', '__pycache__', '.pytest_cache', 'venv', '.venv', '.git'}


def scan_content(name: str, content: bytes) -> list[dict]:
    findings = []
    path = Path(name)
    if set(path.parts) & EXCLUDED:
        findings.append({'file': name, 'rule': 'private-generated-or-debug-path'})
    if path.suffix.lower() in {'.safetensors', '.onnx', '.task', '.db', '.sqlite', '.sqlite3', '.log', '.pyc'}:
        findings.append({'file': name, 'rule': 'model-data-or-generated-file'})
    if path.name.lower() == 'settings.json':
        findings.append({'file': name, 'rule': 'user-settings'})
    if len(content) > 100 * 1024**2:
        findings.append({'file': name, 'rule': 'large-binary'})
    if path.name.lower() in {'license', 'license.md', 'license.txt', 'copying'} and len(path.parts) == 1:
        findings.append({'file': name, 'rule': 'unintended-manager-license-review'})
    if b'\0' not in content[:8192]:
        text = content.decode('utf-8', errors='replace')
        for rule, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append({'file': name, 'rule': rule})  # Never echo possible credentials.
    return findings


def repository_preflight(root: Path, *, history=True) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    findings = []
    tracked = git('ls-files', '-z').decode().split('\0')
    for name in filter(None, tracked):
        path = root / name
        if not path.is_file():
            findings.append({'file': name, 'rule': 'missing-tracked-file'})
        else:
            findings.extend(scan_content(name, path.read_bytes()))
    historical = []
    seen = set()
    if history:
        for commit in git('rev-list', '--all').decode().splitlines():
            for line in git('ls-tree', '-rz', commit).split(b'\0'):
                if not line:
                    continue
                header, name = line.split(b'\t', 1)
                _, kind, oid = header.split()
                if kind != b'blob' or (oid, name) in seen:
                    continue
                seen.add((oid, name))
                for item in scan_content(name.decode(), git('cat-file', 'blob', oid.decode())):
                    historical.append({**item, 'commit': commit, 'blob': oid.decode()})
    remotes = git('remote').decode().splitlines()
    return {'tracked_findings': findings, 'history_findings': historical,
            'history_blobs_checked': len(seen), 'remote_names': remotes,
            'remote_review_required': bool(remotes), 'customer_source_release_passed': not(findings or historical or remotes),
            'scope': 'Obvious pattern/content checks; not a complete secrets or legal review. No upload or history rewrite.'}


def payload_preflight(root: Path) -> dict:
    findings = []
    for path in sorted(root.rglob('*')):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            # Standard-library bytecode is intentionally bundled; this scanner checks our explicit payload.
            findings.extend(scan_content(name, path.read_bytes()))
    return {'passed': not findings, 'findings': findings}


def validate_notices(inventory: dict, root: Path) -> None:
    if inventory.get('schema_version') != 1 or not inventory.get('components'):
        raise ValueError('missing third-party inventory')
    for item in inventory['components']:
        if not all(item.get(k) for k in ('project', 'version', 'license', 'source', 'delivery', 'redistribution', 'commercial')):
            raise ValueError('incomplete component inventory')
        if item['delivery'] == 'bundled-in-installer':
            if not item.get('notices') or any(not (root / n).is_file() for n in item['notices']):
                raise ValueError('missing bundled notices: ' + item['project'])
