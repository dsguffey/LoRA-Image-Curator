"""Bounded HTTPS reads from the publisher registry and its designated file host."""
from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_HOSTS = {"pypi.org", "files.pythonhosted.org"}
_last_request: dict[str, float] = {}


def check_url(url: str) -> str:
    """Restrict every request, including redirects, to approved HTTPS hosts."""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError(f"Unapproved upstream URL: {url}")
    return str(parsed.hostname)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Validate a redirect before urllib sends another request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url: str, *, etag: str = "", limit: int = 16_000_000) -> tuple[bytes | None, str]:
    """Make one sequential, size-limited request with bounded retry/backoff."""
    host = check_url(url)
    headers = {"User-Agent": "LoRA-Image-Curator-dependency-maintenance/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(4):
        time.sleep(max(0, 1 - (time.monotonic() - _last_request.get(host, 0))))
        _last_request[host] = time.monotonic()
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                check_url(response.url)
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Upstream artifact exceeds the size limit")
                return data, response.headers.get("ETag", "")
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return None, etag
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
            retry = error.headers.get("Retry-After", "")
            try:
                delay = float(retry)
            except ValueError:
                try:
                    delay = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
                except (ValueError, TypeError):
                    delay = 2 ** attempt
            if delay > 60:
                raise RuntimeError(f"Upstream requested a {delay}s pause; retry maintenance later") from error
            time.sleep(max(delay, 2 ** attempt) + random.uniform(0, 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt + random.uniform(0, 1))
    raise RuntimeError("Upstream request exhausted retries")


def release_metadata(version: str | None, cache: Path) -> dict:
    """Read PyPI JSON conditionally; cache contains metadata, never credentials."""
    from packaging.version import Version
    suffix = f"/{Version(version)}" if version else ""
    url = f"https://pypi.org/pypi/insightface{suffix}/json"
    key = hashlib.sha256(url.encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{key}.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    data, etag = read_url(url, etag=old.get("etag", ""))
    if data is None:
        return old["body"]
    body = json.loads(data)
    path.write_text(json.dumps({"url": url, "etag": etag, "body": body}), encoding="utf-8")
    return body


def acquire(url: str, digest: str, destination: Path) -> Path:
    """Reuse only hash-verified artifacts; fail on mismatches without executing code."""
    check_url(url)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("An exact SHA-256 is required")
    if destination.exists():
        data = destination.read_bytes()
    else:
        data, _ = read_url(url)
    if data is None or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Upstream artifact SHA-256 mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(data)
    return destination
