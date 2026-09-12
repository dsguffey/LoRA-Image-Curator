"""Small HTTPS acquisition primitive with verified cache promotion."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import socket
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .artifacts import AcquiredArtifact, ArtifactDescriptor
from .process_lock import process_lock


class AcquisitionCancelled(RuntimeError):
    """A caller requested cancellation at a safe acquisition boundary."""


class AcquisitionFailure(OSError):
    """Sanitized structured transport failure for later customer-facing UX."""

    def __init__(self, category: str, artifact_id: str, host: str, attempts: int,
                 detail: str):
        self.category = category
        self.artifact_id = artifact_id
        self.host = host
        self.attempts = attempts
        self.detail = detail
        super().__init__(f"artifact acquisition failed: category={category} artifact={artifact_id} "
                         f"host={host} attempts={attempts} detail={detail}")


def classify_acquisition_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return "not-found"
        if 500 <= error.code <= 599:
            return "server-error"
        return "http-error"
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError):
        return "tls-certificate"
    if isinstance(reason, socket.gaierror):
        return "dns-unavailable"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    return "network-unavailable"


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    """Global limits that artifact metadata cannot weaken."""

    allowed_hosts: tuple[str, ...]
    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    minimum_request_interval_seconds: float = 0.25
    timeout_seconds: float = 60.0
    max_artifact_bytes: int = 8 * 1024 * 1024 * 1024
    user_agent: str = "InstallManager/0.2 (+local-user-controlled-acquisition)"

    def __post_init__(self) -> None:
        if not self.allowed_hosts or not 1 <= self.max_attempts <= 5:
            raise ValueError("acquisition policy requires hosts and 1-5 attempts")
        if self.base_backoff_seconds < 0 or self.minimum_request_interval_seconds < 0:
            raise ValueError("acquisition delays cannot be negative")
        if self.timeout_seconds <= 0 or self.max_artifact_bytes <= 0:
            raise ValueError("acquisition limits must be positive")


_lock_guard = threading.Lock()
_host_last_request: dict[str, float] = {}


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy):
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != 'https' or url.hostname not in self.policy.allowed_hosts
                or url.username or url.password or url.port not in (None, 443)):
            raise ValueError('untrusted artifact redirect')
        _rate_limit(url.hostname, self.policy, time.sleep)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _artifact_lock(path: Path):
    return process_lock(path)


def _rate_limit(host: str, policy: AcquisitionPolicy, sleep: Callable[[float], None]) -> None:
    with _lock_guard:
        now = time.monotonic()
        wait = policy.minimum_request_interval_seconds - (now - _host_last_request.get(host, 0.0))
        if wait > 0:
            sleep(wait)
        _host_last_request[host] = time.monotonic()


def _target_path(cache_root: Path, descriptor: ArtifactDescriptor) -> Path:
    root = cache_root.expanduser().resolve()
    target = (root / "verified" / descriptor.artifact_id / descriptor.version / descriptor.filename).resolve()
    if root not in target.parents:
        raise ValueError("artifact cache path escapes the approved cache root")
    return target


def _open(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


def verified_tls_context(ca_bundle: Path) -> ssl.SSLContext:
    """Build strict TLS trust from Windows roots plus the delivered pinned CA bundle."""
    bundle = ca_bundle.resolve()
    if not bundle.is_file():
        raise FileNotFoundError('Delivered CA bundle is missing')
    context = ssl.create_default_context()
    # Additive: retain applicable Windows/user roots while making clean-machine
    # operation independent of whether Windows has populated every public root.
    context.load_verify_locations(cafile=str(bundle))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('TLS peer and hostname verification are required')
    return context


def acquire_artifact(descriptor: ArtifactDescriptor, cache_root: Path, policy: AcquisitionPolicy,
                     *, ca_bundle: Path | None = None, opener: Callable = _open,
                     sleep: Callable[[float], None] = time.sleep,
                     progress: Callable[[dict], None] = lambda event: None,
                     partial_observer: Callable[[Path, str], None] = lambda path, state: None,
                     keep_partial_on_cancel: bool = False) -> AcquiredArtifact:
    """Acquire once, verify fully, then atomically promote into the verified cache."""
    descriptor.validate(policy.allowed_hosts)
    if opener is _open:
        if ca_bundle is None:
            raise ValueError('HTTPS artifact acquisition requires the delivered CA bundle')
        context = verified_tls_context(ca_bundle)
        transport = urllib.request.build_opener(ArtifactRedirect(policy),
                                                 urllib.request.HTTPSHandler(context=context))
        opener = lambda request, timeout: transport.open(request, timeout=timeout)
    target = _target_path(cache_root, descriptor)
    lock = _artifact_lock(target)
    with lock:
        if target.is_file():
            digest = sha256_file(target)
            size = target.stat().st_size
            if digest != descriptor.expected_sha256 or (
                    descriptor.expected_size is not None and size != descriptor.expected_size):
                raise ValueError("existing verified-cache path does not match its descriptor")
            return AcquiredArtifact(descriptor, target.as_posix(), digest, size,
                                    "verified", True, True, 0)
        if target.exists():
            raise ValueError("verified-cache target exists but is not a file")
        partial_root = (cache_root.expanduser().resolve() / "partial").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        partial_root.mkdir(parents=True, exist_ok=True)
        partial = partial_root / f"{descriptor.artifact_id}-{uuid.uuid4().hex}.partial"
        partial_observer(partial, "created")
        host = urllib.parse.urlparse(descriptor.url).hostname or ""
        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                _rate_limit(host, policy, sleep)
                request = urllib.request.Request(descriptor.url, headers={"User-Agent": policy.user_agent})
                digest = hashlib.sha256()
                size = 0
                with opener(request, policy.timeout_seconds) as response, partial.open("wb") as output:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise OSError(f"unexpected HTTP status {status}")
                    headers = getattr(response, 'headers', None)
                    header = headers.get('Content-Length', '') if headers else ''
                    total = int(header) if header.isdigit() else descriptor.expected_size
                    last_report = -8 * 1024 * 1024
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > policy.max_artifact_bytes:
                            raise ValueError("artifact exceeds the configured size limit")
                        output.write(chunk)
                        digest.update(chunk)
                        if size - last_report >= 8 * 1024 * 1024 or (total is not None and size == total):
                            progress({'kind': 'download', 'artifact': descriptor.artifact_id,
                                      'downloaded_bytes': size, 'total_bytes': total})
                            last_report = size
                    output.flush()
                    os.fsync(output.fileno())
                actual = digest.hexdigest()
                if descriptor.expected_size is not None and size != descriptor.expected_size:
                    raise ValueError(f"artifact size mismatch: expected {descriptor.expected_size}, got {size}")
                if actual != descriptor.expected_sha256:
                    raise ValueError("artifact SHA-256 mismatch")
                progress({'kind': 'download', 'artifact': descriptor.artifact_id,
                          'downloaded_bytes': size, 'total_bytes': total, 'complete': True})
                os.replace(partial, target)
                partial_observer(partial, "validated")
                return AcquiredArtifact(descriptor, target.as_posix(), actual, size,
                                        "verified", True, False, attempt)
            except ValueError:
                partial.unlink(missing_ok=True)
                partial_observer(partial, "discarded")
                raise
            except AcquisitionCancelled:
                # Keeping a partial means only preserving the bytes for inspection;
                # generic range-resume is deliberately not promised.
                if keep_partial_on_cancel:
                    partial_observer(partial, "unvalidated")
                else:
                    partial.unlink(missing_ok=True)
                    partial_observer(partial, "discarded")
                raise
            except urllib.error.HTTPError as error:
                last_error = error
                partial.unlink(missing_ok=True)
                partial_observer(partial, "discarded")
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                error.close()
                if attempt < policy.max_attempts:
                    server_delay = min(float(retry_after), 300.0) if retry_after.isdigit() else 0.0
                    sleep(max(policy.base_backoff_seconds * (2 ** (attempt - 1)), server_delay))
            except (OSError, urllib.error.URLError) as error:
                last_error = error
                partial.unlink(missing_ok=True)
                partial_observer(partial, "discarded")
                if attempt < policy.max_attempts:
                    sleep(policy.base_backoff_seconds * (2 ** (attempt - 1)))
        category = classify_acquisition_error(last_error or OSError("unknown transport failure"))
        detail = (f"HTTP {last_error.code}" if isinstance(last_error, urllib.error.HTTPError)
                  else type(last_error).__name__ if last_error else "unknown")
        raise AcquisitionFailure(category, descriptor.artifact_id, host,
                                 policy.max_attempts, detail) from None


def admit_local_artifact(descriptor: ArtifactDescriptor, candidate: Path, cache_root: Path,
                         policy: AcquisitionPolicy) -> AcquiredArtifact:
    """Verify a qualification/manual candidate before copying it into the trusted cache."""
    descriptor.validate(policy.allowed_hosts)
    candidate = candidate.expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    digest = sha256_file(candidate)
    size = candidate.stat().st_size
    if digest != descriptor.expected_sha256:
        raise ValueError("local artifact SHA-256 mismatch")
    if descriptor.expected_size is not None and size != descriptor.expected_size:
        raise ValueError("local artifact size mismatch")
    target = _target_path(cache_root, descriptor)
    with _artifact_lock(target):
        if target.is_file():
            existing_digest = sha256_file(target)
            if existing_digest != digest or target.stat().st_size != size:
                raise ValueError("existing verified-cache path conflicts with local artifact")
            return AcquiredArtifact(descriptor, target.as_posix(), digest, size,
                                    "verified", True, True, 0)
        if target.exists():
            raise ValueError("verified-cache target exists but is not a file")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.partial")
        with candidate.open("rb") as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if sha256_file(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise ValueError("local artifact changed while copying")
        os.replace(temporary, target)
        return AcquiredArtifact(descriptor, target.as_posix(), digest, size,
                                "verified", True, False, 0)
