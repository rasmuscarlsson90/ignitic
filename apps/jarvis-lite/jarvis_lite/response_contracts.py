"""Server-owned response schemas and conservative format normalization.

These contracts describe internal data, never executable tools or authority.
Only whole JSON documents are accepted: no extraction from surrounding prose,
duplicate keys, non-finite numbers, or silently discarded permission fields.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re


ACTIONS = ("draft", "classify", "summarize", "recommend", "escalate")
SPECIALISTS = ("lead_research", "sales", "delivery", "finance", "compliance")
TEXT = {"type": "string", "minLength": 1, "maxLength": 12000}
SHORT = {"type": "string", "minLength": 1, "maxLength": 2000}
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 160}
STRINGS = {"type": "array", "items": SHORT, "maxItems": 12}
IDS = {"type": "array", "items": IDENTIFIER, "maxItems": 50}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


TASK = obj({"role": {"type": "string", "enum": list(SPECIALISTS)},
            "objective": {"type": "string", "minLength": 8, "maxLength": 500}})
CANDIDATE = obj({"lead_id": IDENTIFIER, "score": {"type": "integer", "minimum": 0, "maximum": 100},
    "reason": SHORT, "possible_need": {"type": "string", "maxLength": 2000},
    "suggested_service": {"type": "string", "maxLength": 2000},
    "next_action": {"type": "string", "maxLength": 2000}}, ["lead_id", "score", "reason"])
SECTION = obj({"key": IDENTIFIER, "heading": {"type": "string", "minLength": 1, "maxLength": 100},
    "content": {"type": "string", "minLength": 20, "maxLength": 6000},
    "source_ids": {"type": "array", "items": IDENTIFIER, "minItems": 1, "maxItems": 10}})
SERVICE = obj({"title": {"type": "string", "minLength": 1, "maxLength": 200},
    "sections": {"type": "array", "items": SECTION, "minItems": 1, "maxItems": 12},
    "next_actions": {**STRINGS, "items": {"type": "string", "minLength": 1, "maxLength": 1000}, "minItems": 1},
    "limitations": {**STRINGS, "items": {"type": "string", "minLength": 1, "maxLength": 1000}, "minItems": 1},
    "company_ids": IDS})
OPPORTUNITY_STRINGS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                       "minItems": 1, "maxItems": 10}
OPPORTUNITY = obj({"title": {"type": "string", "minLength": 1, "maxLength": 200},
    "target_group": {"type": "string", "minLength": 1, "maxLength": 500}, "problem": SHORT, "solution": SHORT,
    "price_estimate_minor": {"type": "integer", "minimum": 0, "maximum": 100000000},
    "cost_estimate_minor": {"type": "integer", "minimum": 0, "maximum": 100000000},
    "competition": SHORT, "risks": OPPORTUNITY_STRINGS,
    "source_ids": {**IDS, "minItems": 1, "maxItems": 10},
    "next_steps": OPPORTUNITY_STRINGS, "assumptions": OPPORTUNITY_STRINGS})
PAYLOADS = {
    "ceo_plan": obj({"tasks": {"type": "array", "items": TASK, "minItems": 5, "maxItems": 5}}),
    "research_report": obj({"summary": {"type": "string", "minLength": 1, "maxLength": 4000},
        "candidates": {"type": "array", "items": CANDIDATE, "maxItems": 50},
        "findings": {"type": "array", "items": obj({"claim": SHORT, "source_ids": IDS,
            "kind": {"type": "string", "enum": ["fact", "estimate", "hypothesis"]}}), "maxItems": 20},
        "uncertainties": STRINGS}, ["summary", "candidates"]),
    "lead_score": obj({"score": {"type": "integer", "minimum": 0, "maximum": 100}, "reason": SHORT,
        "potential_needs": {**STRINGS, "maxItems": 10, "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
        "suggested_service": {"type": "string", "maxLength": 200},
        "next_action": SHORT}, ["score", "reason"]),
    "sales_offer": obj({"service_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "package_name": {"type": "string", "minLength": 1, "maxLength": 200}, "fit_reason": SHORT,
        "email_subject": {"type": "string", "minLength": 1, "maxLength": 180},
        "email_body": {"type": "string", "minLength": 1, "maxLength": 8000},
        "follow_up_plan": {**STRINGS, "maxItems": 10, "items": {"type": "string", "minLength": 1, "maxLength": 1000}}},
        ["service_id", "package_name", "fit_reason", "email_subject", "email_body"]),
    "sales_strategy": obj({"summary": SHORT,
        "opportunities": {"type": "array", "items": OPPORTUNITY, "minItems": 1, "maxItems": 5}}),
    "service_result": SERVICE,
    "compliance_qa": obj({"verdict": {"type": "string", "enum": ["pass", "revise", "review"]},
        "findings": STRINGS, "summary": SHORT, "source_ids": IDS}),
}
ROLE_CONTRACTS = {"ceo_plan": "ceo", "ceo_summary": "ceo", "research_report": "lead_research",
    "lead_score": "lead_research", "sales_offer": "sales", "sales_strategy": "sales",
    "service_result": "delivery", "delivery": "delivery", "finance_analysis": "finance",
    "compliance_qa": "compliance", "analysis": None}
ERRORS = {
    "ai_json_invalid": "AI-svaret är inte ett fullständigt giltigt JSON-dokument.",
    "ai_schema_invalid": "AI-svaret följer inte uppdragets strukturerade svarsschema.",
    "ai_policy_violation": "AI-svaret föreslog en otillåten handling eller innehöll otillåtna behörighetsfält.",
    "ai_output_truncated": "Modellens svar avbröts av utdatagränsen och har inte godkänts. Granska tokentaket, AI-godkännandet och budgeten före ett uttryckligt återförsök av det misslyckade steget.",
}
FORBIDDEN_KEYS = frozenset({"tools", "tool_calls", "function_call", "permissions", "grant_permissions",
    "approve_payment", "payment_authorized", "contact_authorized", "execute", "command"})


class InvalidJSON(ValueError):
    pass


class UnsafeJSON(InvalidJSON):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnsafeJSON("duplicate key")
        result[key] = value
    return result


def _constant(value):
    raise InvalidJSON("non-finite constant")


def loads(value):
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)


def validate(value, schema):
    """Validate only our small, closed JSON-schema vocabulary without new deps."""
    expected = schema["type"]
    if ((expected == "object" and not isinstance(value, dict))
            or (expected == "array" and not isinstance(value, list))
            or (expected == "string" and not isinstance(value, str))
            or (expected == "integer" and type(value) is not int)):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if expected == "object":
        if set(value) - set(schema["properties"]) or set(schema["required"]) - set(value):
            return False
        return all(validate(item, schema["properties"][key]) for key, item in value.items())
    if expected == "array":
        return schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100) and all(
            validate(item, schema["items"]) for item in value)
    if expected == "string":
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 32000) and "\x00" not in value
    return schema.get("minimum", -(10**12)) <= value <= schema.get("maximum", 10**12)


def canonical(name):
    return "service_result" if name == "delivery" else name


def contract_schema(name, *, provider=False):
    payload = PAYLOADS.get(canonical(name)) if name else None
    schema = obj({"action": {"type": "string", "enum": list(ACTIONS)},
        "text": deepcopy(payload or TEXT), "reason": deepcopy(SHORT)})
    if provider:
        # OpenAI strict structured output requires every property to be required.
        # Empty arrays/strings in optional explanatory fields represent unknowns.
        def required(node):
            # Provider metadata does not prove support for every validation
            # keyword. Keep a strict structural schema and enforce all bounds
            # locally; the full contract is also supplied in the system prompt.
            for bound in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"):
                node.pop(bound, None)
            if node.get("type") == "object":
                node["required"] = list(node["properties"])
                for child in node["properties"].values():
                    required(child)
            elif node.get("type") == "array":
                required(node["items"])
        required(schema)
    return schema


def _strip_fence(value):
    cleaned = value.strip().lstrip("\ufeff").strip()
    match = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", cleaned, flags=re.IGNORECASE)
    return match.group(1).strip() if match else cleaned


def _unsafe(value):
    if isinstance(value, list):
        return any(_unsafe(item) for item in value)
    if not isinstance(value, dict):
        return False
    if (set(value) & FORBIDDEN_KEYS or "action" in value and (not isinstance(value["action"], str)
            or value["action"] not in ACTIONS and value["action"] != "plan")):
        return True
    # Literal evidence strings are data. Structured nested tool/permission
    # objects are policy failures, never schema errors eligible for repair.
    return any(_unsafe(item) for item in value.values())


def _unsafe_raw(raw, name):
    if not isinstance(raw, str):
        return False
    keys = "|".join(re.escape(key) for key in sorted(FORBIDDEN_KEYS))
    if re.search(r'"(?:' + keys + r')"\s*:', raw):
        return True
    # Even a truncated document may contain complete escaped structural
    # identifiers. Decode those JSON string tokens before comparing policy
    # names, e.g. payment_\u0061uthorized or ex\u0065cute.
    for match in re.finditer(r'("(?:[^"\\]|\\.)*")\s*:', raw):
        try:
            key = loads(match.group(1))
        except (ValueError, TypeError, RecursionError):
            continue
        if key in FORBIDDEN_KEYS:
            return True
        if key == "action":
            value_match = re.match(r'\s*("(?:[^"\\]|\\.)*")', raw[match.end():])
            if value_match:
                try:
                    action = loads(value_match.group(1))
                except (ValueError, TypeError, RecursionError):
                    continue
                if action not in ACTIONS and not (action == "plan" and name == "ceo_plan"):
                    return True
    return False


def _normalize(value, name=None, *, raw=None, finish_reason=None):
    """Return a compatible envelope, error code and recorded harmless changes."""
    name = canonical(name)
    changes = []
    try:
        if value is None and isinstance(raw, str):
            stripped = _strip_fence(raw)
            value = loads(stripped)
            if stripped != raw:
                changes.append("whole_json_fence_or_whitespace")
        elif isinstance(value, str):
            value = loads(_strip_fence(value))
            changes.append("encoded_json_document")
    except UnsafeJSON:
        return None, "ai_policy_violation", changes
    except (ValueError, TypeError, RecursionError):
        # Fail closed on forbidden action names even in a truncated document.
        if _unsafe_raw(raw, name):
            return None, "ai_policy_violation", changes
        return None, "ai_output_truncated" if finish_reason == "length" else "ai_json_invalid", changes
    if _unsafe(value):
        return None, "ai_policy_violation", changes
    payload = PAYLOADS.get(name)
    if payload and isinstance(value, dict) and "action" not in value and validate(value, payload):
        value = {"action": "draft" if name in {"service_result", "sales_offer"} else "recommend",
                 "text": value, "reason": "Normaliserat internt svar enligt serverns granskade datakontrakt."}
        changes.append("direct_role_payload")
    if not isinstance(value, dict):
        return None, "ai_schema_invalid", changes
    value = deepcopy(value)
    if value.get("action") == "plan":
        if name != "ceo_plan":
            return None, "ai_policy_violation", changes
        value["action"] = "recommend"
        changes.append("ceo_plan_action_alias")
    if set(value) != {"action", "text", "reason"}:
        return None, "ai_schema_invalid", changes
    if payload:
        data = value["text"]
        if isinstance(data, str):
            try:
                cleaned = _strip_fence(data)
                if cleaned != data:
                    changes.append("nested_payload_json_fence_or_whitespace")
                data = loads(cleaned)
            except UnsafeJSON:
                return None, "ai_policy_violation", changes
            except (ValueError, TypeError, RecursionError):
                if _unsafe_raw(data, name):
                    return None, "ai_policy_violation", changes
                return None, "ai_output_truncated" if finish_reason == "length" else "ai_json_invalid", changes
        else:
            changes.append("role_payload_object_to_json")
        if _unsafe(data):
            return None, "ai_policy_violation", changes
        if not validate(data, payload):
            return None, "ai_output_truncated" if finish_reason == "length" else "ai_schema_invalid", changes
        if name == "ceo_plan" and {task["role"] for task in data["tasks"]} != set(SPECIALISTS):
            return None, "ai_schema_invalid", changes
        if name == "sales_offer" and ("\r" in data["email_subject"] or "\n" in data["email_subject"]):
            return None, "ai_policy_violation", changes
        value["text"] = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    text_limit = 32000 if name == "service_result" else 12000
    local_schema = contract_schema(None)
    local_schema["properties"]["text"]["maxLength"] = text_limit
    if not validate(value, local_schema):
        return None, "ai_output_truncated" if finish_reason == "length" else "ai_schema_invalid", changes
    return value, None, changes


def normalize(value, name=None, *, raw=None, finish_reason=None):
    """A length finish is incomplete even when its JSON happens to parse.

    Run the full policy validation first so forbidden fields/actions remain
    ineligible for recovery. Never let a shape error on a truncated response
    enter automatic format correction, which could invent missing information.
    """
    output, code, changes = _normalize(value, name, raw=raw, finish_reason=finish_reason)
    if finish_reason == "length" and code != "ai_policy_violation":
        return None, "ai_output_truncated", changes
    return output, code, changes
