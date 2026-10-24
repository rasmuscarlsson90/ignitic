"""Bounded, robots-aware public web reads; page content never grants authority.

The caller owns durable policy approval, budgets, leases and rate accounting.
This adapter additionally enforces the reviewed destination policy at each hop,
pins checked public DNS addresses to TCP, and extracts text without executing
page scripts, PDF actions, linked resources or a browser.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import math
import re
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .policy import PolicyError
from .research import _Deadline, _PinnedHTTPSConnection, _resolve_addresses


USER_AGENT = "JarvisResearch/1.5"
CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "application/pdf"})
MAX_BYTES = 1024 * 1024
MAX_TEXT = 32000
MAX_ROBOTS_BYTES = 65536
_ACTIVE = 0
_NETWORK_LOCK = threading.Lock()
_PAGE_TIMES: dict[str, float] = {}
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_IGNORED = frozenset({"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed",
                      "form", "input", "select", "textarea", "button", "nav", "aside"})


def _error(code, message, status=403):
    return PolicyError(code, message, status)


def canonical_url(value):
    """Canonical public HTTPS identity; no userinfo, internal host or secret URL."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or re.search(r"[\x00-\x20\x7f\\]", value)):
        raise _error("web_url_invalid", "Källan måste ha en avgränsad offentlig HTTPS-adress.", 422)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
                or port not in {None, 443} or host.endswith(".") or not host.isascii()
                or len(host) > 253 or "." not in host
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                       for part in host.split("."))):
            raise ValueError
        # Numeric literals are not reviewed publisher identities. Blocking them
        # also rejects octal/alternate IPv4 forms before resolver interpretation.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if all(part.isdecimal() for part in host.split(".")):
                raise ValueError
        else:
            raise ValueError
        if host.endswith((".localhost", ".local", ".internal", ".home", ".lan", ".test", ".invalid", ".onion", ".arpa", ".example")):
            raise ValueError
        if re.search(r"%(?:00|0a|0d|5c)", value, re.IGNORECASE):
            raise ValueError
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=20)
        if len(parsed.query) > 700 or any(key.lower() in {
                "token", "access_token", "api_key", "apikey", "password", "authorization", "auth", "secret", "signature"}
                for key, _ in pairs):
            raise ValueError
        query = urlencode([(key, item) for key, item in pairs
                           if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}])
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        return urlunsplit(("https", host.lower(), path, query, ""))
    except (ValueError, TypeError, UnicodeError):
        raise _error("web_url_invalid", "Endast offentliga HTTPS-domäner på port 443 utan inloggning eller hemligheter i URL tillåts.", 422) from None


def _domains(value):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, str) for item in value):
        raise _error("web_policy_invalid", "Domänpolicyn måste granskas innan källor hämtas.")
    result = []
    for item in value:
        normalized = item.lower()
        url = canonical_url("https://" + normalized + "/")
        if urlsplit(url).hostname != normalized or url != "https://" + normalized + "/":
            raise _error("web_policy_invalid", "Policyn måste ange exakta domännamn utan wildcard eller sökväg.")
        result.append(normalized)
    return result


def _matches(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _destination(value, rules):
    url = canonical_url(value)
    host = urlsplit(url).hostname
    if _matches(host, rules["prohibited_domains"]) or not _matches(host, rules["allowed_domains"]):
        raise _error("web_domain_blocked", "Källans domän ligger utanför den förhandsgodkända policyn.")
    return url


def _rules(policy):
    if not isinstance(policy, dict) or policy.get("terms_reviewed") is not True:
        raise _error("web_terms_required", "Tillåtna domäners åtkomstvillkor måste granskas i policyn före hämtning.")
    result = {"allowed_domains": _domains(policy.get("allowed_domains", [])),
              "prohibited_domains": _domains(policy.get("prohibited_domains", []))}
    if not result["allowed_domains"]:
        raise _error("web_domain_blocked", "Inga offentliga källdomäner har förhandsgodkänts.")
    types = policy.get("allowed_content_types", ["text/html", "application/pdf"])
    if (not isinstance(types, list) or not types or any(item not in CONTENT_TYPES for item in types)
            or len(set(types)) != len(types)):
        raise _error("web_policy_invalid", "Välj endast stödda HTML- och PDF-innehållstyper.")
    result["allowed_content_types"] = types
    for name, default, low, high in (("timeout_seconds", 8, 1, 10), ("max_bytes", 524288, 1024, MAX_BYTES),
                                   ("max_redirects", 2, 0, 2), ("max_concurrent_requests", 1, 1, 2),
                                   ("minimum_interval_seconds", 1, 0, 3600)):
        value = policy.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise _error("web_policy_invalid", "Webbhämtningens tids-, storleks- eller samtidighetsgräns är ogiltig.")
        result[name] = value
    return result


def _public_address(value):
    try:
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and
                not (isinstance(address, ipaddress.IPv6Address) and (address.sixtofour or address.teredo)))
    except ValueError:
        return False


