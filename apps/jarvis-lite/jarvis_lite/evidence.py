"""Literal, bounded web evidence; source acceptance is never business authority.

VERIFIED means an exact assertion is corroborated by distinct approved publisher
groups. It does not claim independent semantic truth, legal clearance, demand,
revenue or permission to contact anyone. Untrusted model/page text cannot add
tools, approve a policy or manufacture a source citation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import math
import re
from urllib.parse import urlsplit, urlunsplit

from . import policy as authority
from .policy import PolicyError
from .store import fingerprint


SOURCE_LIMIT = 30
CLAIM_LIMIT = 30
CLAIM_FIELDS = frozenset({"text", "subject", "predicate", "value", "unit", "kind", "citations", "importance"})
CLAIM_OPTIONAL = frozenset({"observed_at"})
CLAIM_STATUSES = frozenset({"VERIFIED", "ATTRIBUTED", "UNVERIFIED", "HYPOTHESIS", "CONFLICT", "REJECTED"})
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
    r"(?:system|developer)\s*(?:message|prompt)\s*:|"
    r"(?:reveal|print|send|exfiltrat\w*)\s+(?:the\s+)?(?:api\s*key|secret|password)|"
    r"(?:grant|change|override)\s+(?:permissions|safety\s+policy)|"
    r"ignorera\s+(?:alla\s+)?(?:tidigare|systemets)\s+instruktioner",
    re.IGNORECASE,
)
_COMMERCIAL = re.compile(
    r"\d|%|\b(?:demand|revenue|profit|growth|market\s+size|customers?|"
    r"efterfrågan|betalningsvilja|omsättning|intäkter?|lönsamhet|tillväxt|procent|andel)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset({"undersök", "analysera", "identifiera", "hitta", "skapa", "vilka", "vilken", "samt",
    "eller", "från", "med", "och", "för", "that", "this", "from", "with", "find", "research", "about"})


def _text(value, maximum, *, minimum=1):
    return isinstance(value, str) and minimum <= len(value) <= maximum and "\x00" not in value


def _stamp(value):
    if value is None:
        return None
    if type(value) in {int, float}:
        return float(value) if math.isfinite(value) else None
    if not _text(value, 80):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None


def _host(value):
    if not isinstance(value, str):
        return ""
    try:
        return value.rstrip(".").casefold().encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""


def canonical_url(value):
    """Validate provenance syntax; the network adapter separately pins DNS/TLS."""
    if not _text(value, 2000) or any(character.isspace() or ord(character) < 32 for character in value):
        raise PolicyError("evidence_url", "Källans ursprungsadress är ogiltig", 422)
    try:
        parsed = urlsplit(value)
        hostname = _host(parsed.hostname)
        if (parsed.scheme != "https" or not hostname or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 443} or "." not in hostname
                or hostname.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
            raise ValueError
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None or not re.fullmatch(r"[a-z0-9.-]+", hostname):
            raise ValueError
        return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        raise PolicyError("evidence_url", "Källhänvisningen måste ha ett offentligt granskat HTTPS-ursprung", 422) from None


def _matches(host, domains):
    return [domain for original in domains if (domain := _host(original))
            and (host == domain or host.endswith("." + domain))]


def _publisher_group(host, policy):
    # Unknown ownership is grouped conservatively. This deliberately groups
    # e.g. all *.co.uk or *.github.io together unless ownership was reviewed.
    # A page's own publisher metadata can never manufacture independence.
    reviewed = {_host(key): value for key, value in policy.get("publisher_groups", {}).items()}
    matches = sorted(_matches(host, reviewed), key=len, reverse=True) if isinstance(reviewed, dict) else []
    if matches and _text(reviewed[matches[0]], 160):
        return reviewed[matches[0]]
    return ".".join(host.split(".")[-2:])


def evaluate_source(page, policy, *, now, goal=""):
    """Evaluate a trusted retrieval result under a server-reviewed policy.

    Metadata extracted from HTML is untrusted. Only the HTTPS origin, policy
    rules and the retriever's bounded result support source acceptance. This
    function performs no networking and grants no new network permission.
    """
    if not isinstance(page, dict) or not isinstance(policy, dict) or type(now) not in {int, float} or not math.isfinite(now):
        raise PolicyError("evidence_input", "Källgranskningen behöver ett avgränsat hämtresultat och en granskad policy", 422)
    maximum_age = policy.get("max_source_age_days", 730)
    threshold = policy.get("min_reliability", 60)
    if (type(maximum_age) is not int or not 1 <= maximum_age <= 3650
            or type(threshold) is not int or not 0 <= threshold <= 100
            or any(not isinstance(policy.get(name, []), list) for name in ("allowed_domains", "prohibited_domains", "allowed_categories", "allowed_content_types"))
            or any(not isinstance(policy.get(name, {}), dict) for name in ("publisher_groups", "domain_categories"))):
        raise PolicyError("evidence_policy", "Källpolicyns aktualitets- och kvalitetsgränser är ogiltiga", 422)
    reasons, uncertainties = [], []
    raw_url = page.get("canonical_url", page.get("source_url", page.get("url", "")))
    try:
        url = canonical_url(raw_url)
    except PolicyError:
        url = ""
        reasons.append("invalid_source_origin")
    host = urlsplit(url).hostname or ""
    allowed = policy.get("allowed_domains", [])
    prohibited = policy.get("prohibited_domains", [])
    mode = policy.get("domain_access_mode", "allowlist")
    if mode not in ("allowlist", "open_public_web"):
        reasons.append("domain_access_mode_invalid")
    if mode == "allowlist" and not _matches(host, allowed):
        reasons.append("domain_outside_policy")
    if mode == "open_public_web" and policy.get("open_public_web_reviewed") is not True:
        reasons.append("open_public_web_review_required")
    from .web_retrieval import reuse_reviewed
    # Derive legal scope from server policy and every origin, never page/AI assertions.
    redirects = page.get("redirects", [])
    path_valid = isinstance(redirects, list) and all(isinstance(hop, dict) for hop in redirects)
    origins = [page.get("requested_url", raw_url), raw_url]
    if path_valid:
        origins += [hop.get(key, "") for hop in redirects for key in ("from", "to")]
    legal_reviewed = path_valid and reuse_reviewed(origins, policy)
    if not legal_reviewed:
        reasons.append("source_legal_review_required")
    if isinstance(prohibited, list) and _matches(host, prohibited):
        reasons.append("domain_prohibited")
    if policy.get("enabled") is not True:
        reasons.append("autonomous_policy_disabled")
    if policy.get("terms_reviewed") is not True:
        reasons.append("terms_review_required")
    if page.get("retrieval_status") != "SUCCESS" or page.get("robots_allowed") is not True or page.get("terms_allowed") is not True:
        reasons.append("retrieval_or_access_not_permitted")
    content_type = str(page.get("content_type", "")).split(";", 1)[0].strip().casefold()
    if content_type not in policy.get("allowed_content_types", ["text/html", "application/pdf"]):
        reasons.append("content_type_outside_policy")
    content = page.get("text", page.get("content", ""))
    if not isinstance(content, str) or not _text(content, 200000, minimum=20):
        content = ""
        reasons.append("source_text_missing_or_unbounded")
    digest = hashlib.sha256(content.encode()).hexdigest()
    if page.get("content_hash") != digest:
        reasons.append("source_content_hash_mismatch")
    if _INJECTION.search(content):
        reasons.append("prompt_injection_detected")
    retrieved = _stamp(page.get("retrieved_at"))
    if retrieved is None or retrieved > now + 60 or retrieved < now - maximum_age * 86400:
        reasons.append("invalid_or_stale_retrieval_time")
    publication = _stamp(page.get("published_at"))
    if page.get("published_at") is not None and publication is None:
        reasons.append("invalid_publication_date")
    if publication is not None and (publication > now + 60 or publication < now - maximum_age * 86400):
        reasons.append("future_or_stale_publication")
    if publication is None:
        uncertainties.append("publication_date_unknown")
    categories = {_host(key): value for key, value in policy.get("domain_categories", {}).items()}
    category_matches = sorted(_matches(host, categories), key=len, reverse=True) if isinstance(categories, dict) else []
    category = categories[category_matches[0]] if category_matches else "public_web"
    if category not in policy.get("allowed_categories", ["official", "industry", "company", "public_web"]):
        reasons.append("source_category_outside_policy")
    terms = {term.casefold() for term in re.findall(r"[^\W\d_]{3,}", goal) if term.casefold() not in _STOPWORDS}
    source_terms = {term.casefold() for term in re.findall(r"[^\W\d_]{3,}", str(page.get("title", "")) + " " + content)}
    overlap = terms & source_terms
    if terms and not overlap:
        reasons.append("source_not_relevant_to_goal")
    title = str(page.get("title", ""))[:300]
    reliability = 60 + (10 if category == "official" else 5 if category == "industry" else 0)
    reliability += (5 if title.strip() else 0) + (10 if publication is not None else 0) + (10 if overlap else 0)
    if reliability < threshold:
        reasons.append("reliability_below_policy")
    source_id = "autoweb-" + hashlib.sha256(url.encode()).hexdigest()[:20]
    version = fingerprint({"url": url, "content_hash": digest, "policy_revision": policy.get("revision")})
    return {"id": source_id, "source_id": source_id, "source_version": version,
        "canonical_url": url, "source_url": url, "requested_url": str(page.get("requested_url", raw_url))[:2000],
        "title": title, "publisher": host, "publisher_claim": str(page.get("publisher", ""))[:300],
        "publisher_group": _publisher_group(host, policy), "category": category,
        "retrieved_at": retrieved, "published_at": page.get("published_at"), "content_type": content_type,
        "content_hash": digest, "text": content, "retrieval_status": page.get("retrieval_status"),
        "status": ("REVIEW_REQUIRED" if reasons and set(reasons) <= {"source_legal_review_required", "retrieval_or_access_not_permitted"}
                   and page.get("retrieval_status") == "SUCCESS" and page.get("robots_allowed") is True
                   else "REJECTED" if reasons else "ACCEPTED"), "reliability": reliability,
        "terms_reviewed": legal_reviewed, "license_reviewed": legal_reviewed,
        "legal_review_reference": policy.get("terms_reference", "") if legal_reviewed else None,
        "reasons": list(dict.fromkeys(reasons)), "uncertainties": uncertainties,
        "policy_revision": policy.get("revision"), "untrusted_input": True,
        "max_source_age_days": maximum_age,
        "freshness": "CURRENT" if publication is not None and not any("stale" in reason or "future" in reason for reason in reasons) else "UNKNOWN",
        "authenticity_method": "retrieved_https_origin_under_admin_access_policy",
        "relevance_method": "bounded_token_overlap_not_semantic_truth",
        "rationale": "Källans ursprung och åtkomst bedöms separat från stöd för ett visst påstående. Webbplatsens egen avsändaretikett är inte verifierad identitet.",
        "semantic_correctness_verified": False, "external_contact_authorized": False, "payment_authorized": False}


def _contains_component(quote, component):
    if not component:
        return True
    return re.search(r"(?<!\w)" + re.escape(component.casefold()) + r"(?!\w)", quote.casefold()) is not None


def _claim(candidate, sources):
    candidate = deepcopy(candidate)
    if isinstance(candidate, dict):
        candidate["importance"] = {"ordinary": "general", "numerical": "numeric"}.get(str(candidate.get("importance")), candidate.get("importance"))
    reasons, citations = [], []
    if (not isinstance(candidate, dict) or set(candidate) - (CLAIM_FIELDS | CLAIM_OPTIONAL)
            or CLAIM_FIELDS - set(candidate)):
        raise PolicyError("evidence_claim_shape", "Ett påstående innehåller saknade eller otillåtna fält", 422)
    bounds = {"text": (10, 1000), "subject": (1, 200), "predicate": (1, 120), "value": (1, 1000), "unit": (0, 80)}
    if (any(not _text(candidate.get(name), high, minimum=low) for name, (low, high) in bounds.items())
            or candidate.get("kind") not in {"fact", "estimate", "hypothesis"}
            or candidate.get("importance") not in {"general", "numeric", "commercial"}
            or not isinstance(candidate.get("citations"), list) or len(candidate["citations"]) > 8
            or "observed_at" in candidate and not _text(candidate["observed_at"], 80, minimum=0)):
        raise PolicyError("evidence_claim_shape", "Ett källpåstående måste ha avgränsad text, klassificering och högst åtta hänvisningar", 422)
    if _INJECTION.search(candidate["text"]):
        reasons.append("untrusted_instruction_in_claim")
    for citation in candidate["citations"]:
        if (not isinstance(citation, dict) or set(citation) != {"source_id", "snippet"}
                or not _text(citation.get("source_id"), 160) or not _text(citation.get("snippet"), 1200, minimum=10)):
            reasons.append("invalid_claim_citation")
            continue
        source = sources.get(citation["source_id"])
        if not source or source.get("status") != "ACCEPTED":
            reasons.append("unapproved_or_unknown_claim_source")
            continue
        snippet = citation["snippet"]
        if snippet not in source.get("text", ""):
            reasons.append("fabricated_or_nonliteral_snippet")
            continue
        if (candidate["text"] not in snippet
                or any(not _contains_component(snippet, candidate[name]) for name in ("subject", "predicate", "value", "unit"))):
            reasons.append("claim_not_supported_by_literal_snippet")
            continue
        citations.append({**deepcopy(citation), **{name: source.get(name) for name in (
            "canonical_url", "source_version", "content_hash", "publisher", "publisher_group", "retrieved_at", "published_at", "freshness")}})
    unique = {fingerprint({"source_id": item["source_id"], "snippet": item["snippet"]}): item for item in citations}
    citations = list(unique.values())
    fresh = [item for item in citations if item.get("freshness") == "CURRENT"]
    groups = {item.get("publisher_group") for item in fresh if item.get("publisher_group")}
    hashes = {item.get("content_hash") for item in fresh if item.get("content_hash")}
    independent = min(len(groups), len(hashes))
    important = candidate["importance"] != "general" or bool(_COMMERCIAL.search(candidate["text"]))
    if reasons:
        status = "REJECTED"
    elif candidate["kind"] != "fact":
        status = "HYPOTHESIS"
    elif not citations:
        status = "UNVERIFIED"
        reasons.append("claim_evidence_missing")
    elif independent >= 2:
        status = "VERIFIED"
    else:
        status = "UNVERIFIED" if important else "ATTRIBUTED"
        reasons.append("independent_current_corroboration_required")
    if citations and not fresh:
        reasons.append("claim_publication_freshness_unknown")
    claim_id = "claim-" + fingerprint(candidate)[:32]
    return {**deepcopy({name: candidate[name] for name in CLAIM_FIELDS | CLAIM_OPTIONAL if name in candidate}),
        "id": claim_id, "status": status, "citations": citations,
        "source_ids": list(dict.fromkeys(item["source_id"] for item in citations)),
        "independent_publisher_count": independent, "confidence_score": 85 if status == "VERIFIED" else 40 if citations else 0,
        "reasons": list(dict.fromkeys(reasons)), "requires_corroboration": important,
        "rationale": "Ordagrant stöd kontrolleras för just detta påstående. VERIFIERAT anger samstämmiga daterade belägg från skilda granskade publicister, inte bevisad fullständig faktasanning.",
        "verification_method": "exact_literal_assertion_and_structured_components",
        "semantic_correctness_verified": False, "revenue_verified": False}


def review_claims(claims, sources, *, now=None):
    """Validate only supplied literal claims; never infer facts from prestige."""
    if not isinstance(claims, list) or len(claims) > CLAIM_LIMIT or not isinstance(sources, list) or len(sources) > SOURCE_LIMIT:
        raise PolicyError("evidence_limits", "Faktaunderlaget får innehålla högst 30 källor och 30 påståenden", 422)
    by_id: dict = {}
    for source in sources:
        if not isinstance(source, dict) or not _text(source.get("source_id", source.get("id")), 160):
            raise PolicyError("evidence_source_shape", "Källunderlaget saknar ett spårbart ID", 422)
        source_id = source.get("source_id", source.get("id"))
        if source_id in by_id and fingerprint(source) != fingerprint(by_id[source_id]):
            raise PolicyError("evidence_source_conflict", "Samma käll-ID pekar på motstridiga ögonblicksbilder", 422)
        current = deepcopy(source)
        if now is not None:
            stamp = _stamp(source.get("published_at"))
            age = source.get("max_source_age_days", 730)
            if (type(now) not in {int, float} or not math.isfinite(now)
                    or type(age) is not int or not 1 <= age <= 3650):
                raise PolicyError("evidence_source_shape", "Källans aktualitetskontroll är ogiltig", 422)
            if stamp is None or stamp > now + 60 or stamp < now - age * 86400:
                current["freshness"] = "UNKNOWN"
        by_id[source_id] = current
    reviewed = [_claim(candidate, by_id) for candidate in claims]
    groups: dict[tuple, list[dict]] = {}
    for claim in reviewed:
        if claim["kind"] == "fact" and claim["status"] not in {"REJECTED", "HYPOTHESIS"} and claim["citations"]:
            group = tuple(str(claim.get(name, "")).casefold().strip() for name in ("subject", "predicate", "unit", "observed_at"))
            groups.setdefault(group, []).append(claim)
    conflicts = []
    for rows in groups.values():
        if len({claim["value"].casefold().strip() for claim in rows}) > 1:
            for claim in rows:
                claim["status"] = "CONFLICT"
                claim["reasons"].append("conflicting_values_require_review")
                claim["confidence_score"] = 0
            conflicts.append({"subject": rows[0]["subject"], "predicate": rows[0]["predicate"],
                "claim_ids": [claim["id"] for claim in rows], "values": [claim["value"] for claim in rows]})
    return {"claims": reviewed, "conflicts": conflicts}


def build_pack(tx, mission_id, sources, claims, actor, key):
    """Save an immutable, idempotent evidence snapshot using existing storage."""
    if not isinstance(actor, dict) or actor.get("role") not in {"admin", "operator"}:
        raise PolicyError("operator_required", "En behörig operatör krävs för att spara ett faktaunderlag", 403)
    authority.guard(tx, "research", actor=actor)
    if not _text(mission_id, 160):
        raise PolicyError("evidence_mission", "Faktaunderlaget behöver ett uppdrags-ID", 422)
    reviewed = review_claims(claims, sources, now=tx.now)
    request = {"mission_id": mission_id, "sources": sources, "claims": claims}
    previous = tx.idempotency_get("evidence.pack", key, request)
    if previous:
        return tx.get("evidence_pack", previous["id"])
    gaps = [{"code": "claim_" + claim["status"].lower(), "message":
        "Hitta daterat oberoende stöd för det ordagranna påståendet eller markera det uttryckligen som en hypotes; obelagda affärsslutsatser får inte användas.",
        "claim_ids": [claim["id"]], "source_ids": claim["source_ids"]}
        for claim in reviewed["claims"] if claim["status"] != "VERIFIED"]
    verified = [claim["id"] for claim in reviewed["claims"] if claim["status"] == "VERIFIED"]
    if not verified:
        gaps.append({"code": "verified_evidence_missing", "message":
            "Underlaget innehåller inga samstämmiga aktuella fakta från oberoende publicister. Sök efter specifika primärbelägg för informationsbehovet; en allmän datakatalog visar inte marknadsefterfrågan.",
            "claim_ids": [], "source_ids": [source.get("source_id", source.get("id")) for source in sources]})
    content = {"mission_id": mission_id,
        "sources": deepcopy(sources), "claims": reviewed["claims"], "conflicts": reviewed["conflicts"],
        "verified_claim_ids": verified, "gaps": gaps,
        "status": "COMPLETE" if verified and not reviewed["conflicts"] else "INSUFFICIENT",
        "input_fingerprint": fingerprint(request), "untrusted_input": True,
        "semantic_correctness_verified": False, "external_contact_authorized": False, "payment_authorized": False}
    record = tx.create("evidence_pack", {**content, "content_fingerprint": fingerprint(content)})
    tx.idempotency_put("evidence.pack", key, request, {"id": record["id"]})
    tx.audit(actor, "research.evidence_pack_saved", {"mission_id": mission_id, "pack_id": record["id"],
        "verified_claim_ids": verified, "claim_count": len(reviewed["claims"]), "gap_count": len(gaps)})
    return record


def validate_pack(pack):
    """Verify an immutable server-created snapshot, not semantic fact truth."""
    message = "Faktaunderlaget saknar en oförändrad spårbar ögonblicksbild; nya affärspåståenden är spärrade."
    if (not isinstance(pack, dict) or not isinstance(pack.get("sources"), list)
            or not isinstance(pack.get("claims"), list)
            or pack.get("status") not in {"COMPLETE", "INSUFFICIENT"}
            or pack.get("semantic_correctness_verified") is not False
            or pack.get("external_contact_authorized") is not False or pack.get("payment_authorized") is not False
            or fingerprint({name: value for name, value in pack.items()
                if name not in {"id", "created_at", "content_fingerprint"}}) != pack.get("content_fingerprint")):
        raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
    for source in pack["sources"]:
        if (not isinstance(source, dict) or not isinstance(source.get("text"), str)
                or hashlib.sha256(source["text"].encode()).hexdigest() != source.get("content_hash")):
            raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
    if any(not isinstance(claim, dict) or claim.get("status") not in CLAIM_STATUSES for claim in pack["claims"]):
        raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
    return True


def guard_downstream(pack, claim_ids, *, statements=None, require_verified=True, allow_hypotheses=False):
    """Only exact supported assertions may be represented as business facts."""
    message = "Affärspåståendet saknar exakt aktuellt faktastöd. Länka verifierade påståenden och deras ordagranna text; hypoteser är inte verifierad efterfrågan eller intäkt."
    validate_pack(pack)
    if (not isinstance(pack, dict) or pack.get("conflicts") or not isinstance(claim_ids, list)
            or not 1 <= len(claim_ids) <= 20 or any(not _text(item, 160) for item in claim_ids)
            or len(set(claim_ids)) != len(claim_ids)):
        raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
    by_id = {claim.get("id"): claim for claim in pack.get("claims", []) if isinstance(claim, dict)}
    selected = []
    for identifier in claim_ids:
        claim = by_id.get(identifier)
        permitted = {"VERIFIED"} if require_verified else {"VERIFIED", "ATTRIBUTED"}
        if allow_hypotheses:
            permitted.add("HYPOTHESIS")
        if not claim or claim.get("status") not in permitted or not claim.get("citations") and claim.get("status") != "HYPOTHESIS":
            raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
        selected.append(claim)
    if statements is not None:
        if (not isinstance(statements, list) or len(statements) != len(claim_ids)
                or any(not isinstance(item, dict) or set(item) != {"claim_id", "text"}
                    or not _text(item.get("claim_id"), 160) or not _text(item.get("text"), 1000) for item in statements)
                or {item["claim_id"] for item in statements} != set(claim_ids)
                or any(item["text"] != by_id[item["claim_id"]]["text"] for item in statements)):
            raise PolicyError("unsupported_or_unbounded_opportunity", message, 409)
    return deepcopy(selected)
