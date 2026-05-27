# -*- coding: utf-8 -*-
"""HTTPS-only downloads with fixed host allowlists (QGIS plugin security audit)."""

from __future__ import annotations

import http.client
import ssl
from typing import Mapping
from urllib.parse import urlparse

# Hosts permitted for model weight downloads (FieldWatch GCS).
CHECKPOINT_DOWNLOAD_HOSTS = frozenset({"storage.googleapis.com"})

# Host permitted for pip bootstrap script.
GET_PIP_DOWNLOAD_HOSTS = frozenset({"bootstrap.pypa.io"})


class UnsafeDownloadURLError(ValueError):
    """Raised when a download URL is not HTTPS or not on the allowlist."""


def require_https_url(url: str, allowed_hosts: frozenset[str]) -> tuple[str, str]:
    """Validate URL scheme and host; return (hostname, path_with_optional_query)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeDownloadURLError(
            f"Only HTTPS downloads are allowed, not {parsed.scheme!r}."
        )
    if parsed.username or parsed.password:
        raise UnsafeDownloadURLError("Download URLs must not contain credentials.")
    host = (parsed.hostname or "").lower()
    if not host or host not in allowed_hosts:
        raise UnsafeDownloadURLError(f"Download host is not permitted: {host!r}")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host, path


class HttpsDownloadResponse:
    """Streaming GET over HTTPS (stdlib http.client; no urllib.request.urlopen)."""

    def __init__(
        self,
        url: str,
        allowed_hosts: frozenset[str],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120,
    ) -> None:
        host, path = require_https_url(url, allowed_hosts)
        self._conn = http.client.HTTPSConnection(
            host,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._conn.request("GET", path, headers=dict(headers or {}))
        self._response = self._conn.getresponse()
        self.status = self._response.status
        self.headers = self._response.headers

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        try:
            self._conn.close()
        except OSError:
            pass

    def __enter__(self) -> HttpsDownloadResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def download_https_to_file(
    url: str,
    dest_path: str,
    allowed_hosts: frozenset[str],
    *,
    timeout: float = 120,
    chunk_bytes: int = 1024 * 1024,
) -> None:
    """Download an HTTPS URL to a local file (host must be on allowlist)."""
    with HttpsDownloadResponse(url, allowed_hosts, timeout=timeout) as response:
        if response.status >= 400:
            raise http.client.HTTPException(
                f"HTTP {response.status} while downloading."
            )
        with open(dest_path, "wb") as handle:
            while True:
                chunk = response.read(chunk_bytes)
                if not chunk:
                    break
                handle.write(chunk)
