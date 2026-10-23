"""Attributed research, conservative CRM projections and internal sales material.

Source claims are not official registry verification. Models may rank known
records and suggest hypotheses; they never create company identities, legal
permission, contact actions, approved services or revenue.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from . import ai, policy, research, upgrade, workflow
from .policy import PolicyError
from .store import fingerprint


MAX_CANDIDATES = 50
MAX_PROMPT_CHARACTERS = 12000


def _text(value, name, maximum=4000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or "\x00" in value:
        raise PolicyError("intelligence_input", f"{name} must contain {minimum}–{maximum} characters", 422)
    return value.strip()


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError("intelligence_input", f"{name} must be an integer between {low} and {high}", 422)
    return value


def _once(tx, scope, key, payload):
    key = _text(key, "Idempotency key", 100)
    return tx.idempotency_get(scope, key, payload)


def _ready(tx):
    policy.guard(tx, "research")
    current = ai.status(tx)
    if current.get("effective_paid_enabled") is not True:
        raise PolicyError("AI_NOT_READY", "Real internal work needs administrator-approved AI, a server key, documented FX and available budget", 409)


def _sources(tx, ids):
    if not isinstance(ids, list) or not ids or len(ids) > 10 or len(set(map(str, ids))) != len(ids):
        raise PolicyError("NO_APPROVED_SOURCES", "Select one to ten explicitly approved REAL data sources", 409)
    result = []
    for source_id in ids:
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", source_id):
            raise PolicyError("intelligence_source", "Invalid approved source identifier", 422)
        try:
            source = research._approved(tx, source_id)
        except PolicyError as exc:
            raise PolicyError("NO_APPROVED_SOURCES", "A selected source is missing, unapproved, unlicensed or revoked: " + source_id, 409) from exc
        if source.get("data_mode", "REAL") != "REAL":
            raise PolicyError("REAL_SOURCE_REQUIRED", "Demonstration sources cannot produce real research results", 409)
        result.append(source)
    return result


def verified_citations(tx, lead, source_ids=None):
    """Return current approved citations, never infer legal contact clearance."""
    if upgrade.classify_record(lead) != "REAL":
        return []
    allowed = policy.get_settings(tx).get("approved_sources", [])
    result = []
    stored_citations = lead.get("citations", [])
    for citation in stored_citations if isinstance(stored_citations, list) else []:
        if not isinstance(citation, dict):
            continue
        source_id = citation.get("source_id")
        if source_ids is not None and source_id not in source_ids:
            continue
        source = tx.get("research_source", source_id or "", {})
        if (not source.get("approved") or not source.get("license_reviewed") or source_id not in allowed
                or source.get("data_mode", "REAL") != "REAL"
                or source.get("version") != citation.get("source_version")
                or not isinstance(citation.get("source_url"), str)
                or not citation["source_url"].startswith("https://")
                or not isinstance(citation.get("evidence"), str) or not citation["evidence"].strip()):
            continue
        result.append(deepcopy(citation))
    return result


def _lead(tx, lead_id):
    lead = tx.get("lead", lead_id)
    if not lead:
        raise PolicyError("lead_not_found", "The lead does not exist", 404)
    if not verified_citations(tx, lead):
        raise PolicyError("SOURCE_EVIDENCE_REQUIRED", "A REAL lead with a current approved citation is required; no identity is invented", 409)
    return lead


def _project(tx, lead):
    citations = verified_citations(tx, lead)
    crm = lead.get("crm", {})
    return {**deepcopy(lead), "website": lead.get("website", ""), "industry": lead.get("industry", ""),
        "geography": lead.get("geography", ""), "description": lead.get("description", ""),
        "record_mode": upgrade.classify_record(lead), "source_verified": bool(citations),
        "conservative_verified": bool(citations),
        "verification_method": "current_approved_source_citation" if citations else "unverified_or_stale",
        "official_registry_verified": False, "verified_citations": citations,
        "ai_score": crm.get("score"), "potential_needs": crm.get("potential_needs", []),
        "suggested_service": crm.get("suggested_service"),
        "estimated_business_value_minor": crm.get("estimated_business_value_minor"),
        "owner_agent": crm.get("owner_agent", "lead_research"),
        "next_action": crm.get("next_action", "Review the attributed evidence and permitted service fit"),
        "contact_status": "SUPPRESSED" if lead.get("state") == "UNSUBSCRIBED" else lead.get("state"),
        "contact_authorized": False}


def list_leads(tx, filters=None):
    filters = dict(filters or {})
    allowed = {"search", "record_mode", "state", "industry", "geography", "min_score", "sort", "order", "limit", "offset", "owner_agent", "verified"}
    if set(filters) - allowed:
        raise PolicyError("crm_filter", "Unknown CRM filter", 422)
    limit = _integer(filters.get("limit", 50), "limit", 1, 100)
    offset = _integer(filters.get("offset", 0), "offset", 0, 100000)
    mode = filters.get("record_mode")
    if mode not in {None, "REAL", "DEMO", "UNCLASSIFIED"}:
        raise PolicyError("crm_filter", "Choose REAL, DEMO or UNCLASSIFIED", 422)
    sort = filters.get("sort", "collected_at")
    if sort not in {"company", "ai_score", "collected_at", "quality", "state", "industry", "geography"}:
        raise PolicyError("crm_filter", "Unsupported CRM sort field", 422)
    order = filters.get("order", "desc")
    if order not in {"asc", "desc"}:
        raise PolicyError("crm_filter", "Sort order must be asc or desc", 422)
    query = _text(filters.get("search", ""), "search", 200, 0).casefold()
    min_score = filters.get("min_score")
    if min_score is not None:
        _integer(min_score, "Minimum AI score", 0, 100)
    if "verified" in filters and type(filters["verified"]) is not bool:
        raise PolicyError("crm_filter", "verified must be a boolean", 422)
    rows = []
    for lead in tx.list("lead"):
        row = _project(tx, lead)
        if mode is not None and row["record_mode"] != mode:
            continue
        if query and query not in " ".join(str(row.get(k, "")) for k in ("company", "email", "domain", "description", "need", "industry", "geography")).casefold():
            continue
        if any(name in filters and str(filters[name]).casefold() not in str(row.get(name, "")).casefold()
               for name in ("industry", "geography")):
            continue
        if any(name in filters and row.get(name) != filters[name] for name in ("state", "owner_agent")):
            continue
        if "verified" in filters and row["source_verified"] != filters["verified"]:
            continue
        if min_score is not None and (type(row["ai_score"]) is not int or row["ai_score"] < min_score):
            continue
        rows.append(row)
    def order_key(row):
        if sort == "quality":
            return int(row.get("quality", {}).get("score", 0)), row["id"]
        if sort in {"ai_score", "collected_at"}:
            return row.get(sort) if isinstance(row.get(sort), (int, float)) else -1, row["id"]
        return str(row.get(sort, "")).casefold(), row["id"]
    rows.sort(key=order_key, reverse=order == "desc")
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset,
            "source_verification_note": "Verified means current approved citations, not registry verification or contact permission"}


def _encoded(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _prompt_citations(tx, lead, source_ids=None):
    # Full evidence remains on records and reports. The provider receives a
    # bounded excerpt with the exact URL/version, never a truncated JSON object.
    return [{"source_id": item["source_id"], "source_url": item["source_url"],
             "source_version": item["source_version"], "evidence": item["evidence"][:500],
             "untrusted_input": True} for item in verified_citations(tx, lead, source_ids)[:2]]


def _call(tx, *, role, payload, workflow_id, key, actor):
    text = _encoded(payload)
    if len(text) > MAX_PROMPT_CHARACTERS:
        raise PolicyError("intelligence_prompt_limit", "The attributed input exceeds the bounded AI request; narrow the mission", 422)
    result = ai.task(tx, role=role, text=text,
                     workflow_id=workflow_id[:100], idempotency_key=key, actor=actor)
    if result.get("simulated") or result.get("provider") == "demo":
        return {**result, "output": {}}, None, "BLOCKED"
    if result.get("status") in {"UNKNOWN", "UNCERTAIN"}:
        return result, None, "UNCERTAIN"
    if result.get("status") != "COMPLETED":
        return result, None, result.get("status", "REVIEW_REQUIRED")
    try:
        content = json.loads(result["output"]["text"])
    except (ValueError, KeyError, TypeError):
        content = None
    return result, content if isinstance(content, dict) else None, "COMPLETED"


def _finish(tx, kind, record, *, scope, key, payload, actor):
    result = tx.create(kind, {"created_at": tx.now, "record_mode": "REAL", "external_sent": False,
                              "grants_contact_authority": False, "grants_payment_authority": False, **record})
    tx.audit(actor, kind + ".prepared", {"record_id": result["id"], "status": result.get("status"),
                                         "trace_id": result.get("trace_id"), "external_sent": False})
    tx.idempotency_put(scope, key, payload, result)
    return result


def run_research(tx, mission, actor, key):
    if not isinstance(mission, dict):
        raise PolicyError("research_mission", "A bounded research mission is required", 422)
    goal = _text(mission.get("goal", mission.get("brief", "")), "Research goal", 4000)
    source_ids = mission.get("source_ids", [])
    sources = _sources(tx, source_ids)
    payload = {"goal": goal, "source_ids": source_ids, "mission_id": mission.get("id"),
               "refresh_sources": mission.get("refresh_sources", False)}
    cached = _once(tx, "intelligence.research", key, payload)
    if cached is not None:
        return cached
    _ready(tx)
    fetches: list[dict] = []
    not_refreshed: list[str] = []
    if type(payload["refresh_sources"]) is not bool:
        raise PolicyError("research_mission", "refresh_sources must be a boolean", 422)
    for source in sources:
        present = any(verified_citations(tx, lead, [source["id"]]) for lead in tx.list("lead"))
        if source["kind"] == "https_json" and source.get("fetch_enabled") and (payload["refresh_sources"] or not present):
            # One fetch (10 s absolute) and one AI call per leased work unit.
            # Multiple feeds can be imported separately or on later missions.
            if fetches:
                not_refreshed.append(source["id"])
                continue
            fetched = research.fetch_source(tx, source["id"], actor,
                "mission-source:" + fingerprint({"key": key, "source": source["id"]})[:64])
            fetches.append(fetched)
            if fetched.get("status") in {"UNCERTAIN", "UNKNOWN"}:
                return _finish(tx, "research_report", {"mission_id": mission.get("id"), "goal": goal,
                    "status": "UNCERTAIN", "source_ids": source_ids, "fetches": fetches,
                    "lead_ids": [], "ranked_candidates": [], "citations": [],
                    "message": "Source outcome is uncertain; no AI result or company was fabricated"},
                    scope="intelligence.research", key=key, payload=payload, actor=actor)
    leads = [lead for lead in tx.list("lead") if verified_citations(tx, lead, source_ids)]
    leads.sort(key=lambda lead: (lead.get("quality", {}).get("score", 0), lead["id"]), reverse=True)
    available_count = len(leads)
    leads = leads[:MAX_CANDIDATES]
    if not leads:
        if fetches:
            return _finish(tx, "research_report", {"mission_id": mission.get("id"), "goal": goal,
                "status": "NO_RESEARCH_DATA", "source_ids": source_ids, "fetches": fetches,
                "lead_ids": [], "ranked_candidates": [], "citations": [],
                "coverage": {"available_records": 0, "analyzed_records": 0,
                    "not_refreshed_source_ids": not_refreshed, "max_network_fetches_per_job": 1},
                "message": "No current source-attributed companies were accepted; no company was fabricated"},
                scope="intelligence.research", key=key, payload=payload, actor=actor)
        raise PolicyError("NO_RESEARCH_DATA", "Approved sources contain no current attributed companies. Import a permitted directory or explicitly enable a reviewed feed", 409)
    # Company identities and facts are always taken from records, never model output.
    request = {"task": "Rank only the provided lead IDs for this goal. Return JSON {summary, candidates:[{lead_id,score:0..100,reason,possible_need,suggested_service,next_action}]}. Needs are hypotheses, not verified facts. Never add a company or contact.",
        "goal": goal, "candidates": []}
    analyzed = []
    for lead in leads:
        candidate = {"lead_id": lead["id"], "company": lead["company"], "industry": lead.get("industry", ""),
            "geography": lead.get("geography", ""), "citations": _prompt_citations(tx, lead, source_ids)[:1]}
        bounded_request = {**request, "candidates": [*request["candidates"], candidate]}
        if len(_encoded(bounded_request)) > MAX_PROMPT_CHARACTERS:
            break
        request = bounded_request
        analyzed.append(lead)
    leads = analyzed
    if not leads:
        return _finish(tx, "research_report", {"mission_id": mission.get("id"), "goal": goal,
            "status": "REVIEW_REQUIRED", "source_ids": source_ids, "fetches": fetches,
            "lead_ids": [], "ranked_candidates": [], "citations": [],
            "coverage": {"available_records": available_count, "analyzed_records": 0,
                "not_refreshed_source_ids": not_refreshed, "max_network_fetches_per_job": 1},
            "message": "Attributed input exceeded the bounded request. Narrow the mission; no AI call was made"},
            scope="intelligence.research", key=key, payload=payload, actor=actor)
    result, content, status = _call(tx, role="lead_research", payload=request,
        workflow_id=("mission:" + (getattr(tx, "agent_context", None) or {})["mission_id"]
            if getattr(tx, "agent_context", None) else "research:" + str(mission.get("id", "direct"))) + ":lead_research",
        key="research-ai:" + fingerprint(key)[:64], actor=actor)
    by_id = {lead["id"]: lead for lead in leads}
    ranked, issues, seen = [], [], set()
    for candidate in (content or {}).get("candidates", []) if isinstance((content or {}).get("candidates", []), list) else []:
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("lead_id"), str)
                or candidate.get("lead_id") not in by_id or candidate.get("lead_id") in seen
                or type(candidate.get("score")) is not int or not 0 <= candidate["score"] <= 100
                or not isinstance(candidate.get("reason"), str) or not 1 <= len(candidate["reason"]) <= 2000):
            issues.append("An unsupported, duplicate or unbounded model candidate was ignored")
            continue
        seen.add(candidate["lead_id"])
        lead = by_id[candidate["lead_id"]]
        projected = {"lead_id": lead["id"], "company": lead["company"], "score": candidate["score"],
            "reason": candidate["reason"], "source_verified": True, "conservative_verified": True,
            "official_registry_verified": False,
            "citations": verified_citations(tx, lead, source_ids), "assessment_is_hypothesis": True}
        for field in ("possible_need", "suggested_service", "next_action"):
            projected[field] = candidate.get(field, "")[:2000] if isinstance(candidate.get(field, ""), str) else ""
        ranked.append(projected)
        crm = {**lead.get("crm", {}), "score": projected["score"], "reason": projected["reason"],
               "potential_needs": [projected["possible_need"]] if projected["possible_need"] else [],
               "suggested_service": projected["suggested_service"], "next_action": projected["next_action"],
               "owner_agent": "lead_research", "trace_id": result.get("trace_id"), "assessed_at": tx.now}
        tx.put("lead", lead["id"], {**lead, "crm": crm})
    ranked.sort(key=lambda item: (-item["score"], item["lead_id"]))
    if status == "COMPLETED" and (not content or not ranked or issues):
        status = "REVIEW_REQUIRED"
    return _finish(tx, "research_report", {"mission_id": mission.get("id"), "goal": goal, "status": status,
        "source_ids": source_ids, "source_plan": [{"source_id": s["id"], "url": s["url"], "kind": s["kind"],
            "license_review_reference": s["legal_review_reference"]} for s in sources],
        "lead_ids": [lead["id"] for lead in leads], "ranked_candidates": ranked,
        "citations": [c for lead in leads for c in verified_citations(tx, lead, source_ids)],
        "analysis": {"text": result.get("output", {}).get("text", ""),
                     "summary": content["summary"][:4000] if isinstance((content or {}).get("summary"), str) else "",
                     "model_claims_unverified": True}, "trace_id": result.get("trace_id"),
        "ai_result_id": result.get("id"), "issues": issues, "fetches": fetches,
        "coverage": {"available_records": available_count, "analyzed_records": len(leads),
                     "not_refreshed_source_ids": not_refreshed, "max_network_fetches_per_job": 1}},
        scope="intelligence.research", key=key, payload=payload, actor=actor)


def score_lead(tx, lead_id, actor, key, *, mission_id=None):
    lead = _lead(tx, lead_id)
    payload = {"lead_id": lead_id, "mission_id": mission_id}
    cached = _once(tx, "intelligence.score", key, payload)
    if cached is not None:
        return cached
    catalogue_price = workflow.price_proposal(tx, {})
    _ready(tx)
    result, content, status = _call(tx, role="lead_research", payload={"task": "Return JSON {score:0..100,reason,potential_needs:[text],suggested_service,next_action}. Rank business fit using only supplied evidence. Suggestions are hypotheses, not facts or permissions.",
        "lead": {"lead_id": lead_id, "company": lead["company"], "evidence": lead.get("need", "")[:1000],
                 "industry": lead.get("industry", ""), "geography": lead.get("geography", "")},
        "citations": _prompt_citations(tx, lead)}, workflow_id=("mission:" + mission_id if mission_id else "crm:" + lead_id) + ":lead_research",
        key="score-ai:" + fingerprint(key)[:64], actor=actor)
    valid = (isinstance(content, dict) and type(content.get("score")) is int and 0 <= content["score"] <= 100
             and isinstance(content.get("reason"), str) and 1 <= len(content["reason"]) <= 2000
             and isinstance(content.get("potential_needs", []), list)
             and len(content.get("potential_needs", [])) <= 10
             and all(isinstance(v, str) and 1 <= len(v) <= 1000 for v in content.get("potential_needs", []))
             and isinstance(content.get("suggested_service", ""), str) and len(content.get("suggested_service", "")) <= 200
             and isinstance(content.get("next_action", ""), str) and len(content.get("next_action", "")) <= 2000)
    if valid:
        assert content is not None
        crm = {"score": content["score"], "reason": content["reason"], "potential_needs": content.get("potential_needs", []),
            "suggested_service": str(content.get("suggested_service", ""))[:200],
            "next_action": str(content.get("next_action", ""))[:2000], "owner_agent": "lead_research",
            "estimated_business_value_minor": catalogue_price["amount_minor"],
            "value_basis": "Catalogue starting price only, not contracted value or revenue",
            "trace_id": result.get("trace_id"), "assessed_at": tx.now}
        tx.put("lead", lead_id, {**lead, "crm": crm})
    return _finish(tx, "crm_assessment", {"lead_id": lead_id, "status": status if status != "COMPLETED" or valid else "REVIEW_REQUIRED",
        "assessment": {name: deepcopy(content[name]) for name in ("score", "reason", "potential_needs", "suggested_service", "next_action")
                       if name in content} if valid else None, "assessment_is_hypothesis": True,
        "trace_id": result.get("trace_id"), "ai_result_id": result.get("id"), "citations": verified_citations(tx, lead)},
        scope="intelligence.score", key=key, payload=payload, actor=actor)


def prepare_sales(tx, lead_id, actor, key, *, mission_id=None, pricing=None):
    lead = _lead(tx, lead_id)
    payload = {"lead_id": lead_id, "mission_id": mission_id, "pricing": pricing or {}}
    cached = _once(tx, "intelligence.sales", key, payload)
    if cached is not None:
        return cached
    price = workflow.price_proposal(tx, pricing or {})
    _ready(tx)
    approved = workflow.service_registry(tx)
    crm = lead.get("crm", {})
    result, content, status = _call(tx, role="sales", payload={"task": "Produce internal sales material only. Return JSON {service_id,package_name,fit_reason,email_subject,email_body,follow_up_plan:[text]}. Use only listed service IDs for an approved offer; other package ideas require registry review. No sending, customer acceptance, price changes or payment claims.",
        "lead_id": lead_id, "company": lead["company"], "evidence": lead.get("need", "")[:1000],
        "crm_assessment": {"score": crm.get("score"), "reason": str(crm.get("reason", ""))[:1000]},
        "citations": _prompt_citations(tx, lead), "approved_services": approved, "deterministic_price": price},
        workflow_id=("mission:" + mission_id if mission_id else "crm:" + lead_id) + ":sales", key="sales-ai:" + fingerprint(key)[:64], actor=actor)
    valid = (isinstance(content, dict) and all(isinstance(content.get(k), str) and 1 <= len(content[k]) <= limit
        for k, limit in (("service_id", 120), ("package_name", 200), ("fit_reason", 2000), ("email_subject", 180), ("email_body", 8000))))
    valid = bool(valid and "\r" not in content["email_subject"] and "\n" not in content["email_subject"]
        and isinstance(content.get("follow_up_plan", []), list) and len(content.get("follow_up_plan", [])) <= 10
        and all(isinstance(item, str) and 1 <= len(item) <= 1000 for item in content.get("follow_up_plan", [])))
    registered = valid and any(s["id"] == content["service_id"] and s.get("approved") is True for s in approved)
    if status == "COMPLETED" and (not valid or not registered):
        status = "REVIEW_REQUIRED"
    return _finish(tx, "sales_offer", {"lead_id": lead_id, "mission_id": mission_id, "status": "DRAFT" if status == "COMPLETED" else status,
        "price": price, "content": {name: deepcopy(content[name]) for name in ("service_id", "package_name", "fit_reason", "email_subject", "email_body", "follow_up_plan")
                                    if name in content} if valid else None, "service_registry_approved": bool(registered),
        "administrator_approval": None, "trace_id": result.get("trace_id"), "ai_result_id": result.get("id"),
        "citations": verified_citations(tx, lead), "customer_accepted": False,
        "note": "Internal proposal only; no customer contact or payment request is authorized"},
        scope="intelligence.sales", key=key, payload=payload, actor=actor)


def review_offer(tx, offer_id, approve, reason, actor):
    policy.require_admin(actor)
    if type(approve) is not bool:
        raise PolicyError("offer_review", "Approval must be an explicit boolean", 422)
    reason = _text(reason, "Review reason", 500, 12)
    offer = tx.get("sales_offer", offer_id)
    if not offer:
        raise PolicyError("offer_not_found", "Internal offer does not exist", 404)
    registered = any(service["id"] == (offer.get("content") or {}).get("service_id") and service.get("approved") is True
                     for service in workflow.service_registry(tx))
    if approve and (offer.get("status") not in {"DRAFT", "APPROVED_INTERNAL"}
                    or not offer.get("service_registry_approved") or not registered):
        raise PolicyError("offer_review", "A valid registered-service draft is required; this does not approve an unregistered service", 409)
    offer.update(status="APPROVED_INTERNAL" if approve else "REJECTED", administrator_approval={"actor_id": policy.actor_id(actor),
        "approved": approve, "at": tx.now, "reason": reason, "grants_contact_authority": False, "grants_payment_authority": False})
    tx.put("sales_offer", offer_id, offer)
    tx.audit(actor, "sales_offer.reviewed", {"offer_id": offer_id, "approved": approve, "external_action": False})
    return offer
