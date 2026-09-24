"""Download a host-provided file reference (openai/fileParams) with SSRF defenses.

Only HTTPS to an allowlisted host; every resolved address must be public; the TCP connection is pinned
to the validated IP while TLS still verifies the hostname; redirects are re-validated per hop; the body
is streamed under a hard byte cap. The signed URL is never logged or persisted.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

from .store import StoreError

Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    return sorted({a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


FAKE_IP = ipaddress.ip_network('198.18.0.0/15')
# Prefixes whose addresses embed an IPv4 target (NAT64 64:ff9b::/96 and /48 local-use, 6to4, Teredo).
EMBEDS_IPV4 = [ipaddress.ip_network(n) for n in ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32')]


def public_ip(ip: str, fake_ip_ok: bool = False) -> bool:
    """Global unicast only. `fake_ip_ok` additionally accepts 198.18.0.0/15, the range local fake-IP DNS
    proxies (TUN mode) hand out for every public name; the proxy connects to the real host and TLS still
    verifies the allowlisted hostname."""
    addr = ipaddress.ip_address(ip.split('%')[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv6Address) and any(addr in n for n in EMBEDS_IPV4):
        return False
    if fake_ip_ok and addr in FAKE_IP:
        return True
    return addr.is_global and not addr.is_multicast


def host_allowed(host: str, allowlist: list[str]) -> bool:
    """Exact host or a listed parent domain written as '.example.com'."""
    host = host.lower().rstrip('.')
    for entry in allowlist:
        entry = entry.lower().rstrip('.')
        if host == entry or (entry.startswith('.') and host.endswith(entry)):
            return True
    return False


@dataclass
class Downloaded:
    data: bytes
    host: str
    content_type: str | None
    hops: int


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch(url: str, *, allowlist: list[str], max_bytes: int = 25 * 1024 * 1024, timeout: float = 20.0,
          max_hops: int = 3, resolver: Resolver = system_resolver, context: ssl.SSLContext | None = None,
          allow_nonpublic: bool = False, fake_ip_ok: bool = False) -> Downloaded:
    """allow_nonpublic/context exist for the local test server only; production callers never set them."""
    context = context or ssl.create_default_context()
    deadline = time.monotonic() + timeout * 2
    for hop in range(max_hops + 1):
        if not isinstance(url, str) or len(url) > 8192:
            raise StoreError('file_url_invalid', 'The file reference is not a usable URL.')
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme != 'https' or not host or parts.username or parts.password:
            raise StoreError('file_url_invalid', 'Only HTTPS file references without credentials are accepted.')
        port = parts.port or 443
        if port != 443 and not allow_nonpublic:
            raise StoreError('file_url_invalid', 'Nonstandard ports are not accepted for file downloads.')
        if not host_allowed(host, allowlist):
            raise StoreError('file_host_not_allowlisted',
                             f'File host {host} is not on the verified allowlist; nothing was saved.')
        try:
            ips = resolver(host, port)
        except OSError as e:
            raise StoreError('file_download_failed', 'Could not resolve the file host; nothing was saved.') from e
        if not ips or (not allow_nonpublic and not all(public_ip(ip, fake_ip_ok) for ip in ips)):
            raise StoreError('file_url_blocked', 'The file host resolves to a non-public address; refused.')
        path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
        conn = _PinnedHTTPS(host, ips[0], port, timeout, context)
        try:
            conn.request('GET', path, headers={'User-Agent': 'phctx-file-fetch/1', 'Accept': '*/*',
                                               'Accept-Encoding': 'identity'})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader('Location')
                if not loc:
                    raise StoreError('file_download_failed', 'Redirect without location; nothing was saved.')
                url = urljoin(url, loc)
                continue
            if resp.status in (401, 403, 404, 410):
                raise StoreError('file_url_expired', 'The file link is expired or not accessible; '
                                 'ask the host to provide the attachment again. Nothing was saved.')
            if resp.status != 200:
                raise StoreError('file_download_failed', f'File host returned HTTP {resp.status}; nothing was saved.')
            declared = resp.getheader('Content-Length')
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise StoreError('file_size', 'File exceeds the interactive size cap; nothing was saved.')
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise StoreError('file_download_failed', 'Download timed out; nothing was saved.')
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise StoreError('file_size', 'File exceeds the interactive size cap; nothing was saved.')
                chunks.append(chunk)
            if declared and declared.isdigit() and int(declared) != total:
                raise StoreError('file_download_failed', 'Truncated download; nothing was saved.')
            if not total:
                raise StoreError('file_size', 'Empty file; nothing was saved.')
            return Downloaded(b''.join(chunks), host, resp.getheader('Content-Type'), hop)
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            raise StoreError('file_download_failed', 'File transfer failed; nothing was saved.') from e
        finally:
            conn.close()
    raise StoreError('file_download_failed', 'Too many redirects; nothing was saved.')
