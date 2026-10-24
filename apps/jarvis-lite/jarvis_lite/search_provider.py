"""Bounded search transport; search snippets are discovery hints, never evidence.

The selected Brave wire contract was checked against Brave's official public
SDK. Current prices/account terms are not bundled facts: the control layer must
require dated administrator-reviewed pricing, terms and spending authorization.
This module neither opens subscriptions nor invents a billed monetary receipt.
"""
from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import threading
import time
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx


ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
KEY_ENV = "BRAVE_SEARCH_API_KEY"
MAX_RESPONSE_BYTES = 262144
MAX_TIMEOUT_SECONDS = 10
MAX_RESULTS = 20
_SLOTS = threading.BoundedSemaphore(2)
_SENSITIVE_QUERY_KEYS = frozenset({"key", "api_key", "apikey", "token", "access_token",
    "authorization", "auth", "password", "secret", "signature", "x-amz-signature"})


class SearchRejected(Exception):
    """A local rejection or connection failure proved no HTTP search was sent."""
    definitive_no_charge = True

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SearchUnknown(Exception):
    """Dispatch may have happened; keep the reservation until reconciliation."""
    definitive_no_charge = False

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SearchProvider(Protocol):
    def search(self, query: str, *, limit: int, request_key: str) -> dict: ...


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _text(value, maximum, secret=""):
    if not isinstance(value, str):
        return ""
    # Provider strings may echo request headers. Never persist a server key,
    # even in an otherwise valid title, snippet or provider reference.
    value = value.replace(secret, "[redacted]") if secret else value
    parser = _PlainText()
    parser.feed(value[:12000])
    result = " ".join(" ".join(parser.parts).replace("\x00", "").split())
    return (result.replace(secret, "[redacted]") if secret else result)[:maximum]


