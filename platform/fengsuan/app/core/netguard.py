"""Outbound URL checks: no file://, no cloud metadata, DOI path injection."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote, urlparse, urlsplit

from fengsuan.app.core.errors import ClassifiedHTTPException

_META_HOSTS = {"metadata.google.internal", "metadata.google.internal."}
_LOOPBACK_HOSTS = {"localhost", "ip6-localhost", "ip6-loopback"}
_META_IPS = {"169.254.169.254", "fd00:ec2::254"}


def _reject_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, what: str, allow_private: bool) -> None:
    if str(ip) in _META_IPS or ip.is_link_local or ip.is_unspecified:
        raise ClassifiedHTTPException(400, f"{what} \u4e0d\u5141\u8bb8\u94fe\u8def\u672c\u5730\u5730\u5740", "permission")
    if not allow_private and (ip.is_private or ip.is_loopback or ip.is_reserved):
        raise ClassifiedHTTPException(400, f"{what} \u4e91\u7aef\u4e0d\u80fd\u6307\u5411\u5185\u7f51\u6216\u56de\u73af", "permission")


def _decimal_ipv4(host: str) -> ipaddress.IPv4Address | None:
    if not host.isdigit():
        return None
    try:
        value = int(host)
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(value)
    except (ValueError, OverflowError):
        return None
    return None


def _resolve_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    decimal = _decimal_ipv4(host)
    if decimal is not None:
        found.append(decimal)
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return found
    for info in infos:
        addr = info[4][0]
        try:
            found.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    return found


def assert_http_url(url: str, *, what: str = "URL", allow_private: bool = True) -> str:
    raw = (url or "").strip()
    if not raw or any(c in raw for c in "\n\r\t"):
        raise ClassifiedHTTPException(400, f"{what} \u65e0\u6548", "permission")
    parts = urlparse(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ClassifiedHTTPException(400, f"{what} \u987b\u4e3a http(s)", "permission")
    host = parts.hostname.lower().rstrip(".")
    if host in _META_HOSTS:
        raise ClassifiedHTTPException(400, f"{what} \u4e0d\u5141\u8bb8\u8be5\u4e3b\u673a", "permission")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        _reject_ip(ip, what=what, allow_private=allow_private)
        return raw.rstrip("/")
    if not allow_private and host in _LOOPBACK_HOSTS:
        raise ClassifiedHTTPException(400, f"{what} \u4e91\u7aef\u4e0d\u80fd\u6307\u5411\u56de\u73af", "permission")
    decimal = _decimal_ipv4(host)
    if decimal is not None:
        _reject_ip(decimal, what=what, allow_private=allow_private)
    for resolved in _resolve_ips(host):
        _reject_ip(resolved, what=what, allow_private=allow_private)
    return raw.rstrip("/")


def assert_llm_base_url(url: str, *, kind: str = "cloud") -> str:
    """云端禁回环/私网；内网可用实验室网关。云元数据两种都不能用。"""
    return assert_http_url(url, what="base_url", allow_private=(kind or "cloud") == "onprem")


def assert_git_https(url: str) -> str:
    raw = (url or "").strip()
    if not raw or any(c in raw for c in " \n\r\t"):
        raise RuntimeError("bad_url")
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise RuntimeError("only_https")
    return assert_http_url(raw, what="git_url", allow_private=True)


def safe_doi(doi: str) -> str:
    d = (doi or "").strip()
    if not d or any(c in d for c in " \n\r\t") or ".." in d:
        raise ClassifiedHTTPException(400, "DOI \u65e0\u6548", "parse")
    lower = d.lower()
    if lower.startswith("http:") or lower.startswith("https:") or lower.startswith("file:"):
        raise ClassifiedHTTPException(400, "DOI \u65e0\u6548", "parse")
    return d[:256]


def crossref_works_url(doi: str) -> str:
    return "https://api.crossref.org/works/" + quote(safe_doi(doi), safe="/")
