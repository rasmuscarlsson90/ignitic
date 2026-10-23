"""Deterministic authority shared by every logical agent and provider adapter."""
import base64
import hashlib
import os
import secrets
from urllib.parse import urlparse

from cryptography.fernet import Fernet


class PolicyError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def actor_id(actor):
    return str(actor.get("id", "unknown")) if isinstance(actor, dict) else str(actor or "system")


def require_admin(actor):
    if not isinstance(actor, dict) or actor.get("role") != "admin":
        raise PolicyError("admin_required", "Administrator approval is required", 403)


def get_settings(tx):
    current = tx.get("settings", "global")
    if current is None:
        current = tx.put("settings", "global", {
            "mode": "DEMO", "kill_switch": False, "safety_epoch": 0,
            "minimum_list_price_minor": 500000, "minimum_discounted_price_minor": 450000,
            "max_discount_bps": 1000, "total_budget_minor": 200000,
            "monthly_budget_minor": 200000, "daily_budget_minor": 10000,
            "rolling_tools_limit_minor": 50000, "monthly_infrastructure_limit_minor": 60000,
            "budget_started_at": tx.now, "budget_horizon_days": 30,
            "approved_test_recipients": ["demo@example.com", "test@example.com", "demo-customer@example.com"],
            "demo_test_recipients": ["demo@example.com", "test@example.com", "demo-customer@example.com"],
            "approved_sources": ["manual", "demo", "customer_inbound"], "approved_senders": [],
            "email_daily_limit": 10, "email_per_lead_30d": 3, "email_min_interval_seconds": 86400,
            "live_approved": False, "legal_reviewed": False,
            "public_origin": os.getenv("JARVIS_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL", ""),
        })
    if os.getenv("KILL_SWITCH", "false").lower() == "true" and not current["kill_switch"]:
        current["kill_switch"] = True
        current["safety_epoch"] += 1
        tx.put("settings", "global", current)
        from . import jobs
        jobs.pause_for_kill(tx, actor="process-stop")
        tx.audit("process-stop", "kill_switch_latched", {"safety_epoch": current["safety_epoch"]})
    return current


def guard(tx, action, actor=None, lead=None):
    settings = get_settings(tx)
    if settings.get("mode") not in {"DEMO", "LIVE"}:
        raise PolicyError("invalid_mode", "Unknown operation mode; all agent actions are blocked", 403)
    if action in {"refund", "transfer", "payout", "trading", "advertising", "subscription"}:
        raise PolicyError("forbidden_action", "This action is outside Jarvis Lite's authority", 403)
    allowed = {"cost", "llm", "research", "contact", "proposal", "delivery", "payment_request",
               "payment_confirm", "send_email", "email", "external_write", "job"}
    if action not in allowed:
        raise PolicyError("unknown_action", "Tool action is not allowlisted", 403)
    # Verified receipts are bookkeeping, not a new external action: connectors
    # preserve those receipts directly even while autonomy is paused.
    if settings["kill_switch"]:
        raise PolicyError("kill_switch", "Emergency stop is active; explicit resume is required", 423)
    if settings["mode"] == "LIVE":
        if os.getenv("JARVIS_ALLOW_LIVE", "false").lower() != "true" or not settings.get("live_approved"):
            raise PolicyError("live_disabled", "Separate live authorization has not been enabled", 403)
        if not settings.get("legal_reviewed"):
            raise PolicyError("legal_review_required", "A reviewed legal contact policy is required", 403)
        if lead is not None and action in {"contact", "email", "send_email", "proposal", "external_write"}:
            review = lead.get("compliance", {})
            if review.get("simulation") or review.get("approval_mode") != "LIVE":
                raise PolicyError("live_compliance", "Demo compliance evidence cannot authorize live actions", 403)
    return settings


