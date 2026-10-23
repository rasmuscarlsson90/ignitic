"""Transactional SEK budget reservations and provider usage accounting.

All mutations must run inside ``Database.transaction()``. Its global lock makes
checking a ceiling and creating a reservation one atomic operation on both
PostgreSQL and SQLite. No provider is contacted by this module.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import math
import uuid

from .policy import PolicyError, get_settings, guard, require_admin


DAY = 24 * 60 * 60
MONTH = 30 * DAY
CATEGORIES = {"ai", "tools", "infrastructure"}
DEFAULT_LIMITS = {
    "total": 200_000,
    "monthly": 200_000,
    "daily": 10_000,
    "tools_30d": 50_000,
    "infrastructure_monthly": 60_000,
}
SETTINGS_KEYS = {
    "total": "total_budget_minor",
    "monthly": "monthly_budget_minor",
    "daily": "daily_budget_minor",
    "tools_30d": "rolling_tools_limit_minor",
    "infrastructure_monthly": "monthly_infrastructure_limit_minor",
}


def _amount(value, name="amount_minor"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError("invalid_amount", f"{name} must be a nonnegative integer in minor currency units", 422)
    if value > 10**12:
        raise PolicyError("invalid_amount", f"{name} exceeds the supported accounting range", 422)
    return value


def _text(value, name, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PolicyError("invalid_budget_input", f"{name} must contain 1–{maximum} characters", 422)
    return value.strip()


def _currency(value):
    if value not in {"SEK", "USD", "EUR"}:
        raise PolicyError("unsupported_currency", "Supported reporting inputs are SEK, USD and EUR", 422)
    return value


def _limits(tx):
    settings = get_settings(tx)
    result = {}
    for name, fallback in DEFAULT_LIMITS.items():
        value = settings.get(SETTINGS_KEYS[name], fallback)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyError("invalid_budget_policy", f"Invalid server budget limit: {name}", 503)
        result[name] = value
    # Preserve stored policy/history while enforcing the current owner mandate.
    result["total"] = min(result["total"], 200000)
    result["tools_30d"] = min(result["tools_30d"], 50000)
    return result


def set_fx_rate(tx, *, currency, sek_per_unit, source, observed_at, actor="system"):
    """Record an explicitly supplied rate; never assume a current FX quotation."""
    require_admin(actor)
    currency = _currency(currency)
    source = _text(source, "source", 300)
    try:
        rate = Decimal(str(sek_per_unit))
    except (InvalidOperation, ValueError):
        raise PolicyError("invalid_fx_rate", "FX rate must be a positive decimal SEK per currency unit", 422) from None
    if not rate.is_finite() or rate <= 0 or rate > 10_000:
        raise PolicyError("invalid_fx_rate", "FX rate must be positive and at most 10000 SEK per currency unit", 422)
    if currency == "SEK" and rate != 1:
        raise PolicyError("invalid_fx_rate", "SEK must always use an identity exchange rate", 422)
    if (isinstance(observed_at, bool) or not isinstance(observed_at, (int, float))
            or not math.isfinite(observed_at) or observed_at < 0 or observed_at > tx.now + 300):
        raise PolicyError("invalid_fx_timestamp", "FX observation must be a valid timestamp, not in the future", 422)
    value = {"currency": currency, "sek_per_unit": str(rate), "source": source,
             "observed_at": float(observed_at), "recorded_at": tx.now}
    tx.put("fx_rate", currency, value)
    tx.audit(actor, "budget.fx_rate.updated", value)
    return {"id": currency, **value}


def _fx(tx, currency):
    if currency == "SEK":
        return {"currency": "SEK", "sek_per_unit": "1", "source": "SEK identity conversion",
                "observed_at": tx.now}
    rate = tx.get("fx_rate", currency)
    if not rate:
        raise PolicyError("fx_rate_required", f"A documented {currency}/SEK rate is required before reserving budget", 409)
    if tx.now - rate["observed_at"] > MONTH:
        raise PolicyError("fx_rate_stale", "FX observations older than 30 days must be reviewed before new reservations", 409)
    return {key: rate[key] for key in ("currency", "sek_per_unit", "source", "observed_at")}


def _sek(amount, rate):
    # Input minor units * SEK per whole currency unit = SEK minor units. Round
    # upward so repeated fractional conversions cannot under-reserve the budget.
    return int((Decimal(amount) * Decimal(rate["sek_per_unit"])).to_integral_value(rounding=ROUND_CEILING))


def _usage(tx):
    usage = {key: 0 for key in DEFAULT_LIMITS}
    actual = 0
    reserved = 0
    tokens_in = 0
    tokens_out = 0
    calls = 0
    by_vendor: dict[str, dict[str, int]] = {}
    by_workflow: dict[str, dict[str, int]] = {}
    by_category: dict[str, dict[str, int]] = {}
    for entry in tx.list("budget_entry"):
        if entry["state"] == "RELEASED":
            continue
        pending = entry["state"] == "RESERVED"
        amount = entry["reserved_sek_minor"] if pending else entry["actual_sek_minor"]
        usage["total"] += amount
        # Unsettled liabilities never age out merely because a provider has not
        # reported the final charge yet.
        if pending or entry["created_at"] >= tx.now - MONTH:
            usage["monthly"] += amount
            if entry["category"] in {"ai", "tools"}:
                usage["tools_30d"] += amount
            if entry["category"] == "infrastructure":
                usage["infrastructure_monthly"] += amount
        if pending or entry["created_at"] >= tx.now - DAY:
            usage["daily"] += amount
        if pending:
            reserved += amount
        else:
            actual += amount
            tokens_in += entry.get("input_tokens", 0)
            tokens_out += entry.get("output_tokens", 0)
            calls += entry.get("calls", 0)
        for grouping, key in ((by_vendor, entry["vendor"]), (by_workflow, entry["workflow_id"]),
                              (by_category, entry["category"])):
            group = grouping.setdefault(key, {"actual_sek_minor": 0, "reserved_sek_minor": 0,
                                               "input_tokens": 0, "output_tokens": 0, "calls": 0})
            group["reserved_sek_minor" if pending else "actual_sek_minor"] += amount
            if not pending:
                for field in ("input_tokens", "output_tokens", "calls"):
                    group[field] += entry.get(field, 0)
    return {"usage": usage, "actual_sek_minor": actual, "reserved_sek_minor": reserved,
            "input_tokens": tokens_in, "output_tokens": tokens_out, "calls": calls,
            "by_vendor": by_vendor, "by_workflow": by_workflow, "by_category": by_category}


def _emit_alerts(tx, actor):
    limits, usage = _limits(tx), _usage(tx)["usage"]
    previous = tx.get("budget_notice", "thresholds", {})
    current = {}
    for window, limit in limits.items():
        for threshold in (50, 75, 90, 100):
            key = f"{window}:{threshold}"
            exceeded = usage[window] * 100 >= limit * threshold if limit else usage[window] > 0
            current[key] = exceeded
            if exceeded and not previous.get(key, False):
                notice = {"window": window, "threshold_percent": threshold,
                          "used_minor": usage[window], "limit_minor": limit,
                          "currency": "SEK", "created_at": tx.now,
                          "severity": "critical" if threshold == 100 else "warning"}
                tx.create("budget_alert", notice)
                tx.audit(actor, "budget.threshold", notice)
    tx.put("budget_notice", "thresholds", current)


def reserve(tx, *, vendor, workflow_id, amount_minor, currency="SEK", category="ai",
            idempotency_key, actor="system"):
    """Atomically reserve a provider cost; equal retries return the same entry."""
    vendor, workflow_id = _text(vendor, "vendor"), _text(workflow_id, "workflow_id")
    key = _text(idempotency_key, "idempotency_key")
    amount_minor = _amount(amount_minor)
    currency = _currency(currency)
    if category not in CATEGORIES:
        raise PolicyError("invalid_budget_category", "Category must be ai, tools or infrastructure", 422)
    payload = {"vendor": vendor, "workflow_id": workflow_id, "amount_minor": amount_minor,
               "currency": currency, "category": category}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    entry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jarvis-budget:{vendor}:{workflow_id}:{key}"))
    existing = tx.get("budget_entry", entry_id)
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise PolicyError("idempotency_conflict", "This reservation key already refers to different cost inputs", 409)
        if existing["state"] == "RELEASED":
            raise PolicyError("reservation_released", "Use a new action key after a definitively released reservation", 409)
        return existing
    guard(tx, "cost", actor)
    fx = _fx(tx, currency)
    sek_amount = _sek(amount_minor, fx)
    limits, usage = _limits(tx), _usage(tx)["usage"]
    targets = {"total", "monthly", "daily"}
    if category in {"ai", "tools"}:
        targets.add("tools_30d")
    if category == "infrastructure":
        targets.add("infrastructure_monthly")
    for window in sorted(targets):
        if usage[window] >= limits[window] or usage[window] + sek_amount > limits[window]:
            raise PolicyError("budget_exhausted", f"The {window} SEK budget ceiling blocks this reservation", 409)
    entry = {**payload, "fingerprint": fingerprint, "idempotency_key": key,
             "state": "RESERVED", "reserved_sek_minor": sek_amount, "fx": fx,
             "created_at": tx.now, "actual_minor": None, "actual_sek_minor": None,
             "input_tokens": 0, "output_tokens": 0, "calls": 0}
    tx.put("budget_entry", entry_id, entry)
    tx.audit(actor, "budget.reserved", {"reservation_id": entry_id, **payload, "reserved_sek_minor": sek_amount})
    _emit_alerts(tx, actor)
    return {"id": entry_id, **entry}


def settle(tx, reservation_id, *, actual_minor, currency=None, input_tokens=0,
           output_tokens=0, calls=1, actor="system"):
    """Record the actual liability even if a provider exceeded its estimate.

    An overrun is never hidden or truncated to the ceiling. The next reservation
    is blocked by the increased usage; the charge itself has already occurred.
    """
    entry = tx.get("budget_entry", reservation_id)
    if not entry:
        raise PolicyError("reservation_not_found", "Budget reservation not found", 404)
    actual_minor = _amount(actual_minor, "actual_minor")
    currency = currency or entry["currency"]
    if currency != entry["currency"]:
        raise PolicyError("settlement_currency_mismatch", "Settle in the original provider currency using its recorded FX rate", 422)
    usage = {"input_tokens": _amount(input_tokens, "input_tokens"),
             "output_tokens": _amount(output_tokens, "output_tokens"), "calls": _amount(calls, "calls")}
    if entry["state"] == "SETTLED":
        if actual_minor != entry["actual_minor"] or any(entry[field] != value for field, value in usage.items()):
            raise PolicyError("settlement_conflict", "A settled charge cannot be overwritten by a different retry", 409)
        return entry
    if entry["state"] != "RESERVED":
        raise PolicyError("reservation_released", "A released reservation cannot be charged", 409)
    actual_sek = _sek(actual_minor, entry["fx"])
    entry.update(state="SETTLED", actual_minor=actual_minor, actual_sek_minor=actual_sek,
                 settled_at=tx.now, **usage)
    tx.put("budget_entry", reservation_id, entry)
    tx.audit(actor, "budget.settled", {"reservation_id": reservation_id,
                                      "actual_minor": actual_minor, "actual_sek_minor": actual_sek,
                                      "currency": currency, **usage})
    _emit_alerts(tx, actor)
    return entry


def release(tx, reservation_id, *, reason, definitive_no_charge=False, actor="system"):
    entry = tx.get("budget_entry", reservation_id)
    if not entry:
        raise PolicyError("reservation_not_found", "Budget reservation not found", 404)
    reason = _text(reason, "reason", 300)
    if definitive_no_charge is not True:
        raise PolicyError("charge_outcome_unknown", "Keep the reservation until the provider confirms no charge occurred", 409)
    if entry["state"] == "RELEASED":
        return entry
    if entry["state"] != "RESERVED":
        raise PolicyError("charge_already_settled", "Settled liabilities cannot be erased by releasing a reservation", 409)
    entry.update(state="RELEASED", release_reason=reason, released_at=tx.now)
    tx.put("budget_entry", reservation_id, entry)
    tx.audit(actor, "budget.released", {"reservation_id": reservation_id, "reason": reason})
    _emit_alerts(tx, actor)
    return entry


def record_cost(tx, *, vendor, workflow_id, actual_minor, source, idempotency_key,
                currency="SEK", category="infrastructure", input_tokens=0,
                output_tokens=0, calls=0, actor="system"):
    """Admin reconciliation of an *already incurred* externally billed cost.

    This must never be used to authorize a new provider call. Fixed cloud
    invoices can arrive after an application shutdown or exceed a local ceiling;
    dropping them would make budget reporting misleading. The invoice key is
    unique per vendor across workflows, preventing the same invoice being
    recorded twice under a different workflow.
    """
    require_admin(actor)
    vendor, workflow_id = _text(vendor, "vendor"), _text(workflow_id, "workflow_id")
    source = _text(source, "source", 300)
    key = _text(idempotency_key, "idempotency_key")
    actual_minor, currency = _amount(actual_minor, "actual_minor"), _currency(currency)
    if category not in CATEGORIES:
        raise PolicyError("invalid_budget_category", "Category must be ai, tools or infrastructure", 422)
    usage = {"input_tokens": _amount(input_tokens, "input_tokens"),
             "output_tokens": _amount(output_tokens, "output_tokens"), "calls": _amount(calls, "calls")}
    payload = {"vendor": vendor, "workflow_id": workflow_id, "actual_minor": actual_minor,
               "currency": currency, "category": category, "source": source, **usage}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    entry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jarvis-budget-invoice:{vendor}:{key}"))
    existing = tx.get("budget_entry", entry_id)
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise PolicyError("idempotency_conflict", "This vendor invoice key already refers to a different cost", 409)
        return existing
    fx = _fx(tx, currency)
    actual_sek = _sek(actual_minor, fx)
    entry = {**payload, "fingerprint": fingerprint, "idempotency_key": key,
             "state": "SETTLED", "reserved_sek_minor": 0, "actual_sek_minor": actual_sek,
             "amount_minor": actual_minor, "fx": fx, "created_at": tx.now,
             "settled_at": tx.now, "reconciliation": True}
    tx.put("budget_entry", entry_id, entry)
    tx.audit(actor, "budget.external_cost_recorded", {"reservation_id": entry_id, **payload,
                                                     "actual_sek_minor": actual_sek})
    _emit_alerts(tx, actor)
    return {"id": entry_id, **entry}


def summary(tx):
    result, limits = _usage(tx), _limits(tx)
    result["currency"] = "SEK"
    result["limits_minor"] = limits
    result["remaining_minor"] = {key: max(0, limit - result["usage"][key]) for key, limit in limits.items()}
    result["blocked_windows"] = [key for key, limit in limits.items() if result["usage"][key] >= limit]
    result["alerts"] = sorted(tx.list("budget_alert"), key=lambda item: item["created_at"], reverse=True)[:30]
    result["fx_rates"] = tx.list("fx_rate")
    settled = [item for item in tx.list("budget_entry") if item["state"] == "SETTLED"]
    oldest = min((item["created_at"] for item in settled), default=tx.now)
    elapsed_days = max(1, (tx.now - oldest) / DAY)
    daily_burn = Decimal(result["actual_sek_minor"]) / Decimal(str(elapsed_days))
    result["forecast_daily_minor"] = int(daily_burn.to_integral_value(rounding=ROUND_CEILING))
    result["forecast_monthly_minor"] = int((daily_burn * 30).to_integral_value(rounding=ROUND_CEILING))
    remaining = result["remaining_minor"]["total"]
    result["forecast_days_remaining"] = float(Decimal(remaining) / daily_burn) if daily_burn else None
    result["window_definitions"] = {"daily": "rolling 24 hours plus all unsettled reservations",
                                    "monthly": "rolling 30 days plus all unsettled reservations",
                                    "total": "non-resetting start budget; changes require administrator review"}
    result["external_fixed_costs"] = (
        "This ledger blocks new application-controlled costs. Cloud subscriptions, storage and other fixed vendor charges "
        "can continue while Jarvis is stopped; set vendor spending limits and stop/delete billable resources manually."
    )
    return result
