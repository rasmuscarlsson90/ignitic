"""Explicit contact authorization and signed email-gateway receipts.

Domain records are evidence reviewed by an operator, not a claim that Jarvis has
queried DNS or Brevo. ``run_checks`` executes an isolated offline safety probe;
it never proves real-world deliverability. Native Brevo delivery callbacks need
a trusted gateway that validates the provider and signs this bounded contract.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import quote, urlparse

from . import policy
from .policy import PolicyError
from .store import Database, fingerprint


APPROVE = "I APPROVE LIVE CUSTOMER CONTACT"
REVOKE = "REVOKE LIVE CUSTOMER CONTACT"
CONTACT_POLICY_VERSION = "1.1.0"
REFERENCE_FIELDS = ("domain_review_reference", "spf_reference", "dkim_reference", "dmarc_reference", "review_reference")
APPROVED_TEMPLATE_IDS = frozenset({"contact-standard-v1", "followup-standard-v1"})
TERMINAL_DELIVERY = {"HARD_BOUNCED", "COMPLAINT"}
DELIVERY_STATES = {"sent": "SENT", "delivered": "DELIVERED", "deferred": "DEFERRED",
                   "soft_bounce": "SOFT_BOUNCED", "hard_bounce": "HARD_BOUNCED", "complaint": "COMPLAINT"}


def _text(value, name, maximum=500, *, required=True):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise PolicyError("outreach_input", f"{name} must be bounded text", 422)
    value = value.strip()
    if required and len(value) < 12:
        raise PolicyError("outreach_evidence", f"{name} needs a meaningful evidence reference", 422)
    return value


def _sender(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        raise PolicyError("outreach_sender", "A single reviewed sender email is required", 422)
    return value.lower()


def _secret(tx, name):
    return policy.get_secret(tx, name) or os.getenv(name)


def _origin(tx):
    value = policy.get_settings(tx).get("public_origin", "")
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return ""
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        return ""
    return value.rstrip("/")


def _configuration(tx):
    return tx.get("outreach_config", "global", {"provider": "brevo", "enabled": False, "sender": ""})


def _configuration_fingerprint(tx):
    # Both configuration surfaces are fenced: old email configuration APIs do
    # not silently retain a new contact approval after changing the sender.
    credentials = {name: hashlib.sha256(value.encode()).hexdigest() if value else "unconfigured"
                   for name in ("BREVO_API_KEY", "EMAIL_WEBHOOK_SECRET", "EMAIL_DELIVERY_WEBHOOK_SECRET")
                   for value in (_secret(tx, name),)}
    return fingerprint({"policy_version": CONTACT_POLICY_VERSION, "credential_identities": credentials,
                        "outreach": _configuration(tx), "email": tx.get("connector_config", "email", {}),
                        "origin": _origin(tx)})


def configure(tx, config, actor):
    policy.require_admin(actor)
    if not isinstance(config, dict) or set(config) - {"provider", "enabled", "sender", "approved_template_ids",
                                                   "auto_review_source_ids", "reviewed_lawful_basis", *REFERENCE_FIELDS}:
        raise PolicyError("outreach_input", "Unknown contact configuration fields", 422)
    current = _configuration(tx)
    updated = {**current, **config}
    if updated.get("provider") != "brevo" or type(updated.get("enabled")) is not bool:
        raise PolicyError("outreach_provider", "Only the reviewed Brevo adapter is supported", 422)
    updated["sender"] = _sender(updated.get("sender", ""))
    templates = updated.get("approved_template_ids", [])
    if (not isinstance(templates, list) or len(templates) > len(APPROVED_TEMPLATE_IDS)
            or any(not isinstance(item, str) or item not in APPROVED_TEMPLATE_IDS for item in templates)
            or len(set(templates)) != len(templates)):
        raise PolicyError("outreach_template", "Only unique reviewed canonical contact/follow-up template IDs may be approved", 422)
    updated["approved_template_ids"] = sorted(templates)
    source_ids = updated.get("auto_review_source_ids", [])
    if (not isinstance(source_ids, list) or len(source_ids) > 50
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", item) for item in source_ids)
            or len(set(source_ids)) != len(source_ids)):
        raise PolicyError("outreach_sources", "Use unique reviewed source IDs, at most fifty", 422)
    updated["auto_review_source_ids"] = sorted(source_ids)
    updated["reviewed_lawful_basis"] = _text(updated.get("reviewed_lawful_basis", ""), "reviewed lawful basis", 1000,
                                             required=bool(source_ids))
    for name in REFERENCE_FIELDS:
        updated[name] = _text(updated.get(name, ""), name, required=False)
    updated.update(verification_kind="operator_evidence_review", updated_by=policy.actor_id(actor), updated_at=tx.now)
    tx.put("outreach_config", "global", updated)
    # Revocation is recorded rather than deleting prior approval evidence.
    approval = tx.get("outreach_approval", "global")
    if approval and approval.get("approved"):
        approval.update(approved=False, revoked_at=tx.now, reason="Contact configuration changed")
        tx.put("outreach_approval", "global", approval)
    tx.audit(actor, "outreach.configuration_reviewed", {"provider": "brevo", "sender": updated["sender"],
                                                        "enabled": updated["enabled"], "verification_kind": updated["verification_kind"]})
    return status(tx)


def _requirements(tx):
    config = _configuration(tx)
    email = tx.get("connector_config", "email", {})
    settings = policy.get_settings(tx)
    references = all(len(config.get(name, "").strip()) >= 12 for name in REFERENCE_FIELDS)
    return {
        "contact_config_enabled": config.get("enabled") is True,
        "provider_and_sender": bool(config.get("provider") == "brevo" and config.get("sender")
                                    and config.get("sender") == str(email.get("sender", "")).lower()
                                    and config.get("sender") in settings.get("approved_senders", [])),
        "reviewed_domain_evidence": references,
        "reviewed_sender_and_lawful_policy": all(email.get(key) is True for key in ("approved", "spf", "dkim", "dmarc", "legal_review")),
        "brevo_key_configured": bool(_secret(tx, "BREVO_API_KEY")),
        "delivery_gateway_key_configured": bool(_secret(tx, "EMAIL_DELIVERY_WEBHOOK_SECRET")),
        "inbound_gateway_key_configured": bool(_secret(tx, "EMAIL_WEBHOOK_SECRET")),
        "https_unsubscribe_origin": bool(_origin(tx)),
        "mandate_within_limits": (settings.get("minimum_list_price_minor", 0) >= 500000
                                  and settings.get("minimum_discounted_price_minor", 0) >= 450000
                                  and 0 <= settings.get("max_discount_bps", 10001) <= 1000
                                  and 0 <= settings.get("rolling_tools_limit_minor", 50001) <= 50000
                                  and 0 <= settings.get("total_budget_minor", 200001) <= 200000),
        "emergency_stop_clear": not settings.get("kill_switch"),
    }


def _approval_current(tx, approval):
    if not approval or approval.get("approved") is not True:
        return False
    evidence = tx.get("outreach_test", approval.get("test_id", ""))
    epoch = policy.get_settings(tx).get("safety_epoch", 0)
    current = _configuration_fingerprint(tx)
    return bool(evidence and evidence.get("passed") is True and evidence.get("safety_epoch") == epoch
                and evidence.get("probe_version") == CONTACT_POLICY_VERSION
                and evidence.get("configuration_fingerprint") == current
                and approval.get("configuration_fingerprint") == current and approval.get("safety_epoch") == epoch)


def status(tx):
    config = _configuration(tx)
    approval = tx.get("outreach_approval", "global")
    checks = _requirements(tx)
    approval_current = _approval_current(tx, approval)
    runtime = os.getenv("JARVIS_ALLOW_LIVE_CONTACT", "false").lower() == "true"
    settings = policy.get_settings(tx)
    general_live = (settings.get("mode") == "LIVE" and settings.get("live_approved") is True
                    and settings.get("legal_reviewed") is True
                    and os.getenv("JARVIS_ALLOW_LIVE", "false").lower() == "true")
    return {"config": config, "runtime_contact_enabled": runtime, "approval_current": approval_current,
            "general_live_authorized": general_live,
            "release_locked": policy.INTERNAL_ONLY_RELEASE,
            "live_contact_authorized": not policy.INTERNAL_ONLY_RELEASE and runtime and general_live and approval_current and all(checks.values()),
            "checks": checks, "blocked_reasons": [key for key, ok in checks.items() if not ok]
                + ([] if runtime else ["runtime_contact_disabled"])
                + ([] if general_live else ["general_live_approval_required"])
                + ([] if approval_current else ["explicit_contact_approval_required"])
                + (["internal_only_release"] if policy.INTERNAL_ONLY_RELEASE else []),
            "verification_kind": "operator_evidence_review", "test_scope": "isolated_offline_safety_probe",
            "native_brevo_webhook_signature": False, "delivery_gateway_required": True}


def _offline_probe():
    from . import connectors, workflow
    db = Database("sqlite:///:memory:")
    actor = {"id": "outreach-offline-probe", "role": "admin"}
    try:
        with db.transaction() as probe:
            policy.initialize(probe)
            result = workflow.run_demo(probe, actor=actor, key="contact-safety-probe")
            payment = result["payment"]
            if payment.get("approval") is not None or payment.get("status") != "DRAFT":
                return False
            try:
                connectors.request_payment(probe, payment["id"], idempotency_key="must-not-send", actor=actor)
            except PolicyError as error:
                if error.code != "payment_approval_required":
                    return False
            else:
                return False
            if probe.list("external_intent") or probe.list("budget_entry"):
                return False
            policy.set_kill_switch(probe, True, "Offline contact safety test", actor)
            try:
                policy.guard(probe, "email", actor=actor)
            except PolicyError as error:
                return error.code == "kill_switch" and result["lead"]["state"] == "PAYMENT_APPROVAL_REQUIRED"
            return False
    finally:
        db.close()


def run_checks(tx, actor):
    """Server-owned checks; no user supplied passed flag or provider HTTP."""
    policy.require_admin(actor)
    checks = _requirements(tx)
    try:
        checks["offline_journey_and_manual_payment_stop"] = _offline_probe()
    except (PolicyError, ValueError, KeyError):
        checks["offline_journey_and_manual_payment_stop"] = False
    result = tx.create("outreach_test", {"checks": checks, "passed": all(checks.values()),
        "configuration_fingerprint": _configuration_fingerprint(tx),
        "safety_epoch": policy.get_settings(tx).get("safety_epoch", 0), "created_at": tx.now,
        "scope": "isolated_offline_safety_probe", "live_deliverability_verified": False,
        "probe_version": CONTACT_POLICY_VERSION,
        "requested_by": policy.actor_id(actor)})
    tx.audit(actor, "outreach.safety_checks_executed", {"test_id": result["id"], "passed": result["passed"], "scope": result["scope"]})
    return result


def record_review(tx, payload, actor):
    policy.require_admin(actor)
    if not isinstance(payload, dict) or set(payload) - {"statement", "test_id", "legal_review_reference", "reason"}:
        raise PolicyError("outreach_review", "Unknown approval fields; client test results are not trusted", 422)
    statement = payload.get("statement")
    reason = _text(payload.get("reason"), "reason")
    if statement == REVOKE:
        approval = tx.get("outreach_approval", "global", {})
        approval.update(approved=False, revoked_at=tx.now, revoked_by=policy.actor_id(actor), reason=reason)
    elif statement == APPROVE:
        legal = _text(payload.get("legal_review_reference"), "legal review reference")
        test = tx.get("outreach_test", str(payload.get("test_id", "")))
        if (not test or not test.get("passed") or test.get("probe_version") != CONTACT_POLICY_VERSION
                or test.get("configuration_fingerprint") != _configuration_fingerprint(tx)
                or test.get("safety_epoch") != policy.get_settings(tx).get("safety_epoch", 0)
                or not 0 <= tx.now - test.get("created_at", 0) <= 86400 or not all(_requirements(tx).values())):
            raise PolicyError("outreach_tests_required", "Fresh server-run safety checks for this exact configuration are required", 403)
        approval = {"approved": True, "approved_at": tx.now, "approved_by": policy.actor_id(actor),
                    "legal_review_reference": legal, "test_id": test["id"], "reason": reason,
                    "configuration_fingerprint": _configuration_fingerprint(tx),
                    "policy_version": CONTACT_POLICY_VERSION,
                    "safety_epoch": policy.get_settings(tx).get("safety_epoch", 0)}
    else:
        raise PolicyError("outreach_statement", "An exact separate customer-contact approval or revocation statement is required", 422)
    result = tx.put("outreach_approval", "global", approval)
    tx.audit(actor, "outreach.contact_review_recorded", {"approved": result["approved"], "reason": reason,
                                                       "test_id": result.get("test_id")})
    return status(tx)


def guard_contact(tx, lead):
    """Additional to the shared policy, per-recipient and template controls."""
    current = status(tx)
    if not current["live_contact_authorized"]:
        raise PolicyError("live_contact_disabled", "Separate runtime, current tests and legal customer-contact approval are required", 403)
    from .upgrade import classify_record
    if lead is None or classify_record(lead) != "REAL":
        raise PolicyError("live_contact_real_lead", "Demo or unclassified data cannot authorize real customer contact", 403)
    review = lead.get("compliance", {})
    if review.get("policy_authorization") is not None:
        delegated = policy_review(tx, lead)
        if (not delegated or review.get("policy_authorization") != delegated["policy_authorization"]
                or review.get("lawful_basis") != delegated["lawful_basis"]
                or review.get("legal_review") != delegated["legal_review"]):
            raise PolicyError("contact_policy_stale", "This delegated contact review no longer matches the current source/legal policy", 403)
    return current


def policy_review(tx, lead):
    """Delegate only an explicitly reviewed source/contact policy, never B2B law.

    The caller must compare the complete result with the current policy at the
    workflow boundary. It is not a client-provided grant or a role escalation.
    Personal addresses, sole traders and missing/stale provenance stay manual.
    """
    from .upgrade import classify_record
    if not status(tx)["live_contact_authorized"] or classify_record(lead) != "REAL":
        return None
    config = _configuration(tx)
    if lead.get("entity_type") != "legal_person" or lead.get("address_type") != "generic":
        return None
    provenance = lead.get("provenance", {})
    source_id = provenance.get("source_id")
    if source_id not in config.get("auto_review_source_ids", []) or not config.get("reviewed_lawful_basis", "").strip():
        return None
    source = tx.get("research_source", source_id)
    settings = policy.get_settings(tx)
    if (not source or source.get("approved") is not True or source.get("license_reviewed") is not True
            or source.get("data_mode") != "REAL" or source_id not in settings.get("approved_sources", [])
            or provenance.get("license_reviewed") is not True
            or type(provenance.get("source_version")) is not int
            or provenance.get("source_version") != source.get("version")):
        return None
    quality = lead.get("quality", {}).get("score")
    if type(quality) is not int or quality < max(80, source.get("minimum_quality", 80)):
        return None
    citation = next((item for item in lead.get("citations", []) if isinstance(item, dict)
                     and item.get("source_id") == source_id and item.get("source_version") == source["version"]
                     and isinstance(item.get("source_url"), str) and urlparse(item["source_url"]).scheme == "https"
                     and bool(urlparse(item["source_url"]).hostname)
                     and urlparse(item["source_url"]).hostname == urlparse(source.get("url", "")).hostname
                     and isinstance(item.get("evidence"), str) and len(item["evidence"].strip()) >= 20), None)
    email = lead.get("email")
    if (citation is None or not isinstance(email, str)
            or tx.get("suppression", hashlib.sha256(email.strip().lower().encode()).hexdigest())):
        return None
    approval = tx.get("outreach_approval", "global")
    return {"approved": True, "simulation": False, "channel": "email",
            "lawful_basis": config["reviewed_lawful_basis"], "legal_review": approval["legal_review_reference"],
            "policy_authorization": {"kind": "reviewed_source_contact_policy", "policy_version": CONTACT_POLICY_VERSION,
                "configuration_fingerprint": approval["configuration_fingerprint"], "approval_test_id": approval["test_id"],
                "lead_id": lead["id"], "source_id": source_id, "source_version": source["version"],
                "citation_fingerprint": fingerprint(citation)}}


def _unsubscribe_secret(tx):
    secret = policy.get_secret(tx, "EMAIL_UNSUBSCRIBE_SECRET")
    if not secret:
        secret = secrets.token_urlsafe(48)
        policy.set_secret(tx, "EMAIL_UNSUBSCRIBE_SECRET", secret, {"id": "outreach-key-bootstrap", "role": "admin"})
    return secret


def unsubscribe_headers(tx, lead):
    origin = _origin(tx)
    if not origin:
        raise PolicyError("unsubscribe_origin", "A reviewed HTTPS public origin is required for unsubscribe links", 403)
    recipient = _sender(lead.get("email", ""))
    recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()
    grant_id = hashlib.sha256((lead["id"] + "\0" + recipient_hash).encode()).hexdigest()
    # Bind to the actual recipient snapshot. Editing a lead's address must not
    # redirect an old recipient's opt-out onto a different mailbox.
    if not tx.get("unsubscribe_grant", grant_id):
        tx.create("unsubscribe_grant", {"lead_id": lead["id"], "recipient": recipient,
                  "recipient_hash": recipient_hash, "created_at": tx.now}, id=grant_id)
    raw = json.dumps({"lead": lead["id"], "recipient_hash": recipient_hash, "scope": "email-unsubscribe-v1"},
                     sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    signature = hmac.new(_unsubscribe_secret(tx).encode(), encoded.encode(), hashlib.sha256).hexdigest()
    url = origin + "/api/contact/unsubscribe?token=" + quote(encoded + "." + signature, safe="")
    return {"List-Unsubscribe": "<" + url + ">", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def process_unsubscribe(tx, token, *, confirm=True):
    """GET may preview; only explicit POST/one-click submission suppresses."""
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 1024:
            raise ValueError
        encoded, signature = token.split(".")
        secret = policy.get_secret(tx, "EMAIL_UNSUBSCRIBE_SECRET")
        expected = hmac.new((secret or "").encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature.encode(), expected.encode()):
            raise ValueError
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if (set(data) != {"lead", "recipient_hash", "scope"} or data.get("scope") != "email-unsubscribe-v1"
                or not isinstance(data.get("lead"), str) or not isinstance(data.get("recipient_hash"), str)):
            raise ValueError
        grant_id = hashlib.sha256((data["lead"] + "\0" + data["recipient_hash"]).encode()).hexdigest()
        grant = tx.get("unsubscribe_grant", grant_id)
        if (not grant or grant.get("lead_id") != data["lead"]
                or hashlib.sha256(grant.get("recipient", "").encode()).hexdigest() != data["recipient_hash"]):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise PolicyError("unsubscribe_token", "Invalid unsubscribe link", 400) from None
    if not confirm:
        return {"confirmation_required": True, "unsubscribed": False}
    from . import connectors
    connectors.unsubscribe(tx, email=grant["recipient"], reason="Signed one-click unsubscribe", actor="recipient")
    return {"unsubscribed": True}


def receive_delivery(tx, raw_body, signature, timestamp, event_id=None):
    """Gateway HMAC over timestamp + '.' + exact JSON bytes; never a live action."""
    if not isinstance(raw_body, bytes) or len(raw_body) > 32768:
        raise PolicyError("delivery_body", "A bounded signed JSON event is required", 413)
    secret = _secret(tx, "EMAIL_DELIVERY_WEBHOOK_SECRET")
    try:
        stamp = int(timestamp)
        expected = hmac.new((secret or "").encode(), str(stamp).encode() + b"." + raw_body, hashlib.sha256).hexdigest()
        if (not secret or abs(tx.now - stamp) > 300 or not isinstance(signature, str)
                or not hmac.compare_digest(signature.encode(), expected.encode())):
            raise ValueError
        payload = json.loads(raw_body)
        required = {"event_id", "provider", "message_id", "provider_id", "recipient", "type", "occurred_at"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError
        signed_id = payload["event_id"]
        if (not isinstance(signed_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", signed_id)
                or event_id is not None and event_id != signed_id):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise PolicyError("delivery_signature", "Invalid, expired or mismatched signed delivery event", 401) from None
    previous = tx.idempotency_get("email.delivery.v11", signed_id, payload)
    if previous is not None:
        return previous
    if payload["provider"] != "brevo" or payload["type"] not in DELIVERY_STATES:
        raise PolicyError("delivery_provider", "Unsupported provider or delivery status", 422)
    occurred = payload["occurred_at"]
    if type(occurred) not in {int, float} or not 0 <= occurred <= tx.now + 300:
        raise PolicyError("delivery_timestamp", "A finite provider observation time is required", 422)
    message = tx.get("email_message", payload["message_id"])
    if (not message or message.get("provider") != "brevo" or message.get("provider_id") != payload["provider_id"]
            or message.get("recipient", "").lower() != str(payload["recipient"]).lower()
            or occurred < message.get("created_at", 0) - 300):
        raise PolicyError("delivery_binding", "Receipt does not match the actual provider message and recipient", 409)
    new_status = DELIVERY_STATES[payload["type"]]
    current_status = message.get("status")
    permanent = new_status in TERMINAL_DELIVERY
    apply = permanent or current_status not in TERMINAL_DELIVERY and occurred >= message.get("delivery_observed_at", 0)
    if current_status == "COMPLAINT" and new_status != "COMPLAINT":
        apply = False
    if apply:
        message.update(status=new_status, delivery_observed_at=max(occurred, message.get("delivery_observed_at", 0)),
                       delivery_event_id=signed_id)
        tx.put("email_message", message["id"], message)
    if permanent:
        from . import connectors
        connectors.unsubscribe(tx, email=message["recipient"], reason="Verified " + payload["type"], actor="delivery-gateway")
    event = tx.create("email_delivery_event", {**payload, "signature_verified": True, "applied": apply,
        "created_at": tx.now, "received_while_stopped": bool(policy.get_settings(tx).get("kill_switch"))}, id=signed_id)
    tx.idempotency_put("email.delivery.v11", signed_id, payload, event)
    tx.audit("delivery-gateway", "email.delivery_verified", {"event_id": signed_id, "message_id": message["id"],
                                                            "type": payload["type"], "applied": apply})
    return event
