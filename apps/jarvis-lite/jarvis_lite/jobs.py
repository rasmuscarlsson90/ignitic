"""Durable, bounded logical-agent jobs backed by the same transactional store.

No queue handler is an arbitrary tool runner. Fixed preparation/research handlers
and the sandbox journey reuse the canonical workflows and provider adapters.
Provider effects must pass the current central policy immediately before
execution. Leases are committed before handling a job and fenced by a random
token plus the persistent safety epoch.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from threading import Event
from typing import Any
from zoneinfo import ZoneInfo

from . import policy
from .policy import PolicyError


KINDS = frozenset({"demo_journey", "research", "qualify", "follow_up", "daily_report",
                   "pipeline_advance", "source_research"})
ACTIVE = frozenset({"queued", "leased"})
MAX_ATTEMPTS = 3
LEASE_SECONDS = 90
SCHEDULE_TIMEZONE = "Europe/Stockholm"


def _identity(actor: Any) -> str:
    return policy.actor_id(actor)


def _epoch(settings: dict) -> int:
    return int(settings.get("safety_epoch", 0))


def _save(tx, job: dict) -> dict:
    job["updated_at"] = tx.now
    return tx.put("job", job["id"], job)


def _job(tx, job_id: str) -> dict:
    job = tx.get("job", job_id)
    if not job:
        raise PolicyError("job_not_found", "Job does not exist", status=404)
    return job


def _task(tx, job: dict, reason: str) -> None:
    task_id = "job-review-" + job["id"]
    if tx.get("manual_task", task_id):
        return
    tx.create("manual_task", {
        "type": "job_review", "status": "PENDING", "job_id": job["id"],
        "reason": reason, "title": "Review an interrupted agent job",
        "instructions": "Check the provider/result before explicitly reconciling or resuming this job.",
        "created_at": tx.now,
    }, id=task_id)


def enqueue(tx, kind: str, payload: dict | None = None, *, idempotency_key: str,
            actor: Any = "system", run_at: float | None = None) -> dict:
    if kind not in KINDS:
        raise PolicyError("unknown_job_kind", "Only approved logical agent jobs may be queued", status=422)
    if not isinstance(payload or {}, dict):
        raise PolicyError("invalid_job_payload", "Job payload must be an object", status=422)
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        raise PolicyError("invalid_idempotency_key", "A 1–128 character idempotency key is required", status=422)
    try:
        encoded = json.dumps(payload or {}, allow_nan=False)
        due = float(run_at) if run_at is not None else tx.now
        if due != due or abs(due) == float("inf") or len(encoded) > 32_768:
            raise ValueError
    except (TypeError, ValueError):
        raise PolicyError("invalid_job_payload", "Job must contain finite, bounded JSON data", status=422) from None
    request = {"kind": kind, "payload": payload or {}, "run_at": run_at}
    existing = tx.idempotency_get("job.enqueue", idempotency_key, request)
    if existing is not None:
        return _job(tx, existing["job_id"])
    settings = policy.get_settings(tx)
    killed = bool(settings.get("kill_switch"))
    job = tx.create("job", {
        "kind": kind, "payload": payload or {}, "status": "paused" if killed else "queued",
        "run_at": due, "attempts": 0, "max_attempts": MAX_ATTEMPTS,
        "safety_epoch": _epoch(settings), "requested_by": _identity(actor),
        "created_at": tx.now, "updated_at": tx.now, "lease_token": None,
        "lease_until": None, "paused_reason": "kill_switch" if killed else None,
        "result": None, "error_code": None,
    })
    tx.idempotency_put("job.enqueue", idempotency_key, request, {"job_id": job["id"]})
    tx.audit(_identity(actor), "job.enqueued", {"job_id": job["id"], "kind": kind, "status": job["status"]})
    return job


def pause_for_kill(tx, actor: Any = "system") -> int:
    """Called in the same transaction that changes the global kill switch."""
    count = 0
    for job in tx.list("job"):
        if job["status"] in ACTIVE:
            job.update(status="paused", paused_reason="kill_switch", lease_token=None, lease_until=None)
            _save(tx, job)
            tx.audit(_identity(actor), "job.paused", {"job_id": job["id"], "reason": "kill_switch"})
            count += 1
    return count


def pause(tx, job_id: str, *, actor: Any, reason: str = "Administrator paused job") -> dict:
    policy.require_admin(actor)
    job = _job(tx, job_id)
    if job["status"] == "completed":
        raise PolicyError("job_already_completed", "Completed jobs cannot be paused")
    if job["status"] == "uncertain":
        raise PolicyError("reconciliation_required", "An uncertain outcome cannot be bypassed by pausing the job")
    job.update(status="paused", paused_reason=reason[:300], lease_token=None, lease_until=None)
    result = _save(tx, job)
    tx.audit(_identity(actor), "job.paused", {"job_id": job_id, "reason": reason[:300]})
    return result


def resume(tx, job_id: str, *, actor: Any, reason: str = "Administrator reviewed paused job") -> dict:
    policy.require_admin(actor)
    settings = policy.get_settings(tx)
    if settings.get("kill_switch"):
        raise PolicyError("kill_switch", "Deactivate the kill switch before explicitly resuming a job")
    job = _job(tx, job_id)
    if job["status"] == "uncertain":
        raise PolicyError("reconciliation_required", "An uncertain provider outcome must be reconciled before retry")
    if job["status"] not in {"paused", "failed"}:
        raise PolicyError("job_not_paused", "Only paused or reviewed failed jobs can be resumed")
    job.update(status="queued", paused_reason=None, error_code=None, lease_token=None,
               lease_until=None, run_at=tx.now, safety_epoch=_epoch(settings))
    if job["attempts"] >= MAX_ATTEMPTS:
        job["attempts"] = 0
    job["manual_resumes"] = int(job.get("manual_resumes", 0)) + 1
    result = _save(tx, job)
    tx.audit(_identity(actor), "job.resumed", {"job_id": job_id, "reason": reason[:300],
                                              "safety_epoch": _epoch(settings)})
    return result


def reconcile(tx, job_id: str, *, outcome: str, reason: str, actor: Any) -> dict:
    policy.require_admin(actor)
    if outcome not in {"completed", "not_executed"} or not isinstance(reason, str) or len(reason.strip()) < 10:
        raise PolicyError("invalid_reconciliation", "A checked outcome and a meaningful review reason are required", status=422)
    job = _job(tx, job_id)
    if job["status"] != "uncertain":
        raise PolicyError("job_not_uncertain", "Only uncertain jobs need outcome reconciliation")
    job.update(status="completed" if outcome == "completed" else "paused",
               paused_reason=None if outcome == "completed" else "reconciled_not_executed",
               lease_token=None, lease_until=None, reconciliation={
                   "outcome": outcome, "reason": reason[:500], "actor": _identity(actor), "at": tx.now,
               })
    result = _save(tx, job)
    tx.audit(_identity(actor), "job.reconciled", {"job_id": job_id, "outcome": outcome, "reason": reason[:500]})
    return result


def claim(db, *, lease_seconds: float = LEASE_SECONDS) -> dict | None:
    if not 1 <= lease_seconds <= 600:
        raise ValueError("Lease duration must be between 1 and 600 seconds")
    with db.transaction() as tx:
        settings = policy.get_settings(tx)
        if settings.get("kill_switch"):
            pause_for_kill(tx)
            return None
        jobs = sorted(tx.list("job"), key=lambda j: (j["run_at"], j["created_at"], j["id"]))
        for job in jobs:
            if job["status"] not in ACTIVE:
                continue
            # This catches a kill activation/clear that happened between worker ticks.
            if job.get("safety_epoch", 0) != _epoch(settings):
                job.update(status="paused", paused_reason="safety_epoch_changed", lease_token=None, lease_until=None)
                _save(tx, job)
                tx.audit("system", "job.paused", {"job_id": job["id"], "reason": "safety_epoch_changed"})
                continue
            if job["status"] == "leased" and (job.get("lease_until") or 0) > tx.now:
                continue
            if job["run_at"] > tx.now:
                continue
            if job["attempts"] >= MAX_ATTEMPTS:
                job.update(status="failed", error_code="lease_exhausted", lease_token=None, lease_until=None)
                _save(tx, job)
                _task(tx, job, "The job exhausted its bounded attempts after interrupted workers")
                continue
            # Local work commits with the result. Provider handlers additionally
            # persist their idempotent outbox before dispatch: an expired lease
            # must resolve/quarantine that intent, never blindly send again.
            job.update(status="leased", attempts=job["attempts"] + 1,
                       lease_token=secrets.token_hex(16), lease_until=tx.now + lease_seconds)
            claimed = _save(tx, job)
            tx.audit("system", "job.claimed", {"job_id": job["id"], "attempt": job["attempts"]})
            return claimed
    return None


def _dispatch(tx, job: dict) -> dict:
    actor = {"id": "agent:ceo", "role": "operator"}
    policy.guard(tx, "job", actor=actor)
    payload = job["payload"]
    key = "job:" + job["id"]
    if job["kind"] in {"pipeline_advance", "source_research"}:
        from . import pipeline
        config = pipeline.status(tx)
        manual_preparation = job["kind"] == "pipeline_advance" and payload.get("manual_preparation") is True
        if (payload.get("pipeline_revision") != config["revision"]
                or (not manual_preparation and (not config["enabled"] or config["requires_reauthorization"]))):
            raise PolicyError("pipeline_authority_changed", "Review the preparation scheduler before resuming old work")
        if job["kind"] == "pipeline_advance":
            return pipeline.advance(tx, payload["lead_id"], actor=actor, key=key)
        from . import research
        return research.fetch_source(tx, payload["source_id"], actor=actor, key=key)
    if job["kind"] == "demo_journey":
        from . import workflow
        return workflow.run_demo(tx, actor=actor, key=key)
    if job["kind"] == "research":
        from . import workflow
        return workflow.research_lead(tx, payload["lead_id"], payload["evidence"], actor=actor, key=key)
    if job["kind"] == "qualify":
        from . import workflow
        return workflow.qualify_lead(tx, payload["lead_id"], actor=actor, key=key)
    if job["kind"] == "daily_report":
        from . import workflow
        report = workflow.agent_report(tx)
        return tx.create("report", {"role": "CEO", "generated_at": tx.now, "summary": report,
                                     "report_date": payload.get("report_date"), "timezone": SCHEDULE_TIMEZONE,
                                     "is_test": policy.get_settings(tx).get("mode") == "DEMO"})
    if job["kind"] == "follow_up":
        from . import pipeline
        configuration = pipeline.status(tx)
        allow_dispatch = payload.get("allow_dispatch") is True
        if allow_dispatch and (not configuration["enabled"] or configuration["requires_reauthorization"]
                or payload.get("pipeline_revision") != configuration["revision"]):
            raise PolicyError("pipeline_authority_changed", "Review follow-up dispatch authority before resuming old work")
        if allow_dispatch:
            lead = tx.get("lead", payload["lead_id"], {})
            if (type(payload.get("scheduled_for")) not in {int, float}
                    or lead.get("follow_up_at") != payload.get("scheduled_for")):
                return {"skipped": True, "reason": "The follow-up was rescheduled or cancelled"}
        return pipeline.follow_up(tx, payload["lead_id"], actor=actor, key=key,
                                  allow_dispatch=allow_dispatch)
    raise PolicyError("unknown_job_kind", "Unrecognized stored job kind", status=422)


def _failure(db, claimed: dict, error: Exception) -> None:
    with db.transaction() as tx:
        job = _job(tx, claimed["id"])
        if job["status"] != "leased" or job.get("lease_token") != claimed["lease_token"]:
            return  # An administrator or another fenced worker already changed it.
        settings = policy.get_settings(tx)
        code = error.code if isinstance(error, PolicyError) else type(error).__name__
        job.update(error_code=str(code)[:100], lease_token=None, lease_until=None)
        if settings.get("kill_switch") or job["safety_epoch"] != _epoch(settings):
            job.update(status="paused", paused_reason="kill_switch")
        elif str(code).lower() in {"provider_uncertain", "unknown_outcome", "uncertain_outcome", "payment_unknown"}:
            job.update(status="uncertain", paused_reason="provider_outcome_requires_review")
            _task(tx, job, "Provider outcome is unknown; automatic retries are forbidden")
        elif isinstance(error, PolicyError):
            job.update(status="paused", paused_reason=str(code)[:100])
            _task(tx, job, "A policy or approval requirement needs manual review")
        elif job["attempts"] >= MAX_ATTEMPTS:
            job.update(status="failed", paused_reason="attempt_limit")
            _task(tx, job, "The job reached its bounded attempt limit")
        else:
            job.update(status="queued", run_at=tx.now + min(60, 2 ** job["attempts"]))
        _save(tx, job)
        # Exception messages may contain provider responses/secrets. Store only a code.
        tx.audit("system", "job.interrupted", {"job_id": job["id"], "status": job["status"], "code": str(code)[:100]})


def run_once(db) -> dict | None:
    claimed = claim(db)
    if not claimed:
        return None
    try:
        with db.transaction() as tx:
            job = _job(tx, claimed["id"])
            settings = policy.get_settings(tx)
            if job["status"] != "leased" or job.get("lease_token") != claimed["lease_token"]:
                return job
            if settings.get("kill_switch") or job["safety_epoch"] != _epoch(settings):
                job.update(status="paused", paused_reason="kill_switch", lease_token=None, lease_until=None)
                tx.audit("system", "job.paused", {"job_id": job["id"], "reason": "kill_switch"})
                return _save(tx, job)
            tx.execution_fence = (job["id"], claimed["lease_token"])
            result = _dispatch(tx, job)
            # Provider outbox checkpoints can release the lock. Do not overwrite
            # a stop/reconfiguration that fenced this lease during dispatch.
            current = _job(tx, claimed["id"])
            settings = policy.get_settings(tx)
            if (current["status"] != "leased" or current.get("lease_token") != claimed["lease_token"]
                    or settings.get("kill_switch") or current["safety_epoch"] != _epoch(settings)):
                return current
            if isinstance(result, dict) and result.get("status") == "UNCERTAIN":
                job.update(status="uncertain", result=result, lease_token=None, lease_until=None,
                           paused_reason="provider_outcome_requires_review", error_code="provider_uncertain")
                _task(tx, job, "Provider outcome is unknown; automatic retries are forbidden")
                return _save(tx, job)
            job.update(status="completed", result=result, lease_token=None, lease_until=None,
                       completed_at=tx.now, error_code=None)
            saved = _save(tx, job)
            tx.audit("agent:ceo", "job.completed", {"job_id": job["id"], "kind": job["kind"]})
            return saved
    except Exception as error:
        _failure(db, claimed, error)
        with db.transaction() as tx:
            return _job(tx, claimed["id"])


def schedule_due(tx) -> list[dict]:
    settings = policy.get_settings(tx)
    if settings.get("kill_switch"):
        pause_for_kill(tx)
        return []
    if not settings.get("scheduler_enabled", True):
        return []
    day = datetime.fromtimestamp(tx.now, ZoneInfo(SCHEDULE_TIMEZONE)).date().isoformat()
    scheduled = [enqueue(tx, "daily_report", {"report_date": day, "timezone": SCHEDULE_TIMEZONE},
                         idempotency_key=f"daily-report:{day}")]
    for lead in tx.list("lead"):
        # Autonomous synthetic research is deliberately limited to labelled demo
        # imports. Real research requires an approved imported source/evidence.
        if settings.get("mode") == "DEMO" and lead.get("is_test") and lead.get("source_id") == "demo":
            if lead.get("state") == "DISCOVERED":
                scheduled.append(enqueue(tx, "research", {
                    "lead_id": lead["id"], "evidence": {
                        "summary": "Approved demo import: assess an inbound enquiry assistant; actual needs require customer confirmation.",
                    },
                }, idempotency_key="scheduled-research:" + lead["id"]))
            elif lead.get("state") == "RESEARCHED":
                scheduled.append(enqueue(tx, "qualify", {"lead_id": lead["id"]},
                                         idempotency_key="scheduled-qualify:" + lead["id"]))
        due = lead.get("follow_up_at")
        if isinstance(due, (int, float)) and due <= tx.now and lead.get("state") in {"CONTACTED", "NO_RESPONSE", "REPLIED", "NEGOTIATING", "PROPOSAL_SENT"}:
            from . import pipeline
            configuration = pipeline.status(tx)
            allow_dispatch = bool(configuration["enabled"] and not configuration["requires_reauthorization"])
            # Preserve v1.0 payload fingerprints for existing review-only jobs.
            payload = {"lead_id": lead["id"]}
            key = f"follow-up:{lead['id']}:{int(due)}"
            if allow_dispatch:
                payload.update(allow_dispatch=True, pipeline_revision=configuration["revision"], scheduled_for=due)
                from .store import fingerprint
                key = "v11-follow-up:" + fingerprint({"lead_id": lead["id"], "due": due, "revision": configuration["revision"]})
            scheduled.append(enqueue(tx, "follow_up", payload, idempotency_key=key))
    from . import pipeline
    scheduled.extend(pipeline.schedule_due(tx))
    return scheduled


def worker_loop(db, stop_event: Event, *, poll_interval: float = 1.0) -> None:
    """One bounded claim per tick; the caller owns process/thread lifecycle."""
    if poll_interval < 0.05:
        raise ValueError("Polling interval must be at least 50 ms")
    while not stop_event.is_set():
        try:
            with db.transaction() as tx:
                schedule_due(tx)
            run_once(db)
        except Exception:
            # Do not leak SQL/secret-bearing exception text or spin after a DB outage.
            pass
        stop_event.wait(poll_interval)