def canonical_result_url(value, *, secret=""):
    """Syntax-only normalization: retrieval must freshly validate and pin DNS."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ord(char) < 33 for char in value)
            or "\\" in value or secret and secret in value):
        raise ValueError("search_result_url_invalid")
    if secret:
        from urllib.parse import unquote
        decoded = value
        for _ in range(4):
            decoded = unquote(decoded)
            if secret in decoded:
                raise ValueError("search_result_url_credentials")
    parsed = urlsplit(value)
    try:
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ValueError("search_result_url_invalid") from None
    if (parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password
            or port not in {None, 443} or "%" in host or "." not in host
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or ".." in host or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
        raise ValueError("search_result_url_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("search_result_url_invalid")
    if any(key.lower() in _SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
        raise ValueError("search_result_url_credentials")
    return urlunsplit(("https", host.lower(), parsed.path or "/", parsed.query, ""))


def _display_url(value, secret=""):
    """Rejected-source ledger reference without credentials, queries or fragments."""
    if not isinstance(value, str) or secret and secret in value:
        return None
    try:
        parsed = urlsplit(value[:2048])
        return _text(urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", "")), 2048, secret)
    except ValueError:
        return None


def _normalize(payload, *, limit, secret=""):
    if not isinstance(payload, dict) or payload.get("type") != "search":
        raise SearchUnknown("search_provider_response_invalid")
    web = payload.get("web")
    if web is not None and not isinstance(web, dict):
        raise SearchUnknown("search_provider_response_invalid")
    candidates = (web or {}).get("results", [])
    if not isinstance(candidates, list) or len(candidates) > 100:
        raise SearchUnknown("search_provider_response_invalid")
    results: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            rejected.append({"url": None, "code": "search_result_shape_invalid"})
            continue
        raw_url = candidate.get("url")
        try:
            url = canonical_result_url(raw_url, secret=secret)
        except ValueError as error:
            rejected.append({"url": _display_url(raw_url, secret), "code": str(error)})
            continue
        title = _text(candidate.get("title"), 500, secret)
        if not title:
            rejected.append({"url": url, "code": "search_result_title_missing"})
            continue
        if url in seen:
            continue
        seen.add(url)
        if len(results) >= limit:
            continue
        profile = candidate.get("profile")
        publisher = profile.get("name") if isinstance(profile, dict) else None
        results.append({"url": url, "canonical_url": url, "title": title,
            "description": _text(candidate.get("description"), 2000, secret),
            "publisher_claimed": _text(publisher, 200, secret) or urlsplit(url).hostname,
            "published_at_claimed": _text(candidate.get("page_age", candidate.get("age")), 100, secret) or None,
            "status": "DISCOVERED", "untrusted": True, "claims_verified": False})
    return results, rejected


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate provider key")
        value[key] = item
    return value


def _constant(_):
    raise ValueError("non-finite provider value")


class BraveSearchAdapter:
    """One fixed-origin request; durable idempotence belongs to the caller's ledger.

    Brave web search does not promise provider-side request idempotency. No retry
    is performed here, and request_key is local correlation, not such a promise.
    """
    name = "brave"

    def __init__(self, *, transport=None, timeout_seconds=MAX_TIMEOUT_SECONDS):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise SearchRejected("search_timeout_unsupported")
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def search(self, query, *, limit=5, request_key):
        key = os.getenv(KEY_ENV, "")
        if (not isinstance(key, str) or not 8 <= len(key) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in key)):
            raise SearchRejected("search_key_missing")
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 400
                or len(query.split()) > 50 or "\x00" in query or key in query
                or type(limit) is not int or not 1 <= limit <= MAX_RESULTS
                or not isinstance(request_key, str) or not 1 <= len(request_key) <= 100):
            raise SearchRejected("search_request_invalid")
        if not _SLOTS.acquire(blocking=False):
            raise SearchRejected("search_provider_busy")
        try:
            return self._request(query.strip(), limit, request_key, key)
        finally:
            _SLOTS.release()

    def _request(self, query, limit, request_key, key):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, connect=min(3, self.timeout_seconds)),
                              follow_redirects=False, trust_env=False, transport=self.transport,
                              limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                with client.stream("GET", ENDPOINT, params={"q": query, "count": limit,
                        "country": "SE", "search_lang": "sv", "ui_lang": "sv-SE", "offset": 0,
                        "safesearch": "strict", "result_filter": "web", "text_decorations": "false",
                        "spellcheck": "false"}, headers={"Accept": "application/json",
                        "Accept-Encoding": "identity", "X-Subscription-Token": key}) as response:
                    # HTTP error responses are not proof that Brave did not
                    # charge the request. Do not expose their bodies/headers.
                    if response.status_code != 200:
                        raise SearchUnknown("search_provider_http_" + str(response.status_code))
                    if (response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
                            or response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}):
                        raise SearchUnknown("search_provider_response_invalid")
                    declared = response.headers.get("Content-Length")
                    if declared is not None and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
                        raise SearchUnknown("search_provider_response_too_large")
                    raw = bytearray()
                    for part in response.iter_bytes():
                        raw.extend(part)
                        if len(raw) > MAX_RESPONSE_BYTES or time.monotonic() - started > self.timeout_seconds:
                            raise SearchUnknown("search_provider_response_limit")
                    payload: Any = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
                    results, rejected = _normalize(payload, limit=limit, secret=key)
                    reference = response.headers.get("X-Request-ID") or response.headers.get("Request-ID")
                    reference = _text(reference, 200, key) or None
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise SearchRejected("search_connection_not_established") from None
        except (httpx.HTTPError, UnicodeError, ValueError, TypeError, RecursionError):
            raise SearchUnknown("search_provider_outcome_unknown") from None
        return {"provider": self.name, "results": results, "rejected_results": rejected,
            "usage": {"requests": 1, "billing_basis": "admin_verified_tariff", "reported_cost": None},
            "provider_request_id": reference, "request_key": request_key, "simulated": False}


class MockSearchAdapter:
    """Explicitly injected offline fixtures; never selected from production config."""
    name = "mock"

    def __init__(self, responses):
        self.responses = deepcopy(responses)
        self.requests: list[dict] = []

    def search(self, query, *, limit=5, request_key):
        self.requests.append({"query": query, "limit": limit, "request_key": request_key})
        results, rejected = _normalize(self.responses.get(query, {"type": "search", "web": {"results": []}}), limit=limit)
        return {"provider": self.name, "results": results, "rejected_results": rejected,
            "usage": {"requests": 0, "billing_basis": "synthetic_fixture", "reported_cost": None},
            "provider_request_id": None, "request_key": request_key, "simulated": True}


def provider(name="brave", *, transport=None):
    if name != "brave":
        raise SearchRejected("search_provider_unsupported")
    return BraveSearchAdapter(transport=transport)
