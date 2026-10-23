"""Additive v1.1 classification, task context and conservative real reporting.

No schema/credential/policy reset belongs in this module. Legacy data without
explicit real provenance remains unclassified; changing the operation mode never
turns a simulated receipt into business revenue.
"""
from copy import deepcopy

from .policy import PolicyError, actor_id


UPGRADE_ID = "jarvis-lite-v1.1"


def classify_record(record):
    if not isinstance(record, dict):
        return "UNCLASSIFIED"
    if (record.get("is_test") is True or record.get("source_id") == "demo"
            or record.get("provider") == "demo" or record.get("record_mode") == "DEMO"
            or record.get("test_artifact") is True):
        return "DEMO"
    provenance = record.get("provenance")
    if (record.get("record_mode") == "REAL" and record.get("is_test") is False
            and isinstance(provenance, dict) and provenance.get("approved") is True
            and isinstance(provenance.get("source_id"), str) and provenance["source_id"].strip()):
        return "REAL"
    return "UNCLASSIFIED"


def _related_mode(record, leads):
    own = classify_record(record)
    if own != "UNCLASSIFIED":
        return own
    return classify_record(leads.get(record.get("lead_id") or record.get("customer_id")))


def initialize(tx):
    """Classify existing records once without deleting or replacing any IDs."""
    if tx.get("upgrade", UPGRADE_ID):
        return
    lead_count = artifact_count = 0
    for lead in tx.list("lead"):
        if "record_mode" not in lead:
            lead["record_mode"] = classify_record(lead)
            tx.put("lead", lead["id"], lead)
            lead_count += 1
    cases = {}
    for delivery in tx.list("delivery"):
        if delivery.get("mode") != "test":
            continue
        for test in delivery.get("tests", []):
            if isinstance(test, dict) and test.get("enquiry_id"):
                cases[test["enquiry_id"]] = {"lead_id": delivery.get("lead_id"),
                    "delivery_id": delivery["id"], "test_case": test.get("case"),
                    "expected_category": test.get("expected")}
    for task in tx.list("manual_task"):
        case = cases.get(task.get("enquiry_id"))
        if not case or task.get("title") != "Review enquiry response draft":
            continue
        before = task.get("status")
        task.update({**case, "test_artifact": True, "record_mode": "DEMO",
                     "context": "acceptance_test", "type": "acceptance_test_artifact"})
        if before == "PENDING":
            task.update(status="TEST_ARTIFACT", resolved_at=tx.now,
                        resolution_reason="Historical local acceptance case; no real enquiry review or send required")
        tx.put("manual_task", task["id"], task)
        enquiry = tx.get("enquiry", task["enquiry_id"])
        if enquiry:
            enquiry.update({**case, "test_artifact": True, "record_mode": "DEMO", "context": "acceptance_test"})
            tx.put("enquiry", enquiry["id"], enquiry)
        tx.audit("upgrade", "upgrade.test_task_classified", {"task_id": task["id"],
                 "enquiry_id": task["enquiry_id"], "previous_status": before, **case})
        artifact_count += 1
    tx.put("upgrade", UPGRADE_ID, {"version": "1.1", "applied_at": tx.now,
           "classified_leads": lead_count, "classified_test_tasks": artifact_count})
    tx.audit("upgrade", "upgrade.v11_applied", {"classified_leads": lead_count,
              "classified_test_tasks": artifact_count, "deleted_records": 0})


def _verified_live_payment(tx, payment):
    if payment.get("livemode") is not True or payment.get("provider") != "stripe":
        return False
    event = tx.get("payment_event", payment.get("confirmation_event_id", ""))
    return bool(event and event.get("signature_verified") is True
                and event.get("livemode") is True and event.get("provider") == "stripe"
                and event.get("payment_id") == payment.get("id")
                and event.get("lead_id") == payment.get("lead_id")
                and event.get("status") == "paid" and event.get("currency") == "SEK"
                and event.get("amount_minor") == payment.get("total_minor")
                and event.get("provider_reference") == payment.get("provider_reference"))


