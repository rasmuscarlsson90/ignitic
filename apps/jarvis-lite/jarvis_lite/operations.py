"""Bounded internal schedules and truthful business reporting on existing records.

Schedules only create approved internal work. Contact, checkout and arbitrary
tools are not schedule kinds. The owning job/mission retains schedule identity
so disabling or changing a schedule fences already queued/provider work.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from . import budget, policy, upgrade
from .policy import PolicyError
from .store import fingerprint


TIMEZONE = "Europe/Stockholm"
ZONE = ZoneInfo(TIMEZONE)
UPGRADE_ID = "jarvis-lite-v1.2"
UPGRADE_V13_ID = "jarvis-lite-v1.3"
KINDS = frozenset({"mission", "factory", "lead_rescore", "report"})
DELIVERABLES = frozenset({"market_analysis", "seo_analysis", "content_calendar", "marketing_strategy", "prospecting_report",
    "competitor_analysis", "business_plan", "research_report", "lead_report", "proposal_draft", "action_plan",
    "financial_analysis", "opportunity_report"})
DEFAULTS = {"enabled": False, "kind": "report", "cadence": "daily", "daily_at": "08:00",
            "interval_minutes": 1440, "max_runs_per_day": 1, "max_cost_minor": 1000, "max_cost_per_run_minor": 1000,
            "timezone": TIMEZONE, "goal": "", "source_ids": [], "lead_ids": [],
            "deliverable_kind": "market_analysis", "order_id": "", "lead_id": "", "priority": 5, "weekday": 0}
ACTIVE_JOBS = frozenset({"queued", "leased", "paused", "uncertain"})


def initialize(tx):
    """Record the additive release marker; never replace prior data or secrets."""
    if not tx.get("upgrade", UPGRADE_ID):
        tx.put("upgrade", UPGRADE_ID, {"version": "1.2", "applied_at": tx.now,
                                      "data_reset": False, "external_actions_enabled": False})
        tx.audit("upgrade", "upgrade.v12_applied", {"deleted_records": 0, "activated_capabilities": []})


def initialize_v13(tx):
    """v1.3 adds one marker, leaving every preexisting record unchanged."""
    if not tx.get("upgrade", UPGRADE_V13_ID):
        tx.put("upgrade", UPGRADE_V13_ID, {"version": "1.3", "applied_at": tx.now,
            "data_reset": False, "external_actions_enabled": False, "existing_records_rewritten": 0})
        tx.audit("upgrade", "upgrade.v13_applied", {"deleted_records": 0, "activated_capabilities": []})


def _text(value, name, maximum=500, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or "\x00" in value:
        raise PolicyError("schedule_input", f"{name} must contain {minimum}–{maximum} characters", 422)
    return value.strip()


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError("schedule_input", f"{name} must be an integer between {minimum} and {maximum}", 422)
    return value


def _ids(value, name):
    if (not isinstance(value, list) or len(value) > 20
            or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in value)
            or len(set(value)) != len(value)):
        raise PolicyError("schedule_input", f"{name} must contain at most twenty distinct record IDs", 422)
    return value


def _date(timestamp):
    return datetime.fromtimestamp(timestamp, ZONE).date()


def _day_start(timestamp):
    return datetime.fromtimestamp(timestamp, ZONE).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _next(schedule, now):
    if schedule["cadence"] == "interval":
        return now + schedule["interval_minutes"] * 60
    hour, minute = map(int, schedule["daily_at"].split(":"))
    local = datetime.fromtimestamp(now, ZONE)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule["cadence"] == "weekly":
        candidate += timedelta(days=(schedule.get("weekday", 0) - local.weekday()) % 7)
        if candidate.timestamp() <= now:
            candidate += timedelta(days=7)
        return candidate.timestamp()
    if candidate.timestamp() <= now:
        candidate = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate.timestamp()


def configure_schedule(tx, schedule_id, config, actor):
    policy.require_admin(actor)
    schedule_id = _text(schedule_id, "Schedule ID", 80)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", schedule_id):
        raise PolicyError("schedule_input", "Use a lowercase schedule identifier", 422)
    if not isinstance(config, dict) or set(config) - (set(DEFAULTS) | {"reason"}):
        raise PolicyError("schedule_input", "Unknown internal schedule fields", 422)
    reason = _text(config.get("reason"), "Review reason", minimum=12)
    old = tx.get("internal_schedule", schedule_id, {})
    value = {**deepcopy(DEFAULTS), **{key: old[key] for key in DEFAULTS if key in old}, **config}
    value.pop("reason", None)
    if type(value["enabled"]) is not bool or not isinstance(value["kind"], str) or value["kind"] not in KINDS:
        raise PolicyError("schedule_input", "Only internal mission/factory/lead_rescore/report schedules are permitted", 422)
    if value["timezone"] != TIMEZONE or not isinstance(value["cadence"], str) or value["cadence"] not in {"daily", "weekly", "interval"}:
        raise PolicyError("schedule_input", "Use a daily, weekly or interval schedule in Europe/Stockholm", 422)
    if not isinstance(value["daily_at"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value["daily_at"]):
        raise PolicyError("schedule_input", "daily_at must be HH:MM in Europe/Stockholm", 422)
    _integer(value["interval_minutes"], "interval_minutes", 15, 10080)
    _integer(value["max_runs_per_day"], "max_runs_per_day", 1, 24)
    _integer(value["max_cost_minor"], "max_cost_minor", 1, 50000)
    if "max_cost_per_run_minor" not in config:
        value["max_cost_per_run_minor"] = min(value["max_cost_per_run_minor"], value["max_cost_minor"])
    _integer(value["max_cost_per_run_minor"], "max_cost_per_run_minor", 1, value["max_cost_minor"])
    _integer(value["priority"], "priority", 1, 10)
    _integer(value["weekday"], "weekday", 0, 6)
    value["source_ids"] = _ids(value["source_ids"], "source_ids")
    value["lead_ids"] = _ids(value["lead_ids"], "lead_ids")
    if not isinstance(value["deliverable_kind"], str) or value["deliverable_kind"] not in DELIVERABLES:
        raise PolicyError("schedule_input", "Choose a supported internal delivery type", 422)
    if value["kind"] == "mission":
        value["goal"] = _text(value["goal"], "Business goal", 4000, 12)
    if value["kind"] == "factory":
        value["order_id"] = _text(value["order_id"], "Service order ID", 100)
        if not tx.get("service_order", value["order_id"]):
            raise PolicyError("schedule_order_missing", "Create the reviewed service order before scheduling it", 404)
    if value["kind"] == "lead_rescore":
        value["lead_id"] = _text(value["lead_id"], "Lead ID", 100)
        if not tx.get("lead", value["lead_id"]):
            raise PolicyError("schedule_lead_missing", "Select an existing lead", 404)
    settings = policy.get_settings(tx)
    if value["enabled"] and settings["kill_switch"]:
        raise PolicyError("kill_switch", "Review the emergency stop before enabling internal schedules", 423)
    value.update(revision=int(old.get("revision", 0)) + 1,
                 authorization_epoch=settings.get("safety_epoch", 0),
                 reviewed_by=policy.actor_id(actor), reviewed_at=tx.now,
                 next_run_at=_next(value, tx.now), review_reason=reason)
    tx.put("internal_schedule", schedule_id, value)
    from . import jobs
    for job in tx.list("job"):
        if job.get("payload", {}).get("schedule_id") == schedule_id and job.get("status") in {"queued", "leased"}:
            jobs.pause(tx, job["id"], actor=actor, reason="Internal schedule configuration changed")
    tx.audit(actor, "schedule.configured", {"schedule_id": schedule_id, "enabled": value["enabled"],
                                          "kind": value["kind"], "revision": value["revision"], "reason": reason})
    return next(item for item in schedules(tx) if item["id"] == schedule_id)


def _mission_schedule_ids(tx, schedule_id):
    return {mission["id"] for mission in tx.list("business_mission") if mission.get("schedule_id") == schedule_id}


def _spend(tx, schedule_id):
    mission_ids = _mission_schedule_ids(tx, schedule_id)
    start = _day_start(tx.now)
    amount = 0
    for entry in tx.list("budget_entry"):
        if entry.get("state") not in {"RESERVED", "SETTLED", "ACTUAL"}:
            continue
        workflow_id = str(entry.get("workflow_id", ""))
        linked = entry.get("schedule_id") == schedule_id or any(
            workflow_id == mission_id or workflow_id.startswith("mission:" + mission_id + ":")
            for mission_id in mission_ids)
        if linked and (entry["state"] == "RESERVED" or entry.get("created_at", 0) >= start):
            amount += int(entry.get("reserved_sek_minor", 0) if entry["state"] == "RESERVED" else entry.get("actual_sek_minor", 0))
    return amount


def _run_spend(tx, run_id):
    mission_ids = {mission["id"] for mission in tx.list("business_mission")
                   if mission.get("schedule_run_id") == run_id}
    return sum(int(entry.get("reserved_sek_minor", 0) if entry["state"] == "RESERVED"
                   else entry.get("actual_sek_minor", 0)) for entry in tx.list("budget_entry")
        if entry.get("state") in {"RESERVED", "SETTLED", "ACTUAL"}
        and (entry.get("schedule_run_id") == run_id or entry.get("mission_id") in mission_ids))


def schedules(tx):
    settings = policy.get_settings(tx)
    output = []
    for original in tx.list("internal_schedule"):
        item = deepcopy(original)
        runs = [run for run in tx.list("schedule_run") if run.get("schedule_id") == item["id"]]
        today = [run for run in runs if _date(run.get("created_at", 0)) == _date(tx.now)]
        item.update(requires_reauthorization=bool(item["enabled"] and (
            item.get("authorization_epoch") != settings.get("safety_epoch", 0))),
            runs_today=len(today), cost_today_minor=_spend(tx, item["id"]),
            last_run=max((run.get("created_at", 0) for run in runs), default=None),
            remaining_runs_today=max(0, item["max_runs_per_day"] - len(today)))
        history = []
        for run in sorted(runs, key=lambda row: row.get("created_at", 0), reverse=True)[:10]:
            related = [job for job in tx.list("job") if job.get("payload", {}).get("schedule_run_id") == run["id"]]
            statuses = {job.get("status") for job in related}
            state = ("UNCERTAIN" if "uncertain" in statuses else "FAILED" if "failed" in statuses
                     else "PAUSED" if "paused" in statuses else "RUNNING" if "leased" in statuses
                     else "QUEUED" if "queued" in statuses else "COMPLETED" if statuses == {"completed"}
                     else "CANCELLED" if "cancelled" in statuses else run.get("status", "UNKNOWN"))
            history.append({**run, "status": state, "jobs": len(related)})
        item["recent_runs"] = history
        output.append(item)
    return output


def validate_job(tx, job):
    context = job.get("payload", {})
    if not context.get("schedule_id"):
        tx.schedule_context = None
        return
    tx.schedule_context = {"schedule_id": context["schedule_id"],
                           "schedule_revision": context.get("schedule_revision"),
                           "schedule_run_id": context.get("schedule_run_id")}
    guard_scheduled(tx)


def guard_scheduled(tx, estimated_minor=None):
    context = getattr(tx, "schedule_context", None)
    if not context:
        return
    schedule = tx.get("internal_schedule", context["schedule_id"])
    settings = policy.get_settings(tx)
    if (not schedule or not schedule.get("enabled") or settings.get("kill_switch")
            or schedule.get("revision") != context.get("schedule_revision")
            or schedule.get("authorization_epoch") != settings.get("safety_epoch", 0)):
        raise PolicyError("schedule_authority_changed", "The internal schedule was paused or needs renewed review", 423)
    run = tx.get("schedule_run", context.get("schedule_run_id", ""))
    if not run or run.get("schedule_id") != schedule["id"] or run.get("revision") != schedule["revision"]:
        raise PolicyError("schedule_run_invalid", "This job does not belong to an authorized schedule run", 403)
    if estimated_minor is not None:
        _integer(estimated_minor, "estimated_minor", 0, 50000)
    if _spend(tx, schedule["id"]) + (estimated_minor or 0) > schedule["max_cost_minor"]:
        raise PolicyError("schedule_budget", "The internal schedule's daily cost allowance is exhausted", 409)
    if _run_spend(tx, run["id"]) + (estimated_minor or 0) > schedule.get("max_cost_per_run_minor", schedule["max_cost_minor"]):
        raise PolicyError("schedule_run_budget", "Kostnadstaket för den schemalagda körningen är förbrukat", 409)


def _attach(tx, job, schedule, run):
    job["payload"].update(schedule_id=schedule["id"], schedule_revision=schedule["revision"], schedule_run_id=run["id"])
    tx.put("job", job["id"], job)
    return job


def _queue_schedule(tx, schedule, key, run):
    from . import jobs
    actor = {"id": "schedule:" + schedule["id"], "role": "operator"}
    if schedule["kind"] == "mission":
        from . import engine
        mission = engine.create_mission(tx, {"goal": schedule["goal"], "source_ids": schedule["source_ids"],
            "lead_ids": schedule["lead_ids"], "deliverable_kind": schedule["deliverable_kind"],
            "max_cost_minor": min(schedule["max_cost_minor"] - schedule["cost_today_minor"],
                                  schedule.get("max_cost_per_run_minor", schedule["max_cost_minor"])),
            "priority": schedule["priority"]}, actor=actor, key=key,
            schedule_context={"schedule_id": schedule["id"], "schedule_revision": schedule["revision"],
                              "schedule_run_id": run["id"]})
        return [_attach(tx, job, schedule, run) for job in tx.list("job")
                if job.get("kind") == "internal_agent" and job.get("payload", {}).get("mission_id") == mission["id"]]
    kind, payload = ({"factory": ("factory_generate", {"order_id": schedule["order_id"]}),
                      "lead_rescore": ("lead_rescore", {"lead_id": schedule["lead_id"]}),
                      "report": ("operations_report", {})}[schedule["kind"]])
    return [_attach(tx, jobs.enqueue(tx, kind, payload, actor=actor, idempotency_key=key), schedule, run)]


def schedule_due(tx):
    settings = policy.get_settings(tx)
    if settings.get("kill_switch"):
        return []
    queued = []
    for schedule in schedules(tx):
        if (not schedule["enabled"] or schedule["requires_reauthorization"]
                or schedule.get("next_run_at", math.inf) > tx.now
                or schedule["runs_today"] >= schedule["max_runs_per_day"]
                or schedule["cost_today_minor"] >= schedule["max_cost_minor"]):
            continue
        if any(job.get("payload", {}).get("schedule_id") == schedule["id"]
               and job.get("payload", {}).get("schedule_revision") == schedule["revision"]
               and job.get("status") in ACTIVE_JOBS
               for job in tx.list("job")):
            continue
        due = schedule["next_run_at"]
        key = "schedule:" + fingerprint({"id": schedule["id"], "revision": schedule["revision"], "due": due})
        run_payload = {"schedule_id": schedule["id"], "revision": schedule["revision"],
                       "scheduled_for": due, "created_at": tx.now, "status": "QUEUED"}
        actor = {"id": "schedule:" + schedule["id"], "role": "operator"}
        try:
            # These operations only persist local work. A policy-blocked mission
            # must not prevent unrelated jobs/schedules from progressing.
            with tx.conn.begin_nested():
                run = tx.create("schedule_run", run_payload, id=key)
                new_jobs = _queue_schedule(tx, schedule, key, run)
            queued.extend(new_jobs)
            action = "schedule.run_queued"
        except PolicyError as error:
            run = tx.create("schedule_run", {**run_payload, "status": "BLOCKED", "error_code": error.code}, id=key)
            action = "schedule.run_blocked"
        stored = tx.get("internal_schedule", schedule["id"])
        stored.update(next_run_at=_next(stored, tx.now), last_scheduled_at=tx.now)
        tx.put("internal_schedule", schedule["id"], stored)
        tx.audit(actor, action, {"schedule_id": schedule["id"], "run_id": run["id"], "kind": schedule["kind"],
                                 "error_code": run.get("error_code")})
    return queued


def daily_report(tx, actor, key):
    payload = {"report_date": _date(tx.now).isoformat(), "timezone": TIMEZONE}
    previous = tx.idempotency_get("operations.report", key, payload)
    if previous is not None:
        return previous
    policy.guard(tx, "job", actor=actor)
    result = tx.create("business_report", {**payload, "role": "ceo", "summary": analytics(tx),
                                           "source": "persisted_records", "ai_generated": False,
                                           "created_at": tx.now})
    tx.audit(actor, "operations.report_created", {"report_id": result["id"], "ai_generated": False})
    return tx.idempotency_put("operations.report", key, payload, result)


def _real(record):
    return (record.get("simulated") is not True and record.get("is_test") is not True
            and record.get("data_mode", record.get("record_mode")) != "DEMO")


def _mission_metrics(tx, days, finance):
    missions = [mission for mission in tx.list("business_mission") if _real(mission)]
    ids = {mission["id"] for mission in missions}
    tasks = [task for task in tx.list("agent_task") if task.get("mission_id") in ids and _real(task)]
    jobs = [job for job in tx.list("job") if job.get("kind") == "internal_agent"
            and job.get("payload", {}).get("mission_id") in ids and _real(job)]
    states = {"active": {"RUNNING"}, "queued": {"QUEUED"}, "completed": {"COMPLETED"}, "paused": {"PAUSED"},
              "review_required": {"REVIEW_REQUIRED", "BLOCKED"}, "failed": {"FAILED"},
              "cancelled": {"CANCELLED"}, "uncertain": {"UNCERTAIN"}}
    counters = {name: sum(mission.get("status") in accepted for mission in missions) for name, accepted in states.items()}
    series = {day: {"date": day, "created": 0, "completed": 0} for day in days}
    for mission in missions:
        date = _date(mission.get("created_at", 0)).isoformat()
        if date in series:
            series[date]["created"] += 1
        if mission.get("status") == "COMPLETED":
            date = _date(mission.get("completed_at", mission.get("updated_at", mission.get("created_at", 0)))).isoformat()
            if date in series:
                series[date]["completed"] += 1
    costs = {row["id"]: row for row in finance["by_agent"]}
    roles = ("ceo", "lead_research", "sales", "delivery", "finance", "compliance")
    activity = []
    for role in roles:
        own = [task for task in tasks if task.get("role") == role]
        live = [job for job in jobs if job.get("payload", {}).get("role") == role
                and job.get("status") == "leased" and (job.get("lease_until") or 0) > tx.now]
        activity.append({"role": role, "active_jobs": len(live),
            "waiting_jobs": sum(job.get("status") == "queued" and job.get("payload", {}).get("role") == role for job in jobs),
            "completed_tasks": sum(task.get("status") == "COMPLETED" for task in own),
            "failed_tasks": sum(task.get("status") in {"FAILED", "UNCERTAIN"} for task in own),
            "review_required_tasks": sum(task.get("status") in {"BLOCKED", "REVIEW_REQUIRED"} for task in own),
            "booked_cost_minor": costs.get(role, {}).get("booked_cost_minor", 0),
            "reserved_cost_minor": costs.get(role, {}).get("reserved_cost_minor", 0)})
    return {"missions": counters, "active_agents": sum(row["active_jobs"] > 0 for row in activity),
            "agent_activity": activity, "mission_series": list(series.values())}


def analytics(tx):
    """Actual costs and signed real receipts, never estimated or demo revenue."""
    from . import intelligence
    now = datetime.fromtimestamp(tx.now, ZONE)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = [(start - timedelta(days=offset)).date().isoformat() for offset in range(29, -1, -1)]
    buckets: dict[str, dict[str, Any]] = {day: {"date": day, "jobs": 0, "completed_jobs": 0, "ai_calls": 0,
        "input_tokens": 0, "output_tokens": 0, "ai_cost_minor": 0, "cost_minor": 0,
        "verified_revenue_minor": 0, "net_outcome_minor": 0} for day in days}
    entries = [entry for entry in tx.list("budget_entry") if entry.get("state") in {"SETTLED", "ACTUAL"}]
    today = _day_start(tx.now)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    ai_entries = [entry for entry in entries if entry.get("category") == "ai"]
    for entry in entries:
        bucket = buckets.get(_date(entry.get("created_at", 0)).isoformat())
        if bucket is None:
            continue
        amount = int(entry.get("actual_sek_minor", 0))
        bucket["cost_minor"] += amount
        if entry.get("category") == "ai":
            bucket["ai_cost_minor"] += amount
            for field in ("input_tokens", "output_tokens"):
                bucket[field] += int(entry.get(field, 0))
            bucket["ai_calls"] += int(entry.get("calls", 0))
    real_kinds = {"internal_agent", "factory_generate", "lead_rescore", "sales_prepare", "operations_report"}
    jobs = [job for job in tx.list("job") if job.get("kind") in real_kinds and _real(job)]
    for job in jobs:
        bucket = buckets.get(_date(job.get("created_at", 0)).isoformat())
        if bucket is not None:
            bucket["jobs"] += 1
        if job.get("status") == "completed":
            bucket = buckets.get(_date(job.get("completed_at", job.get("created_at", 0))).isoformat())
            if bucket is not None:
                bucket["completed_jobs"] += 1
    leads = {lead["id"]: lead for lead in tx.list("lead")}
    verified_leads = [lead for lead in leads.values() if intelligence.verified_citations(tx, lead)]
    real_payments = [payment for payment in tx.list("payment") if payment.get("status") == "CONFIRMED"
        and upgrade.classify_record(leads.get(payment.get("lead_id"))) == "REAL" and upgrade._verified_live_payment(tx, payment)]
    for payment in real_payments:
        bucket = buckets.get(_date(payment.get("confirmed_at", payment.get("created_at", 0))).isoformat())
        if bucket is not None:
            bucket["verified_revenue_minor"] += int(payment.get("amount_minor", 0))
    for bucket in buckets.values():
        bucket["net_outcome_minor"] = bucket["verified_revenue_minor"] - bucket["cost_minor"]
    actual = sum(int(entry.get("actual_sek_minor", 0)) for entry in entries)
    revenue = sum(int(payment.get("amount_minor", 0)) for payment in real_payments)
    tasks = tx.list("agent_task")
    results = tx.list("service_result")
    financial = budget.summary(tx)
    dimensions = budget.analytics(tx)
    return {"timezone": TIMEZONE, "currency": "SEK", "verified_leads": len(verified_leads),
        "verification_definition": "REAL records with current approved licensed source citations; not an independent company-registry identity check",
        "research_tasks": sum(task.get("role") == "lead_research" for task in tasks),
        "research_reports": len(tx.list("research_report")),
        "active_jobs": sum(job.get("status") in {"queued", "leased"} for job in jobs),
        "completed_jobs": sum(job.get("status") == "completed" for job in jobs),
        "failed_jobs": sum(job.get("status") in {"failed", "uncertain"} for job in jobs),
        "paused_jobs": sum(job.get("status") == "paused" for job in jobs),
        "ai_calls": sum(int(entry.get("calls", 0)) for entry in ai_entries),
        "input_tokens": sum(int(entry.get("input_tokens", 0)) for entry in ai_entries),
        "output_tokens": sum(int(entry.get("output_tokens", 0)) for entry in ai_entries),
        "ai_cost_today_minor": sum(int(e.get("actual_sek_minor", 0)) for e in ai_entries if e.get("created_at", 0) >= today),
        "ai_cost_month_minor": sum(int(e.get("actual_sek_minor", 0)) for e in ai_entries if e.get("created_at", 0) >= month),
        "ai_cost_30d_minor": sum(int(e.get("actual_sek_minor", 0)) for e in ai_entries if e.get("created_at", 0) >= tx.now - 30 * 86400),
        "budget_remaining_minor": financial["remaining_minor"]["total"],
        "reserved_cost_minor": financial["reserved_sek_minor"], "actual_cost_minor": actual,
        "verified_revenue_minor": revenue, "net_outcome_minor": revenue - actual,
        "offers": len(tx.list("sales_offer")), "service_outputs": len(results),
        "approved_service_outputs": sum(result.get("status") == "APPROVED" for result in results),
        "pending_approvals": sum(result.get("status") == "NEEDS_REVIEW" for result in results)
            + sum(action.get("status") == "PENDING" for action in tx.list("agent_action")),
        "series": list(buckets.values()), "generated_at": tx.now, "finance": dimensions,
        **_mission_metrics(tx, days, dimensions)}
