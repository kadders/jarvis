from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from utils.request_context import outbound_trace_headers


class RelayError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_relay_target(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RelayError("invalid_scheme", "Only http/https URLs are allowed")
    if parsed.scheme == "http" and not settings.jarvis_ocr_relay_allow_insecure:
        raise RelayError("http_not_allowed", "http relay disabled; set JARVIS_OCR_RELAY_ALLOW_INSECURE=1 for dev")
    host = parsed.hostname
    if not host:
        raise RelayError("invalid_host", "Missing host")

    allow = settings.jarvis_ocr_relay_host_suffix_allowlist
    if allow:
        suffixes = [s.strip().lower() for s in allow.split(",") if s.strip()]
        hl = host.lower()
        if not any(hl.endswith(s) or hl == s.lstrip(".") for s in suffixes):
            raise RelayError("host_not_allowlisted", "Host does not match JARVIS_OCR_RELAY_HOST_SUFFIX_ALLOWLIST")

    try:
        ip = ipaddress.ip_address(host)
        if not settings.jarvis_ocr_relay_allow_insecure:
            _check_blocked_ip(ip)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise RelayError("dns_failed", str(e)) from e

    if not settings.jarvis_ocr_relay_allow_insecure:
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            _check_blocked_ip(ip)


def _check_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        raise RelayError("blocked_ip", "Target resolves to a non-public address")
    reserved = getattr(ip, "is_reserved", False)
    if reserved:
        raise RelayError("blocked_ip", "Reserved address blocked")
    if ip.version == 4:
        s = str(ip)
        if s.startswith("169.254."):
            raise RelayError("blocked_ip", "Link-local / metadata range blocked")
        if s == "127.0.0.1" or s.startswith("127."):
            raise RelayError("blocked_ip", "Loopback blocked")


async def deliver_webhook(
    url: str,
    method: str,
    headers: Optional[Dict[str, str]],
    payload: Dict[str, Any],
    timeout_s: float,
) -> Dict[str, Any]:
    method_u = (method or "POST").upper()
    if method_u not in ("POST", "PUT"):
        raise RelayError("invalid_method", "Only POST or PUT supported")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    hdrs.update(outbound_trace_headers())
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            r = await client.request(method_u, url, content=json.dumps(payload), headers=hdrs)
        return {
            "attempted": True,
            "status_code": r.status_code,
            "error": None if r.is_success else r.text[:500],
        }
    except httpx.RequestError as e:
        logger.warning(f"relay delivery failed: {e}")
        return {"attempted": True, "status_code": None, "error": str(e)}
