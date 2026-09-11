"""Bounded Hugging Face downloads with explicit origin and redirect policy."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from .acquisition import AcquisitionPolicy, _rate_limit, verified_tls_context
from .model_resources import ModelFile, ModelSnapshot


ORIGIN_HOSTS = ('huggingface.co',)
# Exact HTTPS endpoints documented by Hugging Face for Hub/Xet downloads.
# A Hugging Face-looking suffix or an arbitrary CDN is not trusted.
STORAGE_HOSTS = (
    'cas-server.xethub.hf.co',
    'cas-server.xethub-eu.hf.co',
    'transfer.xethub.hf.co',
    'transfer.xethub-eu.hf.co',
    'us.aws.cdn.hf.co',
    'us.gcp.cdn.hf.co',
    'cdn-lfs-us-1.hf.co',
    'cdn-lfs-eu-1.hf.co',
)
HOSTS = ORIGIN_HOSTS + STORAGE_HOSTS
MAX_REDIRECTS = 5
ProgressCallback = Callable[[dict], None]


def _authority(url: str) -> tuple[urllib.parse.SplitResult, str]:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        parsed = urllib.parse.urlsplit('')
        host = ''
        port = -1
    return parsed, host if port in (None, 443) else f'{host}:{port}'


def _safe_endpoint(url: str) -> str:
    """Return a useful URL summary without any caller-controlled path material."""
    parsed, authority = _authority(url)
    count = len([piece for piece in parsed.path.split('/') if piece])
    return f'{parsed.scheme or "unknown"}://{authority or "unknown"}/<{count}-path-segments>'


class SourcePolicyError(ValueError):
    """Sanitized source-policy failure safe for durable journals and logs."""

    def __init__(self, *, rule: str, original_url: str, attempted_url: str,
                 hop: int, identity: str):
        _, origin = _authority(original_url)
        _, attempted = _authority(attempted_url)
        self.rule = rule
        self.original_host = origin or 'unknown'
        self.attempted_host = attempted or 'unknown'
        self.hop = hop
        self.identity = identity
        super().__init__(
            'Hugging Face source policy rejected '
            f'stage=model-download artifact={identity} origin={self.original_host} '
            f'attempted={self.attempted_host} hop={hop}/{MAX_REDIRECTS} '
            f'rule={rule} endpoint={_safe_endpoint(attempted_url)}'
        )


def validate_url(url: str, *, original_url: str | None = None, hop: int = 0,
                 identity: str = 'unspecified', origin: bool = False,
                 allowed_hosts: tuple[str, ...] = HOSTS) -> str:
    """Validate an initial Hub URL or a redirect using exact host membership."""
    original_url = original_url or url
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        raise SourcePolicyError(rule='invalid-authority', original_url=original_url,
                                attempted_url=url, hop=hop, identity=identity) from None
    if parsed.scheme.lower() != 'https':
        rule = 'https-required'
    elif parsed.username is not None or parsed.password is not None:
        rule = 'userinfo-forbidden'
    elif port not in (None, 443):
        rule = 'port-not-443'
    elif host not in ((tuple(value for value in ORIGIN_HOSTS if value in allowed_hosts))
                      if origin else allowed_hosts):
        rule = 'origin-host-not-approved' if origin else 'redirect-host-not-approved'
    else:
        return host
    raise SourcePolicyError(rule=rule, original_url=original_url, attempted_url=url,
                            hop=hop, identity=identity)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Permit only a short HTTPS chain to explicitly approved Hub storage hosts."""

    def __init__(self, policy: AcquisitionPolicy):
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = getattr(req, '_im_original_url', req.full_url)
        identity = getattr(req, '_im_identity', 'unspecified')
        hop = getattr(req, '_im_redirect_hop', 0) + 1
        if hop > MAX_REDIRECTS:
            raise SourcePolicyError(rule='redirect-limit-exceeded', original_url=original,
                                    attempted_url=newurl, hop=hop, identity=identity)
        host = validate_url(newurl, original_url=original, hop=hop, identity=identity,
                            allowed_hosts=self.policy.allowed_hosts)
        _rate_limit(host, self.policy, time.sleep)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Public pinned resources need no credentials. Authenticated acquisition
        # requires a separate reviewed flow rather than forwarding secrets.
        for name in ('Authorization', 'Cookie', 'Proxy-Authorization'):
            redirected.remove_header(name)
            redirected.unredirected_hdrs.pop(name, None)
            redirected.unredirected_hdrs.pop(name.lower(), None)
        redirected._im_original_url = original
        redirected._im_identity = identity
        redirected._im_redirect_hop = hop
        return redirected


