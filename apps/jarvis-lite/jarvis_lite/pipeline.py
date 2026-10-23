"""Bounded sales preparation and opt-in durable scheduling.

The coordinator cannot grant contact authority, invent customer replies, approve
payments or deploy a customer installation. Every provider effect stays behind
the existing connectors and their current server-side authorization checks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from . import policy, workflow
from .policy import PolicyError
from .store import fingerprint


DEFAULTS = {"enabled": False, "source_ids": [], "interval_seconds": 3600,
            "follow_up_delay_seconds": 3 * 86400, "max_leads_per_tick": 5,
            "billing_stage": "after_delivery", "revision": 0,
            "authorization_epoch": None, "last_scheduled_at": None}
TERMINAL = {"REJECTED", "UNSUBSCRIBED", "CANCELLED", "DELIVERY_FAILED", "PAYMENT_FAILED"}


def _config(tx) -> dict:
    return {**deepcopy(DEFAULTS), **tx.get("pipeline_config", "global", {})}


def configure(tx, config: dict, *, actor: Any) -> dict:
    policy.require_admin(actor)
    allowed = {"enabled", "source_ids", "interval_seconds", "follow_up_delay_seconds",
               "max_leads_per_tick", "billing_stage", "reason"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise PolicyError("pipeline_config", "Use only the reviewed preparation scheduler fields", 422)
    reason = config.get("reason", "")
    if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 500:
        raise PolicyError("pipeline_review", "A meaningful administrator review reason is required", 422)
    updated = {**_config(tx), **{k: v for k, v in config.items() if k != "reason"}}
    if type(updated["enabled"]) is not bool:
        raise PolicyError("pipeline_config", "enabled must be a boolean", 422)
    for field, low, high in (("interval_seconds", 300, 86400),
                             ("follow_up_delay_seconds", 86400, 30 * 86400),
                             ("max_leads_per_tick", 1, 10)):
        if type(updated[field]) is not int or not low <= updated[field] <= high:
            raise PolicyError("pipeline_config", f"{field} must be an integer between {low} and {high}", 422)
    sources = updated["source_ids"]
    if (not isinstance(sources, list) or len(sources) > 10
            or any(not isinstance(s, str) or not 1 <= len(s) <= 100 for s in sources)
            or len(set(sources)) != len(sources)):
        raise PolicyError("pipeline_config", "Provide at most ten distinct approved source identifiers", 422)
    if updated["billing_stage"] not in {"before_delivery", "after_delivery"}:
        raise PolicyError("pipeline_config", "Select explicit before_delivery or after_delivery quote terms", 422)
    settings = policy.get_settings(tx)
    if settings.get("kill_switch") and updated["enabled"]:
        raise PolicyError("kill_switch", "The emergency stop must be reviewed before enabling preparation", 423)
    updated.update(revision=int(updated["revision"]) + 1,
                   authorization_epoch=settings.get("safety_epoch", 0),
                   updated_at=tx.now, reviewed_by=policy.actor_id(actor), review_reason=reason.strip())
    # Reconfiguring does not let previously queued work inherit new authority.
    for job in tx.list("job"):
        if job.get("kind") in {"pipeline_advance", "source_research"} and job.get("status") in {"queued", "leased"}:
            job.update(status="paused", paused_reason="pipeline_config_changed", lease_token=None, lease_until=None)
            tx.put("job", job["id"], job)
    tx.put("pipeline_config", "global", updated)
    tx.audit(actor, "pipeline.configured", {"enabled": updated["enabled"], "source_ids": sources,
        "billing_stage": updated["billing_stage"], "revision": updated["revision"], "reason": reason.strip()})
    return status(tx)


def status(tx) -> dict:
    config = _config(tx)
    settings = policy.get_settings(tx)
    states = tx.list("pipeline_state")
    return {**config, "local_preparation_only": True,
            "requires_reauthorization": bool(config["enabled"] and
                config["authorization_epoch"] != settings.get("safety_epoch", 0)),
            "blocked_count": sum(s.get("status") == "BLOCKED" for s in states),
            "awaiting_review_count": sum(t.get("status") == "PENDING" and
                t.get("type") in {"pipeline_review", "follow_up", "delivery_plan"} for t in tx.list("manual_task"))}


def _task(tx, lead: dict, reason: str, instructions: str, *, kind="pipeline_review") -> dict:
    if reason == "manual_payment_approval":
        existing_review = next((t for t in tx.list("manual_task") if t.get("lead_id") == lead["id"]
            and t.get("status") == "PENDING" and t.get("title") == "Manually approve the payment request"), None)
        if existing_review:
            return existing_review
    identifier = f"pipeline-{kind}-" + hashlib.sha256((lead["id"] + ":" + reason).encode()).hexdigest()[:32]
    existing = tx.get("manual_task", identifier)
    if existing:
        return existing
    return tx.create("manual_task", {"type": kind, "status": "PENDING", "lead_id": lead["id"],
        "role": "SALES" if kind != "delivery_plan" else "DELIVERY",
        "title": {"legal_review_required": "Review the lawful contact basis and personal message",
                  "live_contact_disabled": "Review live contact readiness; sending remains disabled",
                  "delivery_review_required": "Review agreed delivery before the legacy payment gate",
                  "manual_payment_approval": "Manually approve the payment request",
                  "customer_reply_required": "Wait for a verified customer reply",
                  "customer_acceptance_required": "Review an ambiguous customer response",
                  "verified_payment_delivery_plan": "Plan the authorized customer delivery"}.get(reason, "Review the blocked sales workflow"),
        "reason": reason, "instructions": instructions, "is_test": lead.get("is_test") is True,
        "created_at": tx.now}, id=identifier)


def _draft(tx, lead: dict, *, actor: Any) -> dict:
    identifier = "contact-draft-" + lead["id"]
    draft = tx.get("pipeline_draft", identifier)
    if not draft:
        draft = tx.create("pipeline_draft", {"lead_id": lead["id"], "status": "NEEDS_REVIEW",
        "subject": f"Incoming customer enquiries at {lead['company']}",
        "body": (f"Hello {lead['company']},\n\nWe offer a scoped AI assistant for incoming customer enquiries. "
                 "It classifies enquiries and prepares reply drafts for your team's review. "
                 "An agreed test demonstration checks the fit before any customer installation. "
                 "Would reviewing that scope be useful? No sales or revenue outcome is guaranteed.\n\n"
                 "Reply unsubscribe if you do not want further contact."),
        "evidence": deepcopy(lead.get("research", {})), "untrusted_evidence": True,
        "personalization": {"company": lead["company"], "observed_need": lead.get("need", "")[:1000],
                            "source_url": lead.get("source_url", ""), "citations": deepcopy(lead.get("citations", []))},
        "template": "contact-standard-v1", "is_test": lead.get("is_test") is True,
        "requires_legal_review": True, "external_sent": False, "created_at": tx.now}, id=identifier)
    # Model recommendations remain untrusted review material, never the approved
    # outbound template or a source of policy/pricing/payment authority.
    from . import ai
    readiness = ai.status(tx)
    config = readiness.get("config", {})
    revision = config.get("revision", "unconfigured")
    if (readiness.get("effective_paid_enabled") and draft.get("status") != "UNCERTAIN"
            and draft.get("ai_revision") != revision):
        text = json.dumps({"purpose": "Prepare a truthful unsent contact draft for human legal review, using cited evidence only",
            "company": lead["company"], "untrusted_need": lead.get("need", "")[:4000],
            "untrusted_source_reference": lead.get("source_url", ""),
            "approved_service": "AI assistant for incoming enquiries: classify and prepare unsent response drafts",
            "constraints": "No outcome guarantees, contract changes, price decisions, customer acceptance or payment authority"})
        text = text[:min(12000, int(config.get("max_input_chars", 12000)))]
        ai_key = "sales-draft:" + hashlib.sha256((lead["id"] + ":" + str(revision)).encode()).hexdigest()
        response = ai.task(tx, role="sales", text=text, workflow_id=lead["id"],
                           idempotency_key=ai_key, actor=actor)
        draft.update(ai_revision=revision, ai_trace_id=response.get("trace_id"), ai_status=response.get("status"),
                     ai_recommendation=deepcopy(response.get("output")), ai_untrusted=True)
        if response.get("status") == "UNKNOWN":
            draft.update(status="UNCERTAIN", intent_id=response.get("intent_id"), manual_reconciliation_required=True)
        draft = tx.put("pipeline_draft", identifier, draft)
    return draft


def _input_fingerprint(tx, lead: dict, config: dict) -> str:
    payment = tx.get("payment", lead.get("payment_id", ""), {})
    return fingerprint({"state": lead.get("state"), "need": lead.get("need"),
        "latest_reply": lead.get("latest_reply"), "compliance": lead.get("compliance"),
        "proposal_id": lead.get("proposal_id"), "delivery_id": lead.get("delivery_id"),
        "payment_status": payment.get("status"), "revision": config["revision"]})


def _resume_verified_facts(tx, lead: dict, actor: Any) -> None:
    """Explicit operator/job execution can consume receipts retained by a stop.

    Clearing a stop never calls this helper. Scheduling still requires a fresh
    administrator authorization epoch, and each canonical hook checks evidence.
    """
    if lead.get("state") in {"CONTACTED", "NO_RESPONSE", "REPLIED", "NEGOTIATING", "PROPOSAL_SENT"}:
        pending = sorted((e for e in tx.list("inbound_event")
            if e.get("lead_id") == lead["id"] and e.get("pending_workflow_update")
            and e.get("signature_verified") is True and e.get("type", "reply") == "reply"
            and e.get("from", "").casefold() == lead.get("email", "").casefold()
            and not (lead.get("is_test") is not True and e.get("provider") == "demo")),
            key=lambda e: (e.get("created_at", 0), e["id"]))
        for event in pending[:20]:
            workflow.record_reply(tx, lead["id"], {"event_id": event["id"]}, actor=actor,
                                  key="resume-reply:" + hashlib.sha256(event["id"].encode()).hexdigest())
            event.update(pending_workflow_update=False, resumed_at=tx.now)
            tx.put("inbound_event", event["id"], event)
    lead = tx.get("lead", lead["id"])
    if lead.get("state") not in {"PAYMENT_REQUESTED", "PAYMENT_FAILED"}:
        return
    payment = tx.get("payment", lead.get("payment_id", ""), {})
    for event in tx.list("payment_event"):
        if (event.get("lead_id") != lead["id"] or event.get("payment_id") != payment.get("id")
                or event.get("signature_verified") is not True or not event.get("pending_workflow_update")
                or (lead.get("is_test") is not True and event.get("provider") == "demo")):
            continue
        key = "resume-payment:" + hashlib.sha256(event["id"].encode()).hexdigest()
        if event.get("status") == "paid" and payment.get("status") == "CONFIRMED":
            workflow.record_payment_confirmed(tx, lead["id"], event["id"], actor=actor, key=key)
        elif event.get("status") == "failed" and payment.get("status") == "FAILED":
            workflow.record_payment_failed(tx, lead["id"], event["id"], actor=actor, key=key)
        else:
            continue
        event.update(pending_workflow_update=False, resumed_at=tx.now)
        tx.put("payment_event", event["id"], event)
        break


def follow_up(tx, lead_id: str, *, actor: Any, key: str, allow_dispatch: bool = False) -> dict:
    """Deduplicate review tasks; dispatch only under separately granted gates."""
    lead = tx.get("lead", lead_id)
    if not lead:
        raise PolicyError("lead_not_found", "Lead no longer exists", 404)
    if lead.get("state") not in {"CONTACTED", "NO_RESPONSE", "REPLIED", "NEGOTIATING", "PROPOSAL_SENT"}:
        return {"skipped": True, "reason": "Lead is not eligible for a follow-up"}
    if allow_dispatch and lead.get("state") in {"CONTACTED", "NO_RESPONSE", "PROPOSAL_SENT"}:
        from . import connectors, outreach
        if policy.get_settings(tx).get("mode") == "LIVE" and outreach.status(tx).get("live_contact_authorized"):
            canonical = workflow.contact_message(lead, followup=True)
            due = str(lead.get("follow_up_at", lead.get("last_contact_at", 0)))
            dispatch_key = "follow-up-send:" + hashlib.sha256((lead_id + ":" + due).encode()).hexdigest()
            result = connectors.send_email(tx, lead_id=lead_id, **canonical, purpose="followup",
                idempotency_key=dispatch_key, actor=actor)
            if result.get("status") == "UNKNOWN":
                return {**result, "status": "UNCERTAIN", "reason": "provider_outcome_requires_review"}
            current = tx.get("lead", lead_id)
            current["follow_up_at"] = tx.now + _config(tx)["follow_up_delay_seconds"]
            tx.put("lead", lead_id, current)
            for task in tx.list("manual_task"):
                if task.get("type") == "follow_up" and task.get("lead_id") == lead_id and task.get("status") == "PENDING":
                    task.update(status="COMPLETED", completed_at=tx.now, message_id=result.get("id"))
                    tx.put("manual_task", task["id"], task)
            return result
    for task in tx.list("manual_task"):
        if task.get("type") == "follow_up" and task.get("lead_id") == lead_id and task.get("status") == "PENDING":
            return task
    anchor = str(lead.get("follow_up_at") or lead.get("last_contact_at", lead.get("created_at", 0)))
    identifier = "follow-up-" + hashlib.sha256((lead_id + ":" + anchor).encode()).hexdigest()[:32]
    existing = tx.get("manual_task", identifier)
    if existing:
        return existing
    result = tx.create("manual_task", {"type": "follow_up", "status": "PENDING", "role": "SALES",
        "lead_id": lead_id, "title": "Review a follow-up draft",
        "draft": "Follow up on the approved enquiry-assistant scope; do not promise outcomes or send without contact checks.",
        "is_test": lead.get("is_test") is True, "created_at": tx.now, "requires_compliance": True,
        "external_sent": False}, id=identifier)
    lead["follow_up_at"] = None
    tx.put("lead", lead_id, lead)
    tx.audit(actor, "pipeline.follow_up_prepared", {"lead_id": lead_id, "task_id": result["id"]})
    return result


def advance(tx, lead_id: str, *, actor: Any, key: str) -> dict:
    """Run at most eight deterministic stages, stopping at every human gate."""
    previous = tx.idempotency_get("pipeline.advance", key, {"lead_id": lead_id})
    if previous is not None:
        return previous
    settings = policy.guard(tx, "job", actor=actor)
    lead = tx.get("lead", lead_id)
    if not lead:
        raise PolicyError("lead_not_found", "Lead does not exist", 404)
    _resume_verified_facts(tx, lead, actor)
    trace: list[dict] = []
    config = _config(tx)
    outcome, reason = "WAITING", "customer_reply_required"
    for _ in range(8):
        lead = tx.get("lead", lead_id)
        state = lead.get("state")
        step_key = "pipeline:" + lead_id + ":" + str(state)
        trace.append({"state": state, "at": tx.now})
        if state in TERMINAL or state in {"PAUSED", "MANUAL_REVIEW"}:
            outcome, reason = "BLOCKED", "workflow_requires_review"
            break
        if state == "DISCOVERED":
            evidence = lead.get("need") or lead.get("research", {}).get("summary")
            if not isinstance(evidence, str) or not evidence.strip():
                outcome, reason = "BLOCKED", "source_evidence_required"
                _task(tx, lead, reason, "Import permitted, cited evidence of the customer's actual needs before qualification.")
                break
            workflow.research_lead(tx, lead_id, {"summary": evidence[:4000]}, actor=actor, key=step_key)
        elif state == "RESEARCHED":
            workflow.qualify_lead(tx, lead_id, actor=actor, key=step_key)
        elif state == "QUALIFIED":
            draft = _draft(tx, lead, actor=actor)
            if draft.get("status") == "UNCERTAIN":
                outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                _task(tx, lead, reason, "Reconcile the retained AI request and budget reservation before any retry.")
                break
            from . import outreach
            reviewer = getattr(outreach, "policy_review", None)
            review = reviewer(tx, lead) if reviewer is not None else None
            if review is not None:
                workflow.review_compliance(tx, lead_id, review, actor=actor, key=step_key + ":policy-review")
                continue
            outcome, reason = "BLOCKED", "legal_review_required"
            _task(tx, lead, reason, "A qualified reviewer must approve the source, contact channel, lawful basis, recipient type, opt-out and exact message template. This draft has not been sent.")
            break
        elif state == "COMPLIANCE_CHECK":
            workflow.prepare_contact(tx, lead_id, actor=actor, key=step_key)
        elif state in {"READY_TO_CONTACT", "PROPOSAL_READY"}:
            if settings.get("mode") != "LIVE" and lead.get("is_test") is not True:
                outcome, reason = "BLOCKED", "live_contact_disabled"
                _task(tx, lead, reason, "Real contacts cannot use simulated sending. Separate runtime, tested-provider, legal and administrator live-contact approval is required.")
                break
            from . import connectors
            message = (lead.get("contact_draft") if state == "READY_TO_CONTACT"
                       else workflow.proposal_message(tx, lead_id))
            if not message:
                outcome, reason = "BLOCKED", "contact_draft_required"
                break
            result = connectors.send_email(tx, lead_id=lead_id, subject=message["subject"], body=message["body"],
                purpose="contact" if state == "READY_TO_CONTACT" else "proposal", idempotency_key=step_key, actor=actor)
            if result.get("status") == "UNKNOWN":
                outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                _task(tx, lead, reason, "Do not resend: reconcile the durable provider intent and retained cost reservation.")
                break
            if result.get("status") == "SENT":
                for task in tx.list("manual_task"):
                    if (task.get("lead_id") == lead_id and task.get("status") == "PENDING"
                            and task.get("reason") in {"legal_review_required", "live_contact_disabled"}):
                        task.update(status="COMPLETED", completed_at=tx.now, message_id=result.get("id"))
                        tx.put("manual_task", task["id"], task)
            current = tx.get("lead", lead_id)
            current["follow_up_at"] = tx.now + config["follow_up_delay_seconds"]
            tx.put("lead", lead_id, current)
        elif state in {"CONTACTED", "NO_RESPONSE"}:
            due = lead.get("follow_up_at")
            if isinstance(due, (int, float)) and due <= tx.now:
                follow_result = follow_up(tx, lead_id, actor=actor, key=step_key + ":followup", allow_dispatch=True)
                if follow_result.get("status") == "UNCERTAIN":
                    outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                    break
            outcome, reason = "WAITING", "customer_reply_required"
            break
        elif state in {"REPLIED", "NEGOTIATING"}:
            reply = lead.get("latest_reply", {})
            event = tx.get("inbound_event", reply.get("event_id", ""))
            if not event or not event.get("signature_verified") or (lead.get("is_test") is not True and event.get("provider") == "demo"):
                outcome, reason = "BLOCKED", "verified_reply_required"
                break
            text = event.get("text", "").casefold()
            if not any(word in text for word in ("quote", "proposal", "offert", "pris", "price", "interested", "intresser")):
                outcome, reason = "BLOCKED", "customer_acceptance_required"
                _task(tx, lead, reason, "Review this verified reply and prepare a response within approved terms; no model may invent customer intent.")
                break
            workflow.prepare_proposal(tx, lead_id, {"billing_stage": config["billing_stage"]}, actor=actor, key=step_key)
        elif state == "PROPOSAL_SENT":
            reply = lead.get("latest_reply", {})
            event = tx.get("inbound_event", reply.get("event_id", ""))
            proposal = tx.get("proposal", lead.get("proposal_id", ""), {})
            expected = {f"i accept proposal {proposal.get('id')}".casefold(), f"jag accepterar offert {proposal.get('id')}".casefold()}
            if (event and event.get("signature_verified") and event.get("text", "").strip().rstrip(".! ").casefold() in expected
                    and not (lead.get("is_test") is not True and event.get("provider") == "demo")):
                workflow.accept_proposal(tx, lead_id, {"event_id": event["id"]}, actor=actor, key=step_key)
            else:
                due = lead.get("follow_up_at")
                if isinstance(due, (int, float)) and due <= tx.now:
                    follow_result = follow_up(tx, lead_id, actor=actor, key=step_key + ":followup", allow_dispatch=True)
                    if follow_result.get("status") == "UNCERTAIN":
                        outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                        break
                outcome, reason = "WAITING", "customer_acceptance_required"
                break
        elif state == "ACCEPTED":
            proposal = tx.get("proposal", lead.get("proposal_id", ""), {})
            if proposal.get("billing_stage") == "before_delivery":
                workflow.prepare_payment_gate(tx, lead_id, actor=actor, key=step_key)
            else:
                outcome, reason = "BLOCKED", "delivery_review_required"
                _task(tx, lead, reason, "The accepted legacy quote requires verified delivery before requesting payment. Keep its existing terms; arrange authorized installation and verification.")
                break
        elif state == "DELIVERY_VERIFIED":
            workflow.payment_approval_required(tx, lead_id, actor=actor, key=step_key)
        elif state == "PAYMENT_APPROVAL_REQUIRED":
            from . import connectors
            proposal = tx.get("proposal", lead.get("proposal_id", ""), {})
            payment = tx.get("payment", lead.get("payment_id", ""))
            if payment is None and proposal:
                payment = next((p for p in tx.list("payment") if p.get("proposal_id") == proposal["id"]), None)
                if payment is None:
                    payment = connectors.create_payment_draft(tx, lead_id=lead_id, proposal_id=proposal["id"],
                        amount_minor=proposal["amount_minor"], vat_minor=proposal["vat_minor"],
                        service_id=proposal["service_id"], reference="PROPOSAL-" + proposal["id"],
                        idempotency_key=step_key + ":draft", actor=actor)
            if payment and payment.get("status") in {"UNKNOWN", "DISPATCHING"}:
                outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                _task(tx, lead, reason, "Reconcile the existing payment request with its provider; do not create another request.")
                break
            if payment and payment.get("status") == "APPROVED":
                if settings.get("mode") != "LIVE" and lead.get("is_test") is not True:
                    outcome, reason = "BLOCKED", "live_payments_disabled"
                    break
                result = connectors.request_payment(tx, payment["id"], idempotency_key=step_key + ":request", actor=actor)
                if result.get("status") == "UNKNOWN":
                    outcome, reason = "UNCERTAIN", "provider_outcome_requires_review"
                    break
                continue
            outcome, reason = "BLOCKED", "manual_payment_approval"
            _task(tx, lead, reason, "An administrator must review this exact customer, quote, service, amount, VAT and reference. The agent cannot approve or send a payment request.")
            break
        elif state in {"PAYMENT_REQUESTED"}:
            outcome, reason = "WAITING", "signed_payment_confirmation_required"
            break
        elif state == "COMPLETED":
            outcome, reason = "COMPLETE", "workflow_completed"
            break
        elif state in {"PAYMENT_CONFIRMED", "DELIVERY_IN_PROGRESS"}:
            payment = tx.get("payment", lead.get("payment_id", ""), {})
            if payment.get("status") == "CONFIRMED":
                if not tx.get("manual_task", "delivery-access-" + lead_id):
                    _task(tx, lead, "verified_payment_delivery_plan", "Plan the agreed integration, customer permissions, test data, acceptance and rollback. Payment is not proof of installation or bank payout.", kind="delivery_plan")
                outcome, reason = "READY", "delivery_planning_required"
            else:
                outcome, reason = "WAITING", "delivery_or_payment_evidence_required"
            break
        else:
            outcome, reason = "BLOCKED", "workflow_requires_review"
            break
    lead = tx.get("lead", lead_id)
    result = {"lead_id": lead_id, "state": lead["state"], "status": outcome, "reason": reason,
              "steps": trace, "is_test": lead.get("is_test") is True, "updated_at": tx.now,
              "input_fingerprint": _input_fingerprint(tx, lead, config),
              "manual_payment_approval": True, "automatic_customer_installation": False}
    tx.put("pipeline_state", lead_id, result)
    tx.audit(actor, "pipeline.advanced", {"lead_id": lead_id, "status": outcome, "reason": reason, "steps": len(trace)})
    tx.idempotency_put("pipeline.advance", key, {"lead_id": lead_id}, result)
    return result


def schedule_due(tx) -> list[dict]:
    config = _config(tx)
    settings = policy.get_settings(tx)
    if (not config["enabled"] or settings.get("kill_switch")
            or config["authorization_epoch"] != settings.get("safety_epoch", 0)):
        return []
    from . import jobs
    tick = int(tx.now // config["interval_seconds"])
    revision = config["revision"]
    scheduled = []
    approved_sources = {}
    if config["source_ids"]:
        from . import research
        approved_sources = {s["id"]: s for s in research.sources(tx)}
    pending_jobs = tx.list("job")
    for source_id in config["source_ids"]:
        source = approved_sources.get(source_id, {})
        if (source.get("kind") != "https_json" or not source.get("approved")
                or not source.get("license_reviewed") or not source.get("fetch_enabled")):
            continue
        if any(item.get("source_id") == source_id and item.get("created_at", 0) >
               tx.now - source.get("minimum_interval_seconds", 3600) for item in tx.list("research_fetch")):
            continue
        if any(j.get("kind") == "source_research" and j.get("payload", {}).get("source_id") == source_id
               and j.get("status") in {"queued", "leased", "paused", "uncertain", "failed"} for j in pending_jobs):
            continue
        scheduled.append(jobs.enqueue(tx, "source_research", {"source_id": source_id, "pipeline_revision": revision},
            idempotency_key="pipeline-source:" + hashlib.sha256(f"{revision}:{source_id}:{tick}".encode()).hexdigest()))
    eligible = [lead for lead in tx.list("lead")
                if lead.get("state") not in TERMINAL | {"PAUSED", "MANUAL_REVIEW", "COMPLETED"}
                and tx.get("pipeline_state", lead["id"], {}).get("input_fingerprint") != _input_fingerprint(tx, lead, config)]
    for lead in eligible[:config["max_leads_per_tick"]]:
        # At most one unresolved pipeline job per lead, including after a stop.
        if any(j.get("kind") == "pipeline_advance" and j.get("payload", {}).get("lead_id") == lead["id"]
               and j.get("status") in {"queued", "leased", "paused", "uncertain", "failed"} for j in pending_jobs):
            continue
        scheduled.append(jobs.enqueue(tx, "pipeline_advance", {"lead_id": lead["id"], "pipeline_revision": revision},
            idempotency_key="pipeline-lead:" + hashlib.sha256(f"{revision}:{lead['id']}:{tick}".encode()).hexdigest()))
    if scheduled:
        config["last_scheduled_at"] = tx.now
        tx.put("pipeline_config", "global", config)
    return scheduled
