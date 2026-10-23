"""Deterministic business workflows; model output never authorizes an action.

All callers must use a serialized store transaction. External effects belong to
the connectors, which persist verified provider evidence before calling the
event hooks in this module. No arbitrary state-update API is provided.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
from typing import Any, Callable, NoReturn
from urllib.parse import urlparse

from .policy import PolicyError, actor_id, get_settings, guard, require_admin


STATES = (
    "DISCOVERED", "RESEARCHED", "QUALIFIED", "COMPLIANCE_CHECK", "READY_TO_CONTACT",
    "CONTACTED", "REPLIED", "NEGOTIATING", "PROPOSAL_READY", "PROPOSAL_SENT",
    "ACCEPTED", "DELIVERY_IN_PROGRESS", "DELIVERY_VERIFIED", "PAYMENT_APPROVAL_REQUIRED",
    "PAYMENT_REQUESTED", "PAYMENT_CONFIRMED", "COMPLETED", "REJECTED", "UNSUBSCRIBED",
    "NO_RESPONSE", "DELIVERY_FAILED", "CANCELLED", "PAYMENT_FAILED", "MANUAL_REVIEW", "PAUSED",
)
ROLE_CAPABILITIES = {
    "CEO": ["prioritize", "coordinate", "daily_report"],
    "LEAD_RESEARCH": ["approved_source_import", "deduplicate", "research", "qualify"],
    "SALES": ["draft_contact", "process_verified_reply", "price_standard_proposal"],
    "DELIVERY": ["configure_template", "run_local_acceptance_tests", "report"],
    "FINANCE": ["draft_payment", "await_admin_approval", "process_verified_provider_event"],
    "COMPLIANCE": ["review_contact", "suppress", "pause"],
}
SERVICE_ID = "inbound-enquiry-assistant"
SERVICE = {
    "id": SERVICE_ID,
    "name": "AI assistant for incoming customer enquiries",
    "approved": True,
    "version": "1.0",
    "scope": ["Classify incoming enquiries", "Prepare unsent reply drafts", "Register leads", "Suggest follow-ups"],
    "delivery_days": 5,
    "customer_responsibilities": ["Provide approved test data", "Authorize each integration and access scope", "Review drafts before enabling sending"],
    "recurring_minor": 0,
    "terms": "standard-v1",
    "limitations": ["No guaranteed sales or revenue", "No autonomous legal advice", "Production installation requires an agreement and explicit customer access", "Sending and external writes are permission controlled"],
    "acceptance_criteria": ["Classifies the agreed test cases", "Produces unsent draft replies", "Records a test lead and proposed follow-up", "Emits a delivery test report without external writes"],
}


def _error(code: str, message: str, status: int = 409) -> NoReturn:
    raise PolicyError(code, message, status=status)


def _text(value: Any, name: str, *, limit: int = 4000, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        _error("INVALID_INPUT", f"{name} must be text of at most {limit} characters", 422)
    value = value.strip()
    if required and not value:
        _error("INVALID_INPUT", f"{name} is required", 422)
    return value


def _integer(value: Any, name: str, *, minimum: int = 0, maximum: int = 100_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _error("INVALID_INPUT", f"{name} must be an integer between {minimum} and {maximum}", 422)
    return value


def _lead(tx: Any, lead_id: str) -> dict:
    result = tx.get("lead", lead_id)
    if result is None:
        _error("LEAD_NOT_FOUND", "The lead does not exist", 404)
    return result


def _state(lead: dict, *states: str) -> None:
    if lead.get("state") not in states:
        _error("INVALID_TRANSITION", "This operation is not allowed in the current workflow state")


def _move(tx: Any, lead: dict, target: str, actor: Any, *, reason: str) -> dict:
    if target not in STATES:
        raise ValueError("Unknown internal workflow state")
    before = lead["state"]
    lead = {**lead, "state": target, "updated_at": tx.now}
    tx.put("lead", lead["id"], lead)
    tx.audit(actor_id(actor), "workflow.transition", {
        "lead_id": lead["id"], "from": before, "to": target, "reason": reason,
    })
    return lead


def _once(tx: Any, scope: str, key: str, payload: dict, fn: Callable[[], dict]) -> dict:
    key = _text(key, "idempotency key", limit=128)
    previous = tx.idempotency_get(scope, key, payload)
    if previous is not None:
        return previous
    result = fn()
    tx.idempotency_put(scope, key, payload, result)
    return result


def service_registry(tx: Any) -> list[dict]:
    """A reviewed registry, not arbitrary services proposed by a language model."""
    overrides = {item["id"]: item for item in tx.list("service")}
    return [deepcopy(overrides.get(SERVICE_ID, SERVICE))]


def register_lead(tx: Any, payload: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        guard(tx, "research", actor=actor)
        company = _text(payload.get("company"), "company", limit=200)
        email = _text(payload.get("email"), "email", limit=254).lower()
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
            _error("INVALID_EMAIL", "A single valid email address is required", 422)
        domain = _text(payload.get("domain", email.rsplit("@", 1)[1]), "domain", limit=253).lower().strip(".")
        if not re.fullmatch(r"[a-z0-9.-]+", domain):
            _error("INVALID_DOMAIN", "Use a normalized business domain, not a URL", 422)
        source_id = payload.get("source_id", "manual")
        settings = get_settings(tx)
        if source_id not in settings.get("approved_sources", ["manual", "demo", "customer_inbound"]):
            _error("SOURCE_NOT_APPROVED", "Research is limited to approved sources")
        source_url = _text(payload.get("source_url", ""), "source URL", limit=2000, required=False)
        if source_url and urlparse(source_url).scheme not in ("https", "http"):
            _error("INVALID_SOURCE", "Source references must be HTTP(S) URLs", 422)
        for existing in tx.list("lead"):
            if (existing.get("email") == email or existing.get("domain") == domain
                    or existing.get("company", "").casefold() == company.casefold()):
                tx.audit(actor_id(actor), "lead.duplicate_prevented", {"lead_id": existing["id"]})
                return existing
        entity_type = payload.get("entity_type", "unknown")
        address_type = payload.get("address_type", "unknown")
        if entity_type not in ("legal_person", "sole_trader", "unknown") or address_type not in ("generic", "personal", "unknown"):
            _error("INVALID_CONTACT_TYPE", "Specify the business entity and address classification", 422)
        lead = tx.create("lead", {
            "company": company, "email": email, "domain": domain, "source_id": source_id,
            "source_url": source_url, "collected_at": tx.now, "created_at": tx.now,
            "entity_type": entity_type, "address_type": address_type,
            "is_test": bool(payload.get("is_test", False)), "state": "DISCOVERED",
            "record_mode": "DEMO" if payload.get("is_test") else "UNCLASSIFIED",
            "need": _text(payload.get("need", ""), "need", required=False),
            "channel": "email", "compliance_approved": False,
            "compliance": {"approved": False, "approval_mode": None},
        })
        tx.audit(actor_id(actor), "lead.discovered", {"lead_id": lead["id"], "source_id": source_id})
        return lead
    return _once(tx, "lead.register", key, payload, apply)


def research_lead(tx: Any, lead_id: str, evidence: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "DISCOVERED")
        guard(tx, "research", actor=actor, lead=lead)
        summary = _text(evidence.get("summary"), "research summary")
        lead.update({"research": {"summary": summary, "source_url": lead.get("source_url", ""),
                                  "collected_at": tx.now, "untrusted_input": True}, "need": summary})
        return _move(tx, lead, "RESEARCHED", actor, reason="Approved-source evidence recorded; no source instructions executed")
    return _once(tx, "lead.research", key, {"lead_id": lead_id, "evidence": evidence}, apply)


def qualify_lead(tx: Any, lead_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "RESEARCHED")
        guard(tx, "research", actor=actor, lead=lead)
        need = lead.get("need", "").lower()
        qualified = any(word in need for word in ("inquir", "enquir", "support", "customer", "kund", "email", "mail", "förfråg"))
        lead["qualification"] = {"qualified": qualified, "service_id": SERVICE_ID if qualified else None,
                                 "method": "deterministic-service-fit-v1", "reviewed_at": tx.now}
        return _move(tx, lead, "QUALIFIED" if qualified else "REJECTED", actor,
                     reason="Matches the approved inbound-enquiry template" if qualified else "No approved service matches the recorded need")
    return _once(tx, "lead.qualify", key, {"lead_id": lead_id}, apply)


def review_compliance(tx: Any, lead_id: str, review: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "QUALIFIED", "MANUAL_REVIEW")
        settings = get_settings(tx)
        simulation = bool(review.get("simulation"))
        if simulation:
            if settings["mode"] != "DEMO" or not lead.get("is_test"):
                _error("SIMULATION_ONLY", "Simulated legal review is restricted to DEMO test leads")
        else:
            if not isinstance(actor, dict) or actor.get("role") != "admin":
                from . import outreach
                delegated = outreach.policy_review(tx, lead)
                if delegated is None or delegated != review:
                    require_admin(actor)
        basis = _text(review.get("lawful_basis", ""), "lawful basis", limit=1000, required=False)
        legal_evidence = _text(review.get("legal_review", ""), "legal review", limit=2000, required=False)
        consent_evidence = _text(review.get("consent_evidence", ""), "consent evidence", limit=2000, required=False)
        explicit_consent = review.get("explicit_consent") is True and bool(consent_evidence)
        approved = (review.get("approved") is True and bool(basis) and bool(legal_evidence)
                    and lead.get("entity_type") in ("legal_person", "sole_trader")
                    and lead.get("address_type") in ("generic", "personal")
                    and review.get("channel", "email") == "email"
                    and ((lead.get("entity_type") == "legal_person" and lead.get("address_type") == "generic")
                         or explicit_consent))
        compliance = {"approved": approved, "lawful_basis": basis, "legal_review": legal_evidence,
                      "channel": "email", "reviewed_by": actor_id(actor), "reviewed_at": tx.now,
                      "approval_mode": settings["mode"], "simulation": simulation,
                      "explicit_consent": explicit_consent, "consent_evidence": consent_evidence}
        if review.get("policy_authorization"):
            compliance["policy_authorization"] = deepcopy(review["policy_authorization"])
        lead.update({"compliance": compliance, "compliance_approved": approved,
                     "lawful_basis": basis, "legal_review": legal_evidence, "channel": "email"})
        return _move(tx, lead, "COMPLIANCE_CHECK" if approved else "MANUAL_REVIEW", actor,
                     reason="Recorded legal/channel review" if approved else "Contact policy needs explicit review")
    return _once(tx, "lead.compliance", key, {"lead_id": lead_id, "review": review}, apply)


def contact_message(lead: dict, *, followup=False) -> dict:
    """Reviewed deterministic template: company substitution never grants authority."""
    company = _text(lead.get("company"), "company", limit=200)
    if any(c in company for c in "\r\n"):
        _error("INVALID_COMPANY", "Use a single-line company name", 422)
    return {
        "subject": ("Following up: " if followup else "") + f"Incoming customer enquiries at {company}",
        "body": (f"Hello {company},\n\n" + ("Following up on our enquiry-assistant scope. " if followup else "")
                 + "We offer a scoped assistant that classifies incoming customer enquiries and prepares reply drafts for your review. "
                 "It does not guarantee sales or revenue. Would a test demonstration be useful?\n\n"
                 "Reply unsubscribe if you do not want further contact."),
    }


def prepare_contact(tx: Any, lead_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "COMPLIANCE_CHECK")
        guard(tx, "contact", actor=actor, lead=lead)
        if not lead.get("compliance_approved"):
            _error("COMPLIANCE_REQUIRED", "Contact needs an approved legal/channel review")
        lead["contact_draft"] = {
            **contact_message(lead),
            "status": "DRAFT", "template": "contact-standard-v1",
        }
        return _move(tx, lead, "READY_TO_CONTACT", actor, reason="Prepared an unsent standard contact draft")
    return _once(tx, "lead.prepare_contact", key, {"lead_id": lead_id}, apply)


def _verified_message(tx: Any, lead_id: str, message_id: str, purpose: str) -> dict:
    message = tx.get("email_message", message_id)
    if not message or message.get("lead_id") != lead_id or message.get("purpose") != purpose or message.get("status") != "SENT":
        _error("MESSAGE_EVIDENCE_REQUIRED", "The connector must persist the matching sent message first")
    return message


def record_contacted(tx: Any, lead_id: str, message_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "READY_TO_CONTACT")
        _verified_message(tx, lead_id, message_id, "contact")
        lead["last_contact_at"] = tx.now
        lead["contact_message_id"] = message_id
        return _move(tx, lead, "CONTACTED", actor, reason="Email connector recorded delivery submission")
    return _once(tx, "lead.contacted", key, {"lead_id": lead_id, "message_id": message_id}, apply)


def record_reply(tx: Any, lead_id: str, reply: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        event = tx.get("inbound_event", reply.get("event_id", ""))
        if not event or not event.get("signature_verified") or event.get("lead_id") != lead_id:
            _error("VERIFIED_REPLY_REQUIRED", "A verified provider event must precede an inbound reply")
        text = _text(event.get("text"), "reply")
        lead["latest_reply"] = {"event_id": event["id"], "text": text, "received_at": tx.now,
                                "untrusted_input": True}
        if lead["state"] in ("CONTACTED", "NO_RESPONSE"):
            return _move(tx, lead, "REPLIED", actor, reason="Received a signature-verified reply; text is untrusted data")
        tx.put("lead", lead_id, lead)
        tx.audit(actor_id(actor), "lead.reply_recorded", {"lead_id": lead_id, "event_id": event["id"]})
        return lead
    return _once(tx, "lead.reply", key, {"lead_id": lead_id, "event_id": reply.get("event_id")}, apply)


def price_proposal(tx: Any, payload: dict) -> dict:
    """Integer minor-unit arithmetic, never a floating-point or model decision."""
    settings = get_settings(tx)
    list_price = _integer(payload.get("list_price_minor", 500000), "list price")
    discount = _integer(payload.get("discount_bps", 0), "discount basis points", maximum=10000)
    if list_price < max(500000, settings["minimum_list_price_minor"]):
        _error("PRICE_FLOOR", "The ordinary starting price is below the approved minimum")
    if discount > min(1000, settings["max_discount_bps"]):
        _error("DISCOUNT_LIMIT", "The proposed discount exceeds the approved limit")
    amount = (list_price * (10000 - discount) + 9999) // 10000
    if amount < max(450000, settings["minimum_discounted_price_minor"]):
        _error("PRICE_FLOOR", "The discounted starting price is below the absolute minimum")
    vat_rate = _integer(payload.get("vat_rate_bps", 2500), "VAT basis points", maximum=10000)
    vat = (amount * vat_rate + 5000) // 10000
    return {"list_price_minor": list_price, "discount_bps": discount, "price_minor": amount,
            "amount_minor": amount, "vat_rate_bps": vat_rate, "vat_minor": vat,
            "total_minor": amount + vat, "currency": "SEK"}


def prepare_proposal(tx: Any, lead_id: str, payload: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "REPLIED", "NEGOTIATING")
        guard(tx, "proposal", actor=actor, lead=lead)
        service_id = payload.get("service_id", SERVICE_ID)
        services = {item["id"]: item for item in service_registry(tx)}
        service = services.get(service_id)
        if not service or service.get("approved") is not True:
            _error("SERVICE_NOT_APPROVED", "Only the approved service registry may be sold")
        if payload.get("terms", "standard-v1") != "standard-v1":
            _error("NONSTANDARD_TERMS", "Nonstandard contracts require manual legal review")
        price = price_proposal(tx, payload)
        billing_stage = payload.get("billing_stage", "after_delivery")
        if billing_stage not in {"after_delivery", "before_delivery"}:
            _error("BILLING_STAGE", "Choose an explicit standard payment stage", 422)
        if billing_stage == "before_delivery":
            billing_review = tx.get("pipeline_config", "global", {})
            if not (billing_review.get("billing_stage") == "before_delivery"
                    and billing_review.get("authorization_epoch") == get_settings(tx).get("safety_epoch")):
                require_admin(actor)
        if lead["state"] == "REPLIED":
            lead = _move(tx, lead, "NEGOTIATING", actor, reason="Discussing an approved standard service")
        proposal = tx.create("proposal", {
            **price, "lead_id": lead_id, "service_id": service_id, "status": "DRAFT",
            "scope": deepcopy(service["scope"]), "delivery_days": service["delivery_days"],
            "customer_responsibilities": deepcopy(service["customer_responsibilities"]),
            "recurring_minor": service["recurring_minor"], "terms": service["terms"],
            "billing_stage": billing_stage,
            "billing_terms": ("Payment after verified delivery" if billing_stage == "after_delivery" else
                              "Payment before production delivery; installation is planned only after verified payment and explicit customer access"),
            "limitations": deepcopy(service["limitations"]),
            "acceptance_criteria": deepcopy(service["acceptance_criteria"]), "created_at": tx.now,
        })
        lead["proposal_id"] = proposal["id"]
        _move(tx, lead, "PROPOSAL_READY", actor, reason="Deterministic price and standard contract validated")
        tx.audit(actor_id(actor), "proposal.created", {"lead_id": lead_id, "proposal_id": proposal["id"],
                                                    "amount_minor": price["amount_minor"], "currency": "SEK"})
        return proposal
    return _once(tx, "proposal.prepare", key, {"lead_id": lead_id, "proposal": payload}, apply)


def record_proposal_sent(tx: Any, lead_id: str, message_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "PROPOSAL_READY")
        _verified_message(tx, lead_id, message_id, "proposal")
        proposal = tx.get("proposal", lead["proposal_id"])
        proposal.update({"status": "SENT", "sent_at": tx.now, "message_id": message_id})
        tx.put("proposal", proposal["id"], proposal)
        return _move(tx, lead, "PROPOSAL_SENT", actor, reason="Email connector recorded the standard proposal submission")
    return _once(tx, "proposal.sent", key, {"lead_id": lead_id, "message_id": message_id}, apply)


def proposal_message(tx: Any, lead_id: str) -> dict:
    """Render the approved offer from immutable priced fields, not model prose."""
    lead = _lead(tx, lead_id)
    proposal = tx.get("proposal", lead.get("proposal_id", ""))
    if not proposal or proposal.get("lead_id") != lead_id:
        _error("PROPOSAL_REQUIRED", "A validated proposal is required before sending an offer")
    if lead.get("state") == "PROPOSAL_READY" or proposal.get("status") == "DRAFT":
        # Unsent drafts do not retain authority after an administrator changes
        # the mandate. Never silently rewrite an accepted customer's contract.
        try:
            current = price_proposal(tx, {name: proposal.get(name) for name in
                                         ("list_price_minor", "discount_bps", "vat_rate_bps")})
        except PolicyError as error:
            raise PolicyError("STALE_PROPOSAL", "This unsent proposal no longer meets the pricing mandate; review or create a new quote") from error
        if any(proposal.get(name) != value for name, value in current.items()):
            _error("STALE_PROPOSAL", "Stored proposal totals are inconsistent; review or create a new quote")

    def sek(amount: int) -> str:
        return f"{amount // 100}.{amount % 100:02d} SEK"

    body = [f"Proposal {proposal['id']} for {lead['company']}",
            f"Service: {SERVICE['name']}",
            f"Starting price excluding VAT: {sek(proposal['amount_minor'])}",
            f"VAT ({proposal['vat_rate_bps'] / 100:g}%): {sek(proposal['vat_minor'])}",
            f"Total including VAT: {sek(proposal['total_minor'])}",
            f"Delivery: {proposal['delivery_days']} business days after agreed access and test data are available.",
            f"Recurring fee: {sek(proposal['recurring_minor'])} excluding VAT per month."]
    body.append("Payment terms: " + proposal.get("billing_terms", "Payment after verified delivery"))
    for heading, items in (("Scope", proposal["scope"]),
                           ("Customer responsibilities", proposal["customer_responsibilities"]),
                           ("Acceptance criteria", proposal["acceptance_criteria"]),
                           ("Limitations and prerequisites", proposal["limitations"])):
        body.append(heading + ":")
        body.extend("- " + item for item in items)
    body.extend([f"Contract terms: {proposal['terms']}. No additional terms are accepted automatically.",
                 f"To explicitly accept this exact offer, reply: I accept proposal {proposal['id']}",
                 "Reply unsubscribe to stop further marketing contact."])
    return {"subject": f"Standard AI assistant proposal {proposal['id']}", "body": "\n".join(body)}


def accept_proposal(tx: Any, lead_id: str, evidence: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "PROPOSAL_SENT")
        proposal = tx.get("proposal", lead["proposal_id"])
        event = tx.get("inbound_event", evidence.get("event_id", ""))
        if (not event or not event.get("signature_verified") or event.get("lead_id") != lead_id
                or event.get("from", "").lower() != lead["email"]):
            _error("CUSTOMER_ACCEPTANCE_REQUIRED", "An explicit, verified customer acceptance is required")
        acceptance = event.get("text", "").strip().rstrip(".! ").casefold()
        approved_text = {f"i accept proposal {proposal['id']}".casefold(),
                         f"jag accepterar offert {proposal['id']}".casefold()}
        if acceptance not in approved_text:
            _error("CUSTOMER_ACCEPTANCE_REQUIRED", "Ambiguous replies need manual review; models cannot accept for customers")
        proposal.update({"status": "ACCEPTED", "accepted_at": tx.now,
                         "acceptance_event_id": event["id"], "accepted_terms": proposal["terms"]})
        tx.put("proposal", proposal["id"], proposal)
        return _move(tx, lead, "ACCEPTED", actor, reason="Verified customer explicitly accepted this exact proposal")
    return _once(tx, "proposal.accept", key, {"lead_id": lead_id, "event_id": evidence.get("event_id")}, apply)


def classify_enquiry(text: str) -> str:
    text = text.casefold()
    if any(term in text for term in ("invoice", "payment", "faktura", "billing")):
        return "billing"
    if any(term in text for term in ("broken", "support", "help", "problem", "hjälp")):
        return "support"
    if any(term in text for term in ("price", "quote", "demo", "purchase", "offert", "pris")):
        return "sales"
    return "other"


def process_enquiry(tx: Any, payload: dict, *, actor: Any, key: str) -> dict:
    """First deliverable: classify, draft, register an inquiry and a follow-up.

    The output is persisted but never sent and text never becomes executable
    instructions. A customer inbox/CRM write needs a separately authorized adapter.
    """
    if payload.get("context") == "acceptance_test":
        _error("TEST_CONTEXT_RESERVED", "Acceptance-test context is assigned by the delivery runner", 422)
    return _process_enquiry(tx, payload, actor=actor, key=key)


def _process_enquiry(tx: Any, payload: dict, *, actor: Any, key: str, acceptance_case=None) -> dict:
    def apply() -> dict:
        guard(tx, "research", actor=actor)
        text = _text(payload.get("text"), "enquiry")
        category = classify_enquiry(text)
        enquiry = tx.create("enquiry", {
            "customer_id": _text(payload.get("customer_id", "demo"), "customer ID", limit=200),
            "text": text, "category": category, "untrusted_input": True,
            "draft_reply": f"Thank you for your enquiry. Our team will review your {category} request and respond after checking the details.",
            "draft_status": "UNSENT", "suggested_follow_up": "Review and respond within one business day",
            "created_at": tx.now,
            "context": "acceptance_test" if acceptance_case is not None else "customer_enquiry",
            "test_case": acceptance_case,
        })
        if acceptance_case is None:
            tx.create("manual_task", {"title": "Review enquiry response draft", "enquiry_id": enquiry["id"],
                                      "status": "PENDING", "role": "DELIVERY", "created_at": tx.now},
                      id="enquiry-review-" + enquiry["id"])
        tx.audit(actor_id(actor), "enquiry.classified", {"enquiry_id": enquiry["id"], "category": category,
                                                       "external_write": False})
        return enquiry
    return _once(tx, "enquiry.process", key, payload, apply)


def deliver_service(tx: Any, lead_id: str, config: dict, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "ACCEPTED")
        guard(tx, "delivery", actor=actor, lead=lead)
        if config.get("mode", "test") != "test":
            tx.create("manual_task", {"title": "Review customer production installation and explicit access",
                                      "lead_id": lead_id, "status": "PENDING", "role": "DELIVERY", "created_at": tx.now})
            return _move(tx, lead, "MANUAL_REVIEW", actor, reason="Production installation needs explicit agreement/access; not claimed complete")
        lead = _move(tx, lead, "DELIVERY_IN_PROGRESS", actor, reason="Running the approved template in a local test environment")
        cases = config.get("test_cases", [
            {"text": "Could you give me a price quote?", "expected": "sales"},
            {"text": "Our service is broken, please help", "expected": "support"},
            {"text": "Please review this invoice", "expected": "billing"},
        ])
        if not isinstance(cases, list) or not 1 <= len(cases) <= 50:
            _error("INVALID_TEST_CASES", "Provide between one and fifty bounded acceptance cases", 422)
        results = []
        for index, case in enumerate(cases):
            if not isinstance(case, dict) or case.get("expected") not in ("sales", "support", "billing", "other"):
                _error("INVALID_TEST_CASE", "Each case needs text and a supported expected category", 422)
            inquiry = _process_enquiry(tx, {"text": _text(case.get("text"), "test enquiry"),
                                          "customer_id": lead_id}, actor=actor, key=f"{key}:case:{index}",
                                       acceptance_case=index + 1)
            results.append({"case": index + 1, "expected": case["expected"], "actual": inquiry["category"],
                            "passed": case["expected"] == inquiry["category"], "draft_unsent": True,
                            "enquiry_id": inquiry["id"]})
        passed = all(item["passed"] for item in results)
        delivery = tx.create("delivery", {
            "lead_id": lead_id, "proposal_id": lead["proposal_id"], "service_id": SERVICE_ID,
            "mode": "test", "status": "TEST_VERIFIED" if passed else "FAILED",
            "production_installed": False, "external_writes": 0, "tests": results,
            "business_name": _text(config.get("business_name", lead["company"]), "business name", limit=200),
            "report": "Local acceptance tests completed; production access and installation are separate tasks.",
            "created_at": tx.now,
        })
        lead["delivery_id"] = delivery["id"]
        local_only_live = passed and get_settings(tx)["mode"] == "LIVE"
        _move(tx, lead, "DELIVERY_IN_PROGRESS" if local_only_live else "DELIVERY_VERIFIED" if passed else "DELIVERY_FAILED", actor,
              reason=("Local tests passed, but actual customer installation is still unverified" if local_only_live
                      else "Local template acceptance tests passed" if passed else "Acceptance tests failed; no production changes made"))
        if local_only_live:
            tx.create("manual_task", {"title": "Verify actual customer deployment, agreement and explicit production access",
                                      "lead_id": lead_id, "delivery_id": delivery["id"], "status": "PENDING",
                                      "role": "DELIVERY", "created_at": tx.now})
        if not passed:
            tx.create("manual_task", {"title": "Investigate failed local delivery tests", "lead_id": lead_id,
                                      "status": "PENDING", "role": "DELIVERY", "created_at": tx.now})
        return delivery
    return _once(tx, "delivery.test", key, {"lead_id": lead_id, "config": config}, apply)


def require_delivery_for_payment(tx: Any, lead_id: str) -> None:
    """A DEMO test report cannot become real delivery authority after a mode change."""
    if get_settings(tx)["mode"] != "LIVE":
        return
    lead = _lead(tx, lead_id)
    proposal = tx.get("proposal", lead.get("proposal_id", ""))
    if (proposal and proposal.get("billing_stage") == "before_delivery"
            and proposal.get("status") == "ACCEPTED" and proposal.get("acceptance_event_id")
            and proposal.get("billing_terms") == "Payment before production delivery; installation is planned only after verified payment and explicit customer access"):
        acceptance = tx.get("inbound_event", proposal["acceptance_event_id"])
        if (acceptance and acceptance.get("signature_verified") is True
                and acceptance.get("provider") != "demo" and acceptance.get("lead_id") == lead_id
                and acceptance.get("from", "").lower() == lead.get("email", "").lower()):
            return
    delivery = tx.get("delivery", lead.get("delivery_id", ""))
    if (not delivery or delivery.get("lead_id") != lead_id
            or delivery.get("status") != "PRODUCTION_VERIFIED"
            or delivery.get("production_installed") is not True):
        _error("PRODUCTION_DELIVERY_REQUIRED", "Local tests do not verify a customer production installation; manual deployment review is required")


def prepare_payment_gate(tx: Any, lead_id: str, *, actor: Any, key: str) -> dict:
    """Explicit new prepayment terms; legacy delivery-first contracts stay intact."""
    def apply():
        lead = _lead(tx, lead_id)
        _state(lead, "ACCEPTED")
        proposal = tx.get("proposal", lead.get("proposal_id", ""))
        if not proposal or proposal.get("billing_stage") != "before_delivery":
            _error("DELIVERY_FIRST", "This accepted contract requires verified delivery first")
        require_delivery_for_payment(tx, lead_id)
        tx.put("manual_task", "payment-review-" + proposal["id"], {
            "title": "Manually approve the payment request", "lead_id": lead_id,
            "status": "PENDING", "role": "FINANCE", "created_at": tx.now})
        return _move(tx, lead, "PAYMENT_APPROVAL_REQUIRED", actor,
                     reason="Customer explicitly accepted prepayment terms; exact administrator payment approval still required")
    return _once(tx, "payment.prepayment_gate", key, {"lead_id": lead_id}, apply)


def payment_approval_required(tx: Any, lead_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "DELIVERY_VERIFIED")
        require_delivery_for_payment(tx, lead_id)
        tx.create("manual_task", {"title": "Manually approve the payment request", "lead_id": lead_id,
                                  "status": "PENDING", "role": "FINANCE", "created_at": tx.now})
        return _move(tx, lead, "PAYMENT_APPROVAL_REQUIRED", actor, reason="Payment creation/submission requires a separate admin approval")
    return _once(tx, "payment.await_approval", key, {"lead_id": lead_id}, apply)


def record_payment_requested(tx: Any, lead_id: str, payment_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "PAYMENT_APPROVAL_REQUIRED")
        payment = tx.get("payment", payment_id)
        require_delivery_for_payment(tx, lead_id)
        if (not payment or payment.get("lead_id") != lead_id or payment.get("status") != "REQUESTED"
                or not payment.get("approval") or payment.get("proposal_id") != lead.get("proposal_id")):
            _error("APPROVED_PAYMENT_REQUIRED", "The payment connector must record a matching approved request first")
        lead["payment_id"] = payment_id
        return _move(tx, lead, "PAYMENT_REQUESTED", actor, reason="Connector submitted a separately approved payment request")
    return _once(tx, "payment.requested", key, {"lead_id": lead_id, "payment_id": payment_id}, apply)


def record_payment_confirmed(tx: Any, lead_id: str, event_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "PAYMENT_REQUESTED", "PAYMENT_FAILED")
        event = tx.get("payment_event", event_id)
        payment = tx.get("payment", lead.get("payment_id", ""))
        if (not event or not event.get("signature_verified") or not payment
                or event.get("lead_id") != lead_id or event.get("payment_id") != payment["id"]
                or event.get("status") != "paid" or payment.get("status") != "CONFIRMED"
                or event.get("amount_minor") != payment.get("total_minor") or event.get("currency") != "SEK"
                or event.get("provider_reference") != payment.get("provider_reference")):
            _error("VERIFIED_PAYMENT_REQUIRED", "Only a matching verified provider payment can confirm revenue")
        from . import upgrade
        if upgrade.classify_record(lead) == "REAL" and (event.get("livemode") is not True or payment.get("livemode") is not True):
            _error("REAL_PAYMENT_REQUIRED", "Sandbox receipts cannot advance a real customer's paid workflow")
        lead = _move(tx, lead, "PAYMENT_CONFIRMED", actor, reason="Signed provider event confirms the correct payment amount and currency")
        proposal = tx.get("proposal", lead.get("proposal_id", ""), {})
        if proposal.get("billing_stage") == "before_delivery":
            tx.put("delivery_plan", "delivery-plan-" + lead_id, {
                "lead_id": lead_id, "payment_id": payment["id"], "status": "AWAITING_CUSTOMER_ACCESS",
                "production_installed": False, "created_at": tx.now})
            tx.put("manual_task", "delivery-access-" + lead_id, {
                "lead_id": lead_id, "title": "Review delivery agreement and customer integration access",
                "status": "PENDING", "role": "DELIVERY", "created_at": tx.now})
            return _move(tx, lead, "DELIVERY_IN_PROGRESS", actor, reason="Verified prepayment recorded; plan installation without claiming delivery complete")
        delivery = tx.get("delivery", lead.get("delivery_id", ""))
        verified_demo = get_settings(tx)["mode"] == "DEMO" and delivery and delivery.get("status") == "TEST_VERIFIED"
        verified_production = delivery and delivery.get("status") == "PRODUCTION_VERIFIED" and delivery.get("production_installed") is True
        if verified_demo or verified_production:
            lead = _move(tx, lead, "COMPLETED", actor, reason="Test delivery verified and provider payment confirmed; no bank payout is implied")
        return lead
    return _once(tx, "payment.confirmed", key, {"lead_id": lead_id, "event_id": event_id}, apply)


def record_payment_failed(tx: Any, lead_id: str, event_id: str, *, actor: Any, key: str) -> dict:
    def apply() -> dict:
        lead = _lead(tx, lead_id)
        _state(lead, "PAYMENT_REQUESTED", "PAYMENT_FAILED")
        event = tx.get("payment_event", event_id)
        payment = tx.get("payment", lead.get("payment_id", ""))
        if (not event or not event.get("signature_verified") or not payment
                or event.get("lead_id") != lead_id or event.get("payment_id") != payment["id"]
                or event.get("status") != "failed" or payment.get("status") != "FAILED"
                or event.get("amount_minor") != payment.get("total_minor") or event.get("currency") != "SEK"
                or event.get("provider_reference") != payment.get("provider_reference")):
            _error("VERIFIED_PAYMENT_REQUIRED", "Payment failures also require a matching verified provider event")
        tx.create("manual_task", {"title": "Review the failed payment; do not retry automatically",
                                  "lead_id": lead_id, "payment_id": payment["id"],
                                  "status": "PENDING", "role": "FINANCE", "created_at": tx.now})
        return _move(tx, lead, "PAYMENT_FAILED", actor, reason="Signed provider event reports payment failure; finance review required")
    return _once(tx, "payment.failed", key, {"lead_id": lead_id, "event_id": event_id}, apply)


def pause_lead(tx: Any, lead_id: str, reason: str, *, actor: Any, key: str, target: str = "PAUSED") -> dict:
    def apply() -> dict:
        if target not in ("PAUSED", "MANUAL_REVIEW", "CANCELLED", "UNSUBSCRIBED", "NO_RESPONSE", "PAYMENT_FAILED"):
            _error("INVALID_TRANSITION", "This administrative operation cannot advance the sales pipeline")
        lead = _lead(tx, lead_id)
        lead["pause_reason"] = _text(reason, "reason", limit=1000)
        return _move(tx, lead, target, actor, reason="Workflow stopped or escalated without an external action")
    return _once(tx, "lead.pause", key, {"lead_id": lead_id, "reason": reason, "target": target}, apply)


def agent_report(tx: Any) -> dict:
    leads = tx.list("lead")
    return {"generated_at": tx.now, "roles": deepcopy(ROLE_CAPABILITIES),
            "leads": len(leads), "states": dict(Counter(item["state"] for item in leads)),
            "pending_manual_tasks": sum(item.get("status") == "PENDING" for item in tx.list("manual_task")),
            "mode": get_settings(tx)["mode"], "kill_switch": get_settings(tx)["kill_switch"],
            "capabilities": {"local_template_tests": True, "production_installation": False,
                             "autonomous_payment_approval": False, "autonomous_bank_payout": False}}


def run_demo(tx: Any, *, actor: Any, key: str) -> dict:
    """Coordinate six logical roles up to the mandatory human finance gate."""
    def apply() -> dict:
        from . import connectors

        settings = get_settings(tx)
        if settings["mode"] != "DEMO":
            _error("DEMO_ONLY", "The simulated customer journey is restricted to DEMO mode")
        guard(tx, "research", actor=actor)
        recipient = settings.get("demo_test_recipients", ["demo-customer@example.com"])[0]
        lead = register_lead(tx, {"company": "Demo Customer AB", "email": recipient,
                                 "domain": recipient.rsplit("@", 1)[1], "source_id": "demo",
                                 "entity_type": "legal_person", "address_type": "generic", "is_test": True},
                             actor=actor, key=f"{key}:lead")
        # The default demo is deliberately one company, not an autonomous mass import.
        if lead["state"] != "DISCOVERED":
            return {"lead": lead, "status": "EXISTING_DEMO", "manual_approval_required": lead["state"] == "PAYMENT_APPROVAL_REQUIRED"}
        lead = research_lead(tx, lead["id"], {"summary": "Needs help classifying incoming customer enquiries and preparing reply drafts"}, actor=actor, key=f"{key}:research")
        lead = qualify_lead(tx, lead["id"], actor=actor, key=f"{key}:qualify")
        lead = review_compliance(tx, lead["id"], {"approved": True, "channel": "email", "simulation": True,
                                                "lawful_basis": "Approved synthetic demo recipient only",
                                                "legal_review": "Simulated review, never valid for LIVE contact"},
                                 actor=actor, key=f"{key}:compliance")
        lead = prepare_contact(tx, lead["id"], actor=actor, key=f"{key}:draft")
        connectors.send_email(tx, lead_id=lead["id"], **{k: lead["contact_draft"][k] for k in ("subject", "body")},
                              idempotency_key=f"{key}:contact", purpose="contact", actor=actor)
        connectors.simulate_incoming_reply(tx, lead_id=lead["id"], text="Please send a quote for an incoming customer enquiries assistant",
                                           event_id=f"{key}:reply", actor=actor)
        proposal = prepare_proposal(tx, lead["id"], {}, actor=actor, key=f"{key}:proposal")
        connectors.send_email(tx, lead_id=lead["id"], **proposal_message(tx, lead["id"]),
                              idempotency_key=f"{key}:proposal-email", purpose="proposal", actor=actor)
        event = connectors.simulate_incoming_reply(tx, lead_id=lead["id"], text=f"I accept proposal {proposal['id']}",
                                                   event_id=f"{key}:acceptance", actor=actor)
        accept_proposal(tx, lead["id"], {"event_id": event["id"]}, actor=actor, key=f"{key}:accept")
        delivery = deliver_service(tx, lead["id"], {"mode": "test"}, actor=actor, key=f"{key}:delivery")
        payment_approval_required(tx, lead["id"], actor=actor, key=f"{key}:finance-gate")
        payment = connectors.create_payment_draft(tx, lead_id=lead["id"], proposal_id=proposal["id"],
                                                  amount_minor=proposal["amount_minor"], vat_minor=proposal["vat_minor"],
                                                  service_id=SERVICE_ID, reference=f"DEMO-{proposal['id']}",
                                                  idempotency_key=f"{key}:payment", actor=actor)
        tx.audit(actor_id(actor), "agents.demo_coordinated", {"lead_id": lead["id"], "roles": list(ROLE_CAPABILITIES),
                                                            "stopped_at": "PAYMENT_APPROVAL_REQUIRED", "external_calls": 0})
        return {"lead": _lead(tx, lead["id"]), "proposal": proposal, "delivery": delivery,
                "payment": payment, "status": "AWAITING_MANUAL_APPROVAL", "manual_approval_required": True}
    return _once(tx, "agents.demo", key, {"journey": "inbound-enquiry-v1"}, apply)
