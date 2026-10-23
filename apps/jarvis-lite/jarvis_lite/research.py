"""Approved, attributed company directories; never an arbitrary web crawler.

Sources are administrator-reviewed uploads or one exact HTTPS JSON feed. Network
access is a separate, disabled-by-default runtime capability. Every result is
untrusted evidence, not permission to contact a business or execute instructions.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import threading
import time
from typing import Any, cast
from urllib.parse import urlsplit

from . import budget, policy, workflow
from .policy import PolicyError
from .store import fingerprint


MAX_RECORDS = 50
MAX_RESPONSE_BYTES = 262144
TIMEOUT_SECONDS = 10
# getaddrinfo has no portable cancellation API. Timed-out resolver calls retain
# their slots until the OS returns: at most two daemon DNS calls can exist, and
# no TCP/TLS/provider I/O is ever detached from the request's deadline.
_DNS_SLOTS = threading.BoundedSemaphore(2)
SOURCE_KEYS = {"name", "kind", "url", "approved", "license_reviewed", "legal_review_reference",
               "fetch_enabled", "minimum_quality", "minimum_interval_seconds", "cost_minor",
               "cost_evidence", "data_mode"}
RECORD_KEYS = {"company", "email", "domain", "source_url", "evidence", "entity_type", "address_type"}


def _text(value, name, maximum=2000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or "\x00" in value:
        raise PolicyError("research_input", f"{name} must contain {minimum}–{maximum} characters", 422)
    return value.strip()


def _url(value):
    value = _text(value, "Source URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise PolicyError("research_url", "Use an exact public HTTPS URL on port 443", 422) from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or port not in {None, 443} or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)
            or "." not in parsed.hostname):
        raise PolicyError("research_url", "Use an exact public HTTPS URL without credentials, query or fragment", 422)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise PolicyError("research_url", "Private, local and reserved source addresses are forbidden", 422)
    if parsed.hostname.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise PolicyError("research_url", "A public source hostname is required", 422)
    return value


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError("research_input", f"{name} must be an integer between {minimum} and {maximum}", 422)
    return value


def configure_source(tx, source_id, config, actor):
    """Register reviewed source authority without fetching or changing lead data."""
    policy.require_admin(actor)
    source_id = _text(source_id, "Source ID", 80)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", source_id):
        raise PolicyError("research_source_id", "Use a lowercase source identifier", 422)
    if not isinstance(config, dict) or set(config) - SOURCE_KEYS:
        raise PolicyError("research_input", "Unknown research source configuration", 422)
    kind = config.get("kind", "upload")
    if kind not in {"upload", "https_json"}:
        raise PolicyError("research_source_kind", "Only reviewed uploads and exact HTTPS JSON feeds are supported", 422)
    for field in ("approved", "license_reviewed", "fetch_enabled"):
        if type(config.get(field, False)) is not bool:
            raise PolicyError("research_input", f"{field} must be a boolean", 422)
    mode = config.get("data_mode", "REAL")
    if mode not in {"REAL", "DEMO"}:
        raise PolicyError("research_data_mode", "Sources must explicitly distinguish REAL and DEMO", 422)
    previous = tx.get("research_source", source_id)
    if previous and previous.get("data_mode", "REAL") != mode and any(
            run.get("source_id") == source_id for run in tx.list("research_run")):
        raise PolicyError("research_data_mode", "A used source cannot be relabeled between REAL and DEMO")
    reference = _text(config.get("legal_review_reference", ""), "License/legal review reference", 500, 12)
    cost = _integer(config.get("cost_minor", 0), "Estimated fetch cost", 0, 50000)
    source = {
        "name": _text(config.get("name", source_id), "Source name", 200), "kind": kind,
        "url": _url(config.get("url", "")), "approved": config.get("approved", False),
        "license_reviewed": config.get("license_reviewed", False), "legal_review_reference": reference,
        "fetch_enabled": config.get("fetch_enabled", False) if kind == "https_json" else False,
        "minimum_quality": _integer(config.get("minimum_quality", 80), "Minimum quality", 60, 100),
        "minimum_interval_seconds": _integer(config.get("minimum_interval_seconds", 3600), "Fetch interval", 300, 2592000),
        "cost_minor": cost, "cost_evidence": _text(config.get("cost_evidence", ""), "Cost/free-source evidence", 500, 12),
        "data_mode": mode, "version": int((previous or {}).get("version", 0)) + 1,
        "configured_by": policy.actor_id(actor), "configured_at": tx.now,
    }
    source = tx.put("research_source", source_id, source)
    settings = policy.get_settings(tx)
    approved_sources = set(settings.get("approved_sources", []))
    if source["approved"] and source["license_reviewed"]:
        approved_sources.add(source_id)
    else:
        approved_sources.discard(source_id)
    # Authority changes fence queued work; source revocation also takes effect
    # in this transaction, without rewriting any previous evidence or leads.
    policy.update_settings(tx, {"approved_sources": sorted(approved_sources)}, actor)
    tx.audit(actor, "research.source_configured", {"source_id": source_id, "version": source["version"],
             "kind": kind, "approved": source["approved"], "fetch_enabled": source["fetch_enabled"]})
    return source


def sources(tx):
    return tx.list("research_source")


def _approved(tx, source_id):
    source = tx.get("research_source", source_id)
    if not source or not source.get("approved") or not source.get("license_reviewed"):
        raise PolicyError("research_source_unapproved", "An explicitly approved and licensed source is required", 403)
    if source_id not in policy.get_settings(tx).get("approved_sources", []):
        raise PolicyError("research_source_unapproved", "This source has been revoked by policy", 403)
    return source


def _normalize(source, record):
    if not isinstance(record, dict) or set(record) - RECORD_KEYS:
        raise PolicyError("research_record", "A company record contains unsupported fields", 422)
    company = _text(record.get("company", ""), "Company", 200)
    email = _text(record.get("email", ""), "Business email", 254).lower()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
        raise PolicyError("research_record", "A valid business email is required", 422)
    domain = _text(record.get("domain", email.rsplit("@", 1)[1]), "Business domain", 253).lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", domain):
        raise PolicyError("research_record", "A normalized business domain is required", 422)
    citation = _url(record.get("source_url", source["url"]))
    evidence = _text(record.get("evidence", ""), "Attributed company evidence", 4000, 20)
    entity, address = record.get("entity_type", "unknown"), record.get("address_type", "unknown")
    if entity not in {"legal_person", "sole_trader", "unknown"} or address not in {"generic", "personal", "unknown"}:
        raise PolicyError("research_record", "Contact entity/address classification is invalid", 422)
    factors = {"company": 20, "business_email": 20, "source_citation": 20, "evidence": 20,
               "domain_matches_email": 10 if domain == email.rsplit("@", 1)[1] else 0,
               "contact_classification": 10 if entity != "unknown" and address != "unknown" else 0}
    return {"company": company, "email": email, "domain": domain, "entity_type": entity,
            "address_type": address, "source_url": citation, "need": evidence,
            "quality": {"score": sum(factors.values()), "factors": factors,
                        "method": "attributed-directory-v1.1", "not_legal_clearance": True}}


def ingest(tx, source_id, records, actor, key):
    """Persist attributed candidates from a reviewed source, never send contact."""
    source = _approved(tx, source_id)
    policy.guard(tx, "research", actor)
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise PolicyError("research_record_limit", f"Import between 1 and {MAX_RECORDS} companies", 422)
    payload = {"source_id": source_id, "records": records}
    cached = tx.idempotency_get("research.ingest", key, payload)
    if cached is not None:
        return cached
    normalized = [_normalize(source, record) for record in records]
    lead_ids, rejected, duplicates = [], [], 0
    demo = source.get("data_mode", "REAL") == "DEMO"
    for index, record in enumerate(normalized):
        if record["quality"]["score"] < source["minimum_quality"]:
            rejected.append({"index": index, "reason": "quality_below_threshold", "score": record["quality"]["score"]})
            continue
        matches = [lead for lead in tx.list("lead") if (
            lead.get("email", "").lower() == record["email"] or lead.get("domain", "").lower() == record["domain"]
            or lead.get("company", "").casefold() == record["company"].casefold())]
        # v1.0 deduplicates across all records. Never turn an old demonstration
        # record into a real prospect, or vice versa, through that legacy path.
        if any(bool(lead.get("is_test")) != demo for lead in matches):
            rejected.append({"index": index, "reason": "demo_real_identity_collision"})
            continue
        quality = record.pop("quality")
        lead = workflow.register_lead(tx, {**record, "source_id": source_id, "is_test": demo},
            actor=actor, key="research-" + fingerprint({"key": key, "index": index})[:50])
        duplicates += int(bool(matches))
        attribution = {"source_id": source_id, "source_url": record["source_url"], "collected_at": tx.now,
                       "source_version": source["version"], "license_review_reference": source["legal_review_reference"],
                       "evidence": record["need"], "untrusted_input": True}
        citations = list(lead.get("citations", []))
        if not any(item.get("source_id") == source_id and item.get("source_url") == record["source_url"]
                   and item.get("evidence") == record["need"] for item in citations):
            citations.append(attribution)
        lead.update(citations=citations[-50:], quality=quality, record_mode="DEMO" if demo else "REAL",
                    provenance={"kind": "approved_source", "source_id": source_id, "approved": True,
                                "source_version": source["version"], "license_reviewed": True,
                                "review_reference": source["legal_review_reference"]})
        tx.put("lead", lead["id"], lead)
        lead_ids.append(lead["id"])
    result = tx.create("research_run", {"source_id": source_id, "source_version": source["version"],
        "record_mode": source.get("data_mode", "REAL"), "lead_ids": list(dict.fromkeys(lead_ids)),
        "accepted": len(lead_ids), "duplicates": duplicates, "rejected": rejected, "network_fetch": False,
        "created_at": tx.now, "external_sends": 0})
    tx.audit(actor, "research.imported", {"run_id": result["id"], "source_id": source_id,
             "accepted": len(lead_ids), "duplicates": duplicates, "rejected": len(rejected)})
    tx.idempotency_put("research.ingest", key, payload, result)
    return result


class _Deadline:
    def __init__(self, seconds, clock=None):
        self.clock = clock or time.monotonic
        self.expires_at = self.clock() + seconds

    def remaining(self):
        remaining = self.expires_at - self.clock()
        if remaining <= 0:
            raise TimeoutError("The total approved-source fetch deadline expired")
        return remaining

    def arm(self, sock):
        sock.settimeout(self.remaining())


def _resolve_addresses(host, deadline):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise TimeoutError("Approved-source DNS resolver capacity is occupied")
    result: queue.Queue[Any] = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM), None))
        except Exception as error:
            result.put((None, error))
        finally:
            _DNS_SLOTS.release()

    worker = threading.Thread(target=resolve, daemon=True, name="jarvis-source-dns")
    try:
        worker.start()
    except BaseException:
        _DNS_SLOTS.release()
        raise
    try:
        addresses, error = result.get(timeout=deadline.remaining())
    except queue.Empty:
        raise TimeoutError("The total approved-source fetch deadline expired during DNS") from None
    deadline.remaining()
    if error is not None:
        raise error
    return {item[4][0] for item in addresses}


class _DeadlineReader(io.RawIOBase):
    """Re-arm before EVERY recv, including HTTP header readline/read loops."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self.deadline.arm(self.sock)
        count = self.sock.recv_into(buffer)
        self.deadline.remaining()
        return count