def metrics(tx):
    """Dashboard counters; test/legacy records cannot count as real sales."""
    leads = {lead["id"]: lead for lead in tx.list("lead")}
    messages, proposals, payments = tx.list("email_message"), tx.list("proposal"), tx.list("payment")
    real_leads = [lead for lead in leads.values() if classify_record(lead) == "REAL"]
    real_messages = [message for message in messages if _related_mode(message, leads) == "REAL"
                     and message.get("provider") != "demo"]
    real_proposals = [proposal for proposal in proposals if _related_mode(proposal, leads) == "REAL"]
    real_payments = [payment for payment in payments if _related_mode(payment, leads) == "REAL"
                     and payment.get("livemode") is True and payment.get("provider") == "stripe"]
    confirmed = [payment for payment in real_payments if payment.get("status") == "CONFIRMED"
                 and _verified_live_payment(tx, payment)]
    demo_payments = [payment for payment in payments if _related_mode(payment, leads) == "DEMO"]
    return {
        "real_leads": len(real_leads),
        "demo_leads": sum(classify_record(lead) == "DEMO" for lead in leads.values()),
        "unclassified_leads": sum(classify_record(lead) == "UNCLASSIFIED" for lead in leads.values()),
        "real_qualified": sum(lead.get("qualification", {}).get("qualified") is True for lead in real_leads),
        "real_contact_attempts": sum(message.get("purpose") in {"contact", "follow_up", "followup"} for message in real_messages),
        "real_contacts_sent": sum(message.get("purpose") in {"contact", "follow_up", "followup"}
                                  and message.get("status") in {"SENT", "DELIVERED"} for message in real_messages),
        "real_replies": sum(_related_mode(event, leads) == "REAL" and event.get("provider") != "demo"
                            and event.get("type") == "reply" and event.get("signature_verified") is True
                            for event in tx.list("inbound_event")),
        "real_proposals": len(real_proposals),
        "real_proposals_sent": sum(proposal.get("status") in {"SENT", "ACCEPTED"} for proposal in real_proposals),
        "real_accepted": sum(proposal.get("status") == "ACCEPTED" for proposal in real_proposals),
        "real_paying_customers": len({payment["lead_id"] for payment in confirmed}),
        "real_confirmed_revenue_minor": sum(payment.get("amount_minor", 0) for payment in confirmed),
        "simulated_revenue_minor": sum(payment.get("amount_minor", 0) for payment in demo_payments
                                        if payment.get("status") == "CONFIRMED"),
        "real_outstanding_minor": sum(payment.get("total_minor", 0) for payment in real_payments
                                       if payment.get("status") in {"REQUESTED", "INITIATED"}),
        "pending_drafts": sum(enquiry.get("draft_status") == "UNSENT" and not enquiry.get("test_artifact")
                               for enquiry in tx.list("enquiry")),
    }


def hydrate_tasks(tx):
    leads = {lead["id"]: lead for lead in tx.list("lead")}
    result = []
    for original in tx.list("manual_task"):
        task = deepcopy(original)
        enquiry = tx.get("enquiry", task.get("enquiry_id", "")) or {}
        lead = leads.get(task.get("lead_id") or enquiry.get("lead_id") or enquiry.get("customer_id")) or {}
        mode = _related_mode(task, leads)
        if mode == "UNCLASSIFIED":
            mode = classify_record(enquiry) if classify_record(enquiry) != "UNCLASSIFIED" else classify_record(lead)
        task.update(company=lead.get("company", ""), record_mode=mode,
                    enquiry_excerpt=str(enquiry.get("text", ""))[:160],
                    context_label="Local acceptance test" if task.get("test_artifact") or enquiry.get("test_artifact")
                    else "Customer enquiry draft" if enquiry else "Workflow review")
        if "test_case" not in task and enquiry.get("test_case") is not None:
            task["test_case"] = enquiry["test_case"]
        result.append(task)
    return result


def resolve_task(tx, task_id, reason, actor):
    if not isinstance(actor, dict) or actor.get("role") not in {"admin", "operator"}:
        raise PolicyError("forbidden", "Only operators or administrators may resolve review tasks", 403)
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        raise PolicyError("resolution_reason", "A meaningful review reason (8–500 characters) is required", 422)
    task = tx.get("manual_task", task_id)
    if not task:
        raise PolicyError("task_not_found", "Review task does not exist", 404)
    if task.get("status") != "PENDING":
        return task
    protected = task.get("role") == "FINANCE" or task.get("type") == "job_review"
    protected = protected or any(word in str(task.get("title", "")).casefold()
                                 for word in ("payment", "approve", "approval", "legal", "compliance", "resume", "reconcile"))
    if protected:
        raise PolicyError("explicit_action_required", "Use the dedicated approval or job reconciliation action for this task", 409)
    task.update(status="RESOLVED", resolved_at=tx.now, resolved_by=actor_id(actor), resolution_reason=reason.strip())
    tx.put("manual_task", task_id, task)
    tx.audit(actor, "task.manually_resolved", {"task_id": task_id, "reason": reason.strip(),
             "grants_authority": False, "changes_payment_state": False})
    return task