def _published(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.isoformat()
    except ValueError:
        return None


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []
        self.titles: list[str] = []
        self.publisher = None
        self.published_at = None
        self.login = False
        self.challenge = False
        self.characters = 0

    def handle_starttag(self, tag, attrs):
        values = {key.lower(): value for key, value in attrs}
        if tag == "input" and str(values.get("type", "")).lower() == "password":
            self.login = True
        markers = " ".join(str(values.get(key, "")) for key in ("class", "id", "src")).lower()
        if any(marker in markers for marker in ("g-recaptcha", "h-captcha", "cf-chl", "/recaptcha/")):
            self.challenge = True
        if tag == "meta":
            key = str(values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "")
            if key in {"article:published_time", "date", "datepublished", "pubdate", "dc.date.issued"}:
                self.published_at = self.published_at or _published(content)
            if key in {"og:site_name", "publisher", "dc.publisher"} and isinstance(content, str):
                self.publisher = content.strip()[:200] or None
        hidden = ("hidden" in values or str(values.get("aria-hidden", "")).lower() == "true"
                  or re.search(r"display\s*:\s*none", str(values.get("style", "")), re.IGNORECASE))
        if tag not in _VOID:
            self.stack.append((tag, bool(tag in _IGNORED or hidden)))
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(ignored for _, ignored in self.stack):
            return
        if any(tag == "title" for tag, _ in self.stack):
            self.titles.append(data[:300])
            return
        if any(tag == "head" for tag, _ in self.stack):
            return
        if data.strip():
            self.characters += len(data)
            if self.characters > MAX_TEXT:
                raise _error("web_text_limit", "Sidans läsbara innehåll överskrider den stödda textgränsen.", 422)
            self.parts.append(data)

    def result(self):
        text = re.sub(r"\s+", " ", " ".join(self.parts)).strip()
        title = re.sub(r"\s+", " ", " ".join(self.titles)).strip()[:300]
        challenge_title = title.casefold()
        if (self.login or self.challenge or any(word in challenge_title for word in (
                "access denied", "just a moment", "attention required", "verify you are human", "logga in"))):
            raise _error("web_access_restricted", "Inloggning, CAPTCHA eller annan åtkomstspärr får inte kringgås.")
        if len(text) < 40:
            raise _error("web_text_missing", "Källan saknar tillräcklig offentlig läsbar text för belägg.", 422)
        return {"text": text, "title": title, "publisher": self.publisher, "published_at": self.published_at}


# Run the hostile document parser outside the application worker with no server
# secrets, network capability or writable output. The library never executes
# PDF JavaScript; active/embedded/encrypted formats are rejected as well.
_PDF_SCRIPT = r'''
import builtins, io, json, os, resource, socket, sys
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
def deny(*args, **kwargs): raise RuntimeError("PDF networking is forbidden")
socket.socket = deny
socket.create_connection = deny
socket.getaddrinfo = deny
from pypdf import PdfReader
raw = sys.stdin.buffer.read(1048577)
if len(raw) > 1048576 or not raw.startswith(b"%PDF-"): raise ValueError("size/format")
reader = PdfReader(io.BytesIO(raw), strict=True)
if reader.is_encrypted or not 1 <= len(reader.pages) <= 12: raise ValueError("encrypted/page limit")
root = reader.trailer.get("/Root", {})
if hasattr(root, "get_object"): root = root.get_object()
if any(key in root for key in ("/OpenAction", "/AA", "/AcroForm")): raise ValueError("active document")
names = root.get("/Names", {})
if hasattr(names, "get_object"): names = names.get_object()
if any(key in names for key in ("/JavaScript", "/EmbeddedFiles")): raise ValueError("embedded content")
parts=[]
for page in reader.pages:
 if any(key in page for key in ("/AA", "/OpenAction", "/RichMedia")): raise ValueError("active page")
 value = page.extract_text() or ""
 parts.append(value)
 if sum(map(len, parts)) > 32000: raise ValueError("text limit")
text=" ".join(" ".join(parts).split())
if len(text) < 40: raise ValueError("no readable text")
meta=reader.metadata
result={"text":text,"title":str(meta.title or "")[:300] if meta else "", "publisher":None,"published_at":None}
sys.stdout.write(json.dumps(result, ensure_ascii=False))
'''


def _pdf(raw, deadline):
    if not raw.startswith(b"%PDF-"):
        raise _error("web_pdf_invalid", "PDF-källan har ett ogiltigt filformat.", 422)
    try:
        result = subprocess.run([sys.executable, "-I", "-c", _PDF_SCRIPT], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            timeout=min(3, deadline.remaining()), env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, close_fds=True)
        deadline.remaining()
        if result.returncode != 0 or len(result.stdout) > 200000:
            raise ValueError
        extracted = json.loads(result.stdout)
        if not isinstance(extracted, dict) or not isinstance(extracted.get("text"), str) or len(extracted["text"]) > MAX_TEXT:
            raise ValueError
        return extracted
    except (subprocess.SubprocessError, OSError, ValueError, UnicodeError):
        raise _error("web_pdf_unsupported", "PDF-filen är krypterad, aktiv, för stor eller kan inte läsas inom säkerhetsgränserna.", 422) from None


class PublicWebAdapter:
    """One robots-aware page bundle under a single absolute deadline."""
    def __init__(self, *, resolver=None, connection_factory=None, clock=None, sleeper=None):
        self.resolver = resolver or _resolve_addresses
        self.connection_factory = connection_factory or _PinnedHTTPSConnection
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    def _request(self, url, rules, deadline, kind, limit, before_request, counter):
        url = _destination(url, rules)
        counter[0] += 1
        if counter[0] > 8:
            raise _error("web_request_limit", "Sidans robots- och omdirigeringskedja överskrider begäransgränsen.")
        parsed = urlsplit(url)
        addresses = self.resolver(parsed.hostname, deadline)
        if not addresses or any(not _public_address(address) for address in addresses):
            raise _error("web_ssrf", "Alla DNS-adresser måste vara offentliga; privata, interna och blandade adresser blockeras.")
        if before_request:
            before_request(url, kind=kind)
        deadline.remaining()
        connection = self.connection_factory(parsed.hostname, sorted(addresses)[0], deadline.remaining(), deadline=deadline)
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request("GET", path, headers={"User-Agent": USER_AGENT,
                "Accept": "text/plain" if kind == "robots" else ", ".join(rules["allowed_content_types"]),
                "Accept-Encoding": "identity"})
            deadline.remaining()
            response = connection.getresponse()
            deadline.remaining()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if sum(len(str(key)) + len(str(value)) for key, value in headers.items()) > 16384:
                raise _error("web_headers_limit", "Källans HTTP-huvuden är för stora.", 422)
            if str(headers.get("content-encoding", "identity")).strip().lower() not in {"", "identity"}:
                raise _error("web_encoding", "Komprimerade svar som kan överskrida gränserna tillåts inte.", 422)
            length = headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise _error("web_size_limit", "Källan överskrider den granskade storleksgränsen.", 422)
            raw = response.read(limit + 1)
            deadline.remaining()
            if len(raw) > limit:
                raise _error("web_size_limit", "Källan överskrider den granskade storleksgränsen.", 422)
            return response.status, headers, raw
        finally:
            connection.close()

    def _robots(self, url, rules, deadline, before_request, counter):
        parsed = urlsplit(url)
        robots = urlunsplit(("https", parsed.netloc, "/robots.txt", "", ""))
        for hop in range(rules["max_redirects"] + 1):
            status, headers, raw = self._request(robots, rules, deadline, "robots", MAX_ROBOTS_BYTES, before_request, counter)
            if status in {301, 302, 303, 307, 308}:
                if hop == rules["max_redirects"] or not headers.get("location"):
                    raise _error("web_robots_unavailable", "Robots-reglerna kan inte verifieras inom tillåten omdirigeringsgräns.")
                robots = _destination(urljoin(robots, headers["location"]), rules)
                continue
            if status in {404, 410}:
                return rules["minimum_interval_seconds"]
            if status != 200 or headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/plain":
                raise _error("web_robots_unavailable", "Källans robots- eller åtkomstregler kunde inte verifieras; sidan hämtas inte.")
            try:
                content = raw.decode("utf-8-sig", errors="strict")
            except UnicodeError:
                raise _error("web_robots_unavailable", "Källans robots-regler kan inte läsas säkert.") from None
            if "\x00" in content or len(content.splitlines()) > 2048 or re.search(r"<\s*(?:html|script|form)\b", content, re.IGNORECASE):
                raise _error("web_robots_unavailable", "Källans robots-regler har ett ogiltigt format.")
            lines = [line.split("#", 1)[0].strip() for line in content.splitlines()]
            if any(line and not re.fullmatch(r"[A-Za-z][A-Za-z-]*\s*:.*", line) for line in lines):
                raise _error("web_robots_unavailable", "Källans robots-regler har ett ogiltigt format.")
            parser = RobotFileParser()
            parser.parse(content.splitlines())
            if not parser.can_fetch(USER_AGENT, url):
                raise _error("web_robots_disallowed", "Webbplatsens robots-regler förbjuder läsning av denna sida.")
            interval = max(rules["minimum_interval_seconds"], parser.crawl_delay(USER_AGENT) or 0)
            rate = parser.request_rate(USER_AGENT)
            if rate and rate.requests > 0:
                interval = max(interval, math.ceil(rate.seconds / rate.requests))
            return interval
        raise AssertionError("bounded robots loop")

    def _page_rate(self, url, interval, deadline):
        host = urlsplit(url).hostname
        with _NETWORK_LOCK:
            last = _PAGE_TIMES.get(host)
            remaining = max(0, interval - (self.clock() - last)) if last is not None else 0
            if remaining >= deadline.remaining():
                raise _error("web_rate_limited", "Domänens robots- eller hastighetsgräns kräver en senare läsning.")
            # Reserve the next start under the lock, so concurrent page bundles
            # do not both pass the same domain interval check.
            _PAGE_TIMES[host] = self.clock() + remaining
            if len(_PAGE_TIMES) > 1024:
                oldest = min(_PAGE_TIMES, key=lambda key: _PAGE_TIMES[key])
                _PAGE_TIMES.pop(oldest, None)
        if remaining:
            self.sleeper(remaining)
            deadline.remaining()

    def fetch(self, url, policy, before_request=None):
        global _ACTIVE
        rules = _rules(policy)
        requested = _destination(url, rules)
        with _NETWORK_LOCK:
            if _ACTIVE >= rules["max_concurrent_requests"]:
                raise _error("web_concurrency_limit", "Den begränsade webbhämtningskapaciteten är upptagen.")
            _ACTIVE += 1
        deadline = _Deadline(rules["timeout_seconds"], self.clock)
        counter = [0]
        current, redirects = requested, []
        try:
            for hop in range(rules["max_redirects"] + 1):
                current = _destination(current, rules)
                # Rules apply to the full final path, not only the first origin.
                # Re-read each hop even on the same host so path-specific denial
                # cannot be bypassed by a public redirect.
                interval = self._robots(current, rules, deadline, before_request, counter)
                self._page_rate(current, interval, deadline)
                status, headers, raw = self._request(current, rules, deadline, "page", rules["max_bytes"], before_request, counter)
                if status in {301, 302, 303, 307, 308}:
                    if hop == rules["max_redirects"] or not headers.get("location"):
                        raise _error("web_redirect_limit", "Sidan överskrider tillåten omdirigeringsgräns.")
                    target = _destination(urljoin(current, headers["location"]), rules)
                    redirects.append({"from": current, "to": target, "status": status})
                    current = target
                    continue
                if status in {401, 403, 429}:
                    raise _error("web_access_restricted", "Webbplatsen kräver inloggning, nekar åtkomst eller begränsar läsningen.")
                if status != 200:
                    raise _error("web_http_error", "Källan gav inte ett tillåtet offentligt HTTP-svar.", 502)
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in rules["allowed_content_types"]:
                    raise _error("web_content_type", "Källans innehållstyp ingår inte i den godkända policyn.", 422)
                if content_type == "application/pdf":
                    extracted = _pdf(raw, deadline)
                else:
                    parser = _HTMLText()
                    # Honor only bounded explicit common encodings; no plugins,
                    # sniffing or replacement of corrupted source evidence.
                    charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", headers.get("content-type", ""), re.IGNORECASE)
                    encoding = charset.group(1).lower() if charset else "utf-8"
                    if encoding not in {"utf-8", "utf8", "iso-8859-1", "latin-1", "windows-1252"}:
                        raise _error("web_encoding", "Källans teckenkodning stöds inte för tillförlitliga citat.", 422)
                    parser.feed(raw.decode(encoding, errors="strict"))
                    parser.close()
                    extracted = parser.result()
                deadline.remaining()
                text = extracted["text"]
                return {"requested_url": requested, "canonical_url": current, "title": extracted["title"] or urlsplit(current).hostname,
                    "text": text, "content_type": content_type, "retrieved_at": time.time(),
                    "published_at": extracted.get("published_at"), "publisher": extracted.get("publisher") or urlsplit(current).hostname,
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(), "retrieval_status": "SUCCESS",
                    "robots_allowed": True, "terms_allowed": True, "untrusted_input": True,
                    "redirects": redirects, "byte_count": len(raw)}
            raise AssertionError("bounded page loop")
        except PolicyError:
            raise
        except (OSError, TimeoutError, UnicodeError, ValueError, http.client.HTTPException) as error:
            # Raw exception text can contain source data or proxy details.
            code = "web_timeout" if isinstance(error, TimeoutError) else "web_fetch_failed"
            raise _error(code, "Källan kunde inte hämtas och läsas inom säkerhets- och tidsgränserna.", 502) from None
        finally:
            with _NETWORK_LOCK:
                _ACTIVE -= 1