def set_kill_switch(tx, enabled, reason, actor):
    require_admin(actor)
    if not enabled and os.getenv("KILL_SWITCH", "false").lower() == "true":
        raise PolicyError("process_stop", "The process-level KILL_SWITCH must be cleared before administrator recovery", 423)
    if not reason.strip():
        raise PolicyError("reason_required", "A reason is required", 422)
    settings = get_settings(tx)
    if settings["kill_switch"] != enabled:
        settings["kill_switch"] = enabled
        settings["safety_epoch"] += 1
        tx.put("settings", "global", settings)
    if enabled:
        from . import jobs
        jobs.pause_for_kill(tx, actor=actor)
    tx.audit(actor, "kill_switch_changed", {"enabled": enabled, "reason": reason, "safety_epoch": settings["safety_epoch"]})
    return settings


def update_settings(tx, changes, actor):
    require_admin(actor)
    changes = dict(changes)
    reason = changes.pop("reason", "")
    allowed = {"minimum_list_price_minor", "minimum_discounted_price_minor", "max_discount_bps",
               "total_budget_minor", "monthly_budget_minor", "daily_budget_minor", "rolling_tools_limit_minor",
               "monthly_infrastructure_limit_minor", "approved_senders", "approved_test_recipients",
               "email_daily_limit", "email_per_lead_30d", "email_min_interval_seconds", "approved_sources", "public_origin"}
    if set(changes) - allowed:
        raise PolicyError("protected_settings", "Use explicit authorization endpoints for safety settings", 422)
    settings = get_settings(tx)
    updated = {**settings, **changes}
    if "public_origin" in changes:
        if not isinstance(changes["public_origin"], str):
            raise PolicyError("public_origin", "An approved HTTPS origin is required", 422)
        parsed = urlparse(changes["public_origin"])
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise PolicyError("public_origin", "Use an HTTPS origin without credentials, query or path", 422)
    for name in allowed:
        if name.endswith(("_minor", "_bps", "_limit", "_30d", "_seconds")) and name in updated:
            if type(updated[name]) is not int or updated[name] < 0:
                raise PolicyError("invalid_setting", "Limits must be nonnegative integer amounts", 422)
    price_keys = {"minimum_list_price_minor", "minimum_discounted_price_minor", "max_discount_bps"}
    if price_keys.intersection(changes) and (not isinstance(reason, str) or len(reason.strip()) < 8):
        raise PolicyError("pricing_review", "Changing the pricing mandate needs an explicit administrator reason", 422)
    if (updated["max_discount_bps"] > 10000 or updated["minimum_list_price_minor"] <= 0
            or updated["minimum_discounted_price_minor"] <= 0
            or updated["minimum_discounted_price_minor"] > updated["minimum_list_price_minor"]):
        raise PolicyError("pricing_mandate", "Pricing floors and discounts must describe a valid administrator mandate", 422)
    updated["safety_epoch"] += 1
    tx.put("settings", "global", updated)
    from . import jobs
    jobs.pause_for_kill(tx, actor=actor)
    tx.audit(actor, "policy_updated", {"changes": changes, "previous": {k: settings.get(k) for k in changes}, "reason": reason})
    return updated


def _cipher():
    key = os.getenv("JARVIS_ENCRYPTION_KEY")
    if key:
        return Fernet(key.encode())
    if os.getenv("JARVIS_ENV") == "production":
        raise RuntimeError("A stable JARVIS_ENCRYPTION_KEY is required in production")
    seed = os.getenv("JARVIS_SESSION_SECRET", "isolated-tests-not-for-production")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()))


def set_secret(tx, name, value, actor):
    require_admin(actor)
    if not name.isupper() or len(name) > 80:
        raise PolicyError("secret_name", "Use an uppercase provider secret name", 422)
    tx.put("secret", name, {"ciphertext": _cipher().encrypt(value.encode()).decode()})
    tx.audit(actor, "secret_updated", {"name": name})


def get_secret(tx, name):
    item = tx.get("secret", name)
    return _cipher().decrypt(item["ciphertext"].encode()).decode() if item else None


def initialize(tx):
    get_settings(tx)
    if not tx.get("secret", "DEMO_WEBHOOK_SECRET"):
        set_secret(tx, "DEMO_WEBHOOK_SECRET", secrets.token_urlsafe(48), {"id": "bootstrap", "role": "admin"})
