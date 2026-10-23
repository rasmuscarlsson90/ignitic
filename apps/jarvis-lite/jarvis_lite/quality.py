"""Bounded deterministic checks of actual internal artifacts.

This module never claims semantic fact verification and never grants contact,
payment or publication authority. A completed check still requires the actual
Compliance role's analysis and the operator's exact-version review.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from . import policy
from .policy import PolicyError
from .store import fingerprint


MAJOR_CODES = frozenset({"unknown_company_reference", "unattributed_external_url",
    "generated_contact_details_require_review", "provider_output_requires_review",
    "invalid_deliverable_structure", "source_not_current", "source_evidence_missing",
    "unknown_provider_outcome", "missing_deliverable", "simulated_deliverable",
    "arithmetic_inconsistent", "unapproved_external_action", "result_order_mismatch", "conflicting_sources_require_review", "rejected_deliverable"})
MINOR_CODES = frozenset({"uncertainty_not_explained", "unfinished_section", "currency_not_explained"})
_EQUATION = re.compile(r"(?<![\w.])(-?\d{1,9}(?:[.,]\d{1,2})?)\s*(?:SEK|kr)?\s*([+\-*/×−])\s*(-?\d{1,9}(?:[.,]\d{1,2})?)\s*(?:SEK|kr)?\s*=\s*(-?\d{1,12}(?:[.,]\d{1,2})?)\s*(?:SEK|kr)?\b", re.IGNORECASE)


def _issue(code, severity, message, result_id=None):
    return {"code": code, "severity": severity, "message": message, "result_id": result_id}


def _arithmetic(content):
    """Check only explicitly written simple equations, never infer forecasts."""
    for match in list(_EQUATION.finditer(content))[:20]:
        left, operator, right, answer = match.groups()
        try:
            a, b, actual = (Decimal(value.replace(",", ".")) for value in (left, right, answer))
            if operator == "+":
                expected = a + b
            elif operator in {"-", "−"}:
                expected = a - b
            elif operator in {"*", "×"}:
                expected = a * b
            else:
                if not b:
                    return False
                expected = a / b
            if abs(expected - actual) > Decimal("0.01"):
                return False
        except (InvalidOperation, ZeroDivisionError):
            return False
    return True


def inspect_result(tx, result, order):
    """Return concrete checks; source text and model assertions are untrusted."""
    from . import factory
    result_id = result.get("id")
    issues = []
    if not order or result.get("order_id") != order.get("id") or result.get("kind") != order.get("kind"):
        return [_issue("result_order_mismatch", "major", "Leverabeln motsvarar inte sitt sparade uppdrag.", result_id)]
    if result.get("simulated") is not False:
        issues.append(_issue("simulated_deliverable", "major", "Demodata kan inte godkännas som verkligt uppdragsresultat.", result_id))
    if result.get("status") in {"REJECTED", "CANCELLED", "FAILED", "BLOCKED"}:
        issues.append(_issue("rejected_deliverable", "major", "Leverabeln är avvisad eller stoppad och kan inte användas för att godkänna uppdraget.", result_id))
    if result.get("status") == "UNCERTAIN" or order.get("generation_state") in {"UNCERTAIN", "ATTEMPTED"}:
        issues.append(_issue("unknown_provider_outcome", "major", "Leverantörsutfallet måste stämmas av innan resultatet kan bedömas.", result_id))
    for code in result.get("quality", {}).get("issues", []):
        if code in MINOR_CODES:
            continue  # Recompute soft checks from the actual immutable content.
        issues.append(_issue(str(code), "major", "En struktur-, käll- eller policykontroll för leverabeln underkändes.", result_id))
    try:
        factory._current_sources(tx, order)
    except PolicyError:
        issues.append(_issue("source_not_current", "major", "En källa har återkallats, ändrats eller saknar aktuell behörighet.", result_id))
    definition = factory.TYPES.get(order.get("kind"), {})
    if definition.get("evidence_required") and not order.get("evidence"):
        issues.append(_issue("source_evidence_missing", "major", "Analysen saknar aktuellt godkänt faktaunderlag.", result_id))
    content = result.get("content") or {}
    sections = content.get("sections", [])
    if (not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections)
            or {section.get("key") for section in sections} != set(definition.get("sections", []))):
        issues.append(_issue("invalid_deliverable_structure", "major", "Leverabeln saknar obligatoriska avsnitt.", result_id))
        return issues
    known_sources = {"BRIEF", *(item.get("id") for item in order.get("evidence", []))}
    if order.get("financial_snapshot"):
        known_sources.add("LEDGER")
    for section in sections:
        references = section.get("source_ids", [])
        if not isinstance(references, list) or not references or any(ref not in known_sources for ref in references):
            issues.append(_issue("source_evidence_missing", "major", "Ett avsnitt hänvisar till underlag som inte har tillhandahållits.", result_id))
            break
    text = "\n".join(str(section.get("content", "")) for section in sections)
    if not _arithmetic(text):
        issues.append(_issue("arithmetic_inconsistent", "major", "En uttrycklig beräkning stämmer inte; siffrorna kräver manuell sakgranskning.", result_id))
    if result.get("external_action") is not False:
        issues.append(_issue("unapproved_external_action", "major", "Ett internt underlag får inte ge behörighet till externa handlingar.", result_id))
    if order.get("workflow_version") == "1.3":
        limitations = content.get("limitations")
        if (not isinstance(limitations, list) or not limitations
                or all(not isinstance(value, str) or len(value.strip()) < 20
                       or value.strip().casefold() in {"no limitations", "none", "inga begränsningar", "inga"}
                       for value in limitations)):
            issues.append(_issue("uncertainty_not_explained", "minor", "Beskriv konkret vilket underlag eller vilka antaganden som fortfarande är osäkra.", result_id))
        if any(re.search(r"\b(?:todo|tbd|placeholder)\b|kommer senare|fyll i senare", str(section.get("content", "")), re.IGNORECASE) for section in sections):
            issues.append(_issue("unfinished_section", "minor", "Färdigställ markerade platshållare med befintligt underlag eller en uttrycklig begränsning.", result_id))
        if order.get("kind") == "financial_analysis" and not re.search(r"\bSEK\b|\bkronor\b", text, re.IGNORECASE):
            issues.append(_issue("currency_not_explained", "minor", "Ange valuta och skilj antaganden från bokförda kostnader.", result_id))
    unique = {}
    for issue in issues:
        unique[(issue["code"], issue["result_id"])] = issue
    return list(unique.values())


def review_mission(tx, mission, actor, key):
    if not isinstance(actor, dict) or actor.get("role") not in {"admin", "operator"}:
        raise PolicyError("quality_permission", "En behörig operatör krävs för intern kvalitetskontroll.", 403)
    policy.guard(tx, "job", actor=actor)
    mission_id = mission.get("id") if isinstance(mission, dict) else None
    if not isinstance(mission_id, str) or not mission_id:
        raise PolicyError("quality_mission", "Kvalitetskontrollen behöver ett sparat uppdrags-ID.", 422)
    orders = [item for item in tx.list("service_order") if item.get("mission_id") == mission_id]
    results = [tx.get("service_result", item.get("latest_result_id", "")) for item in orders]
    results = [item for item in results if item is not None]
    source_states = [{"id": source_id, "source": tx.get("research_source", source_id)}
        for source_id in sorted({evidence["source_id"] for order in orders for evidence in order.get("evidence", [])})]
    research_report = tx.get("research_report", mission.get("research_report_id", "")) or {}
    request = {"mission_id": mission_id, "goal": mission.get("goal"),
        "result_versions": [{"id": item["id"], "content": fingerprint(item.get("content")), "status": item.get("status")} for item in results],
        "source_states": fingerprint(source_states), "research_input": fingerprint(research_report),
        "approved_sources": policy.get_settings(tx).get("approved_sources", [])}
    cached = tx.idempotency_get("quality.mission", key, request)
    if cached is not None:
        return tx.get("quality_review", cached["id"])
    issues = []
    if not results:
        issues.append(_issue("missing_deliverable", "major", "Uppdraget har ännu ingen sparad leverabel att granska."))
    by_order = {item["id"]: item for item in orders}
    for result in results:
        issues.extend(inspect_result(tx, result, by_order.get(result["order_id"])))
    if research_report.get("conflicts"):
        issues.append(_issue("conflicting_sources_require_review", "major", "Underlagen innehåller motstridiga uppgifter som behöver mänsklig sakgranskning."))
    major = any(issue["severity"] == "major" for issue in issues)
    severity = "major" if major else "minor" if issues else "none"
    revision_allowed = bool(issues and not major and mission.get("max_revisions", 1) > 0
        and all(int(order.get("auto_revision_count", 0)) < 1 for order in orders))
    record = tx.create("quality_review", {"mission_id": mission_id, "status": "REVIEW_REQUIRED" if issues else "COMPLETED",
        "severity": severity, "revision_required": revision_allowed, "issues": issues,
        "result_ids": [item["id"] for item in results], "source_ids": [item["id"] for item in source_states],
        "result_fingerprints": {item["id"]: fingerprint(item.get("content")) for item in results},
        "checks": ["saved_output", "structure", "current_source_versions", "explicit_uncertainties", "explicit_arithmetic", "internal_authority"],
        "semantic_correctness_verified": False, "administrator_review_required": True,
        "external_contact_authorized": False, "payment_authorized": False, "provider_calls": 0,
        "goal": mission.get("goal"), "created_at": tx.now})
    tx.idempotency_put("quality.mission", key, request, {"id": record["id"]})
    tx.audit(actor, "quality.mission_reviewed", {"review_id": record["id"], "mission_id": mission_id,
        "severity": severity, "result_ids": record["result_ids"], "revision_required": revision_allowed,
        "external_authority_granted": False})
    return record
