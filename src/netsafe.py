"""Outbound-request safety for user-supplied URLs (custom news feeds)."""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

import requests
from urllib3 import HTTPSConnectionPool, Timeout
from urllib3.exceptions import HTTPError

from .guardrails import safe_http_url
from .util import connection_slot


def fetch_feed(url: str, *, limit: int = 2_500_000) -> bytes:
    """Fetch a feed by its vetted IP, verifying TLS against the supplied hostname."""
    safe = safe_http_url(url)
    if not safe or not safe.startswith("https://"):
        raise ValueError("feed URL must be a valid https:// address")
    parsed = urlsplit(safe)
    host = (parsed.hostname or "").encode("idna").decode("ascii")
    port = parsed.port or 443
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        infos = None
        for attempt in range(2):  # resolvers drop lookups under parallel load; one retry fixes it
            try:
                infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                break
            except OSError:
                time.sleep(0.4)
        if not infos:
            raise ValueError("feed host could not be resolved")
        addresses = list(dict.fromkeys(ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos))
    if not addresses or any(not address.is_global or address.is_multicast or address.is_reserved for address in addresses):
        raise ValueError("feed host is not a public internet address")
    authority = f"[{host}]" if ":" in host else host
    if port != 443:
        authority += f":{port}"
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    for address in addresses[:3]:
        with HTTPSConnectionPool(str(address), port=port, server_hostname=host, assert_hostname=host,
                                 cert_reqs=ssl.CERT_REQUIRED, ca_certs=requests.certs.where(),
                                 timeout=Timeout(connect=3, read=6)) as pool:
            try:
                with connection_slot(safe):
                    response = pool.urlopen("GET", path, headers={"Host": authority, "User-Agent": "MorningBrief/3.0"},
                                            preload_content=False, retries=False, redirect=False)
                    try:
                        if response.status != 200:
                            raise ValueError(f"feed rejected request (HTTP {response.status})")
                        deadline, body = time.monotonic() + 10, bytearray()
                        for chunk in response.stream(65536, decode_content=True):
                            body.extend(chunk)
                            if len(body) > limit or time.monotonic() > deadline:
                                raise ValueError("feed exceeded download limits")
                        return bytes(body)
                    finally:
                        response.close()
            except HTTPError:
                continue
    raise OSError("feed endpoint unavailable")