class _DeadlineSocket:
    """Socket duck type used by http.client for bounded sends and buffered reads."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def makefile(self, mode, buffering=None):
        if mode != "rb":
            raise ValueError("Only read-only HTTP response streams are supported")
        return io.BufferedReader(_DeadlineReader(self.sock, self.deadline))

    def sendall(self, data):
        remaining = memoryview(data)
        while remaining:
            self.deadline.arm(self.sock)
            count = self.sock.send(remaining)
            self.deadline.remaining()
            if not count:
                raise ConnectionError("The approved-source connection closed during the request")
            remaining = remaining[count:]

    def close(self):
        self.sock.close()


def _connect_numeric(address, timeout):
    # Do not call create_connection: it performs another getaddrinfo even for
    # numeric destinations. DNS validation and the actual TCP target stay bound.
    family = socket.AF_INET6 if ipaddress.ip_address(address[0]).version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(address)
        return sock
    except BaseException:
        sock.close()
        raise


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, *, deadline=None):
        self.deadline = deadline or _Deadline(timeout)
        self.tls_context = ssl.create_default_context()
        super().__init__(host, port=443, timeout=timeout, context=self.tls_context)
        self.address = address
        self.connect_socket = _connect_numeric

    def connect(self):
        # Resolve once, then pin the actual TCP connection to the checked IP.
        # Certificate hostname verification and SNI still use the original host.
        raw, encrypted = None, None
        try:
            raw = self.connect_socket((self.address, 443), self.deadline.remaining())
            self.deadline.arm(raw)
            encrypted = self.tls_context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.deadline.arm(encrypted)
            encrypted.do_handshake()
            self.deadline.remaining()
            self.sock = cast(Any, _DeadlineSocket(encrypted, self.deadline))
        except BaseException:
            if encrypted is not None:
                encrypted.close()
            elif raw is not None:
                raw.close()
            raise


class HTTPSJSONAdapter:
    """No redirects, env proxies, credentials or private/rebinding DNS targets."""
    def __init__(self, *, clock=None, timeout=TIMEOUT_SECONDS):
        self.clock, self.timeout = clock, timeout

    def fetch(self, url):
        deadline = _Deadline(self.timeout, self.clock)
        parsed = urlsplit(_url(url))
        addresses = _resolve_addresses(parsed.hostname, deadline)
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise PolicyError("research_ssrf", "All resolved source addresses must be public", 403)
        connection = _PinnedHTTPSConnection(parsed.hostname, sorted(addresses)[0], deadline.remaining(), deadline=deadline)
        try:
            deadline.remaining()
            connection.request("GET", parsed.path or "/", headers={
                "Accept": "application/json", "User-Agent": "JarvisLite/1.1 approved-source-reader"})
            deadline.remaining()
            response = connection.getresponse()
            deadline.remaining()
            if response.status != 200:
                raise PolicyError("research_http", "The approved source did not return HTTP 200; redirects are never followed", 502)
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise PolicyError("research_content_type", "Source must return application/json", 502)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            deadline.remaining()
            if len(raw) > MAX_RESPONSE_BYTES:
                raise PolicyError("research_size", "Source response exceeds 256 KiB", 502)
            decoded = json.loads(raw)
            deadline.remaining()
            return decoded
        finally:
            connection.close()


def fetch_source(tx, source_id, actor, key, adapter=None):
    """Read one approved feed with a durable at-most-once accounting intent."""
    source = _approved(tx, source_id)
    policy.guard(tx, "research", actor)
    if source["kind"] != "https_json" or not source.get("fetch_enabled"):
        raise PolicyError("research_fetch_disabled", "This source has not been enabled for network fetching", 403)
    if os.getenv("JARVIS_ALLOW_RESEARCH_NETWORK", "false").lower() != "true":
        raise PolicyError("research_network_disabled", "Research network access needs separate runtime authorization", 403)
    request = {"source_id": source_id, "version": source["version"]}
    previous = tx.idempotency_get("research.fetch", key, request)
    if previous is not None:
        return previous
    intent_id = hashlib.sha256(("research.fetch\0" + key).encode()).hexdigest()
    existing = tx.get("research_fetch", intent_id)
    if existing:
        if existing.get("request") != request:
            raise PolicyError("idempotency_conflict", "Research key belongs to another source/version")
        return existing.get("result") or {"status": "UNCERTAIN", "intent_id": intent_id,
            "manual_reconciliation_required": True, "reservation_id": existing.get("reservation_id")}
    if any(item.get("source_id") == source_id and item.get("created_at", 0) > tx.now - source["minimum_interval_seconds"]
           for item in tx.list("research_fetch")):
        raise PolicyError("research_rate_limit", "The approved source fetch interval has not elapsed")
    reservation = budget.reserve(tx, vendor="research:" + source_id, workflow_id="research:" + source_id,
        amount_minor=source["cost_minor"], category="tools", idempotency_key="research-" + intent_id[:50], actor=actor)
    epoch = policy.get_settings(tx).get("safety_epoch", 0)
    intent = tx.put("research_fetch", intent_id, {"source_id": source_id, "request": request,
        "status": "ATTEMPTED", "created_at": tx.now, "safety_epoch": epoch,
        "reservation_id": reservation["id"], "result": None})
    tx.audit(actor, "research.fetch_started", {"intent_id": intent_id, "source_id": source_id,
             "reservation_id": reservation["id"]})
    tx.checkpoint()
    current = _approved(tx, source_id)
    policy.guard(tx, "research", actor)
    if (current["version"] != source["version"] or not current.get("fetch_enabled")
            or policy.get_settings(tx).get("safety_epoch", 0) != epoch
            or os.getenv("JARVIS_ALLOW_RESEARCH_NETWORK", "false").lower() != "true"):
        raise PolicyError("research_authority_changed", "Research authority changed after reservation; review the durable intent", 423)
    try:
        records = (adapter or HTTPSJSONAdapter()).fetch(source["url"])
        if isinstance(records, dict) and set(records) == {"companies"}:
            records = records["companies"]
        result = ingest(tx, source_id, records, actor, "fetch-" + intent_id[:60])
    except (PolicyError, OSError, ValueError, TypeError, http.client.HTTPException):
        result = {"status": "UNCERTAIN", "intent_id": intent_id, "manual_reconciliation_required": True,
                  "reservation_id": reservation["id"]}
        intent.update(status="UNKNOWN", result=result)
        tx.put("research_fetch", intent_id, intent)
        tx.put("manual_task", "research-review-" + intent_id[:40], {"title": "Review failed source fetch and reserved cost",
            "status": "PENDING", "source_id": source_id, "intent_id": intent_id,
            "reservation_id": reservation["id"], "created_at": tx.now})
        tx.audit(actor, "research.fetch_uncertain", {"intent_id": intent_id})
        return result
    result.update(network_fetch=True, reservation_id=reservation["id"], estimated_cost_minor=source["cost_minor"],
                  cost_requires_settlement=source["cost_minor"] > 0)
    tx.put("research_run", result["id"], result)
    # A free-source contract is explicit evidence for zero cost. A paid estimate
    # remains reserved until the existing administrator settlement API receives
    # an invoice/usage receipt; a successful download is not a billing receipt.
    if source["cost_minor"] == 0:
        budget.settle(tx, reservation["id"], actual_minor=0, calls=1, actor=actor)
    intent.update(status="COMPLETED", result=result)
    tx.put("research_fetch", intent_id, intent)
    tx.idempotency_put("research.fetch", key, request, result)
    tx.audit(actor, "research.fetch_completed", {"intent_id": intent_id, "run_id": result["id"]})
    return result