class HuggingFaceSource:
    """One streaming request at a time, bounded retries and fail-closed redirects."""

    def __init__(self, *, ca_bundle: Path | None = None,
                 policy: AcquisitionPolicy | None = None, opener=None,
                 sleep=time.sleep, progress: ProgressCallback = lambda event: None):
        self.policy = policy or AcquisitionPolicy(HOSTS)
        if opener is None:
            if ca_bundle is None:
                raise ValueError('Hugging Face acquisition requires the delivered CA bundle')
            context = verified_tls_context(ca_bundle)
            transport = urllib.request.build_opener(
                SafeRedirect(self.policy), urllib.request.HTTPSHandler(context=context))
            opener = transport.open
        self.opener = opener
        self.sleep = sleep
        self.progress = progress

    def download(self, url: str, destination: Path, max_bytes: int,
                 *, identity: str = 'unspecified') -> None:
        host = validate_url(url, original_url=url, hop=0, identity=identity, origin=True,
                            allowed_hosts=self.policy.allowed_hosts)
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                _rate_limit(host, self.policy, self.sleep)
                request = urllib.request.Request(url, headers={'User-Agent': self.policy.user_agent})
                request._im_original_url = url
                request._im_identity = identity
                request._im_redirect_hop = 0
                with self.opener(request, timeout=self.policy.timeout_seconds) as response:
                    if getattr(response, 'status', 200) != 200:
                        raise OSError('unexpected provider response status')
                    header = response.headers.get('Content-Length', '') if getattr(response, 'headers', None) else ''
                    total = int(header) if header.isdigit() else None
                    if total is not None and total > min(max_bytes, self.policy.max_artifact_bytes):
                        raise ValueError('provider response exceeds size bound')
                    size = 0
                    last_report = -8 * 1024 * 1024
                    with destination.open('wb') as stream:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > min(max_bytes, self.policy.max_artifact_bytes):
                                raise ValueError('provider response exceeds size bound')
                            stream.write(chunk)
                            if size - last_report >= 8 * 1024 * 1024 or (total is not None and size == total):
                                self.progress({'kind': 'download', 'artifact': identity,
                                               'downloaded_bytes': size, 'total_bytes': total})
                                last_report = size
                self.progress({'kind': 'download', 'artifact': identity,
                               'downloaded_bytes': size, 'total_bytes': total, 'complete': True})
                return
            except SourcePolicyError:
                destination.unlink(missing_ok=True)
                raise
            except (OSError, urllib.error.URLError) as error:
                destination.unlink(missing_ok=True)
                delay = self.policy.base_backoff_seconds * 2 ** (attempt - 1)
                retryable = True
                if isinstance(error, urllib.error.HTTPError):
                    retryable = error.code in (408, 429, 500, 502, 503, 504)
                    retry_after = (error.headers or {}).get('Retry-After', '')
                    if retry_after.isdigit():
                        delay = max(delay, min(float(retry_after), 300))
                    error.close()
                if attempt == self.policy.max_attempts or not retryable:
                    # Signed storage URLs can contain credentials: omit the raw exception URL.
                    raise OSError(f'provider acquisition failed ({type(error).__name__}, attempt {attempt})') from None
                self.sleep(delay)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise

    def fetch_file(self, model: ModelSnapshot, item: ModelFile, destination: Path) -> None:
        validate_hf_identity(model.repository, model.revision)
        url = (f'https://huggingface.co/{model.repository}/resolve/{model.revision}/'
               f'{urllib.parse.quote(item.path)}')
        self.download(url, destination, item.size, identity=f'{model.resource_id}:{item.path}')


def validate_hf_identity(repository: str, revision: str) -> None:
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9._-]+', repository) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Hugging Face requires an explicit repository and immutable commit')


def hf_snapshot_target(shared_root: Path, model: ModelSnapshot) -> Path:
    validate_hf_identity(model.repository, model.revision)
    if model.source != 'huggingface':
        raise ValueError('wrong source adapter')
    return shared_root / 'huggingface' / 'hub' / ('models--' + model.repository.replace('/', '--')) / 'snapshots' / model.revision


def model_from_metadata(metadata: dict, *, repository: str, revision: str,
                        required_files: tuple[str, ...], resource_id: str,
                        compatibility: dict) -> ModelSnapshot:
    validate_hf_identity(repository, revision)
    if metadata.get('id') != repository or metadata.get('sha') != revision:
        raise ValueError('provider metadata identifies the wrong repository/revision')
    if metadata.get('gated') is not False or metadata.get('private') is not False or metadata.get('disabled'):
        raise ValueError('restricted provider artifact requires separate authorization flow')
    siblings = metadata.get('siblings', [])
    entries = {item['rfilename']: item for item in siblings}
    if len(entries) != len(siblings):
        raise ValueError('duplicate provider metadata filenames')
    files = []
    for name in sorted(required_files):
        if name not in entries:
            raise ValueError(f'missing required provider file: {name}')
        item = entries[name]
        lfs = item.get('lfs')
        if lfs and lfs['size'] != item['size']:
            raise ValueError('conflicting LFS size metadata')
        files.append({'path': name, 'size': item['size'],
                      'digest_kind': 'sha256' if lfs else 'git-blob-sha1',
                      'digest': lfs['sha256'] if lfs else item['blobId']})
    card = metadata.get('cardData', {})
    return ModelSnapshot.from_dict({
        'schema_version': 1, 'resource_id': resource_id, 'source': 'huggingface',
        'repository': repository, 'revision': revision, 'family': metadata.get('config', {}).get('model_type', ''),
        'files': files, 'provenance': {'metadata_url': f'https://huggingface.co/api/models/{repository}/revision/{revision}?blobs=true',
                                    'scope': 'explicit runtime-file projection of pinned revision',
                                    'excluded_repository_files': sorted(set(entries) - set(required_files))},
        'license': {'id': card.get('license', 'unknown'), 'reference': card.get('license_link', ''),
                    'distribution': 'acquire-from-provider; bundling requires notice review'},
        'compatibility': compatibility,
    })
