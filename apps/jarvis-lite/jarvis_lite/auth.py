"""Server-side opaque sessions, RBAC and CSRF protection; no browser API keys."""
import base64
import hashlib
import hmac
import secrets

from .policy import PolicyError


def hash_password(password):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise PolicyError("password_length", "Use a password between 12 and 256 characters", 422)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or len(password) > 256:
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(got.hex(), expected)
    except (ValueError, TypeError):
        return False


def public_user(user):
    return {k: user[k] for k in ("id", "email", "role")}


def create_user(tx, email, password, role="operator", actor=None, bootstrap=False):
    from .policy import require_admin
    if not bootstrap:
        require_admin(actor)
    email = email.strip().lower()
    if "@" not in email or any(c.isspace() for c in email) or len(email) > 254:
        raise PolicyError("email", "A valid email address is required", 422)
    if role not in {"admin", "operator", "viewer"}:
        raise PolicyError("role", "Unknown role", 422)
    if any(u["email"] == email for u in tx.list("user")):
        raise PolicyError("duplicate_user", "This operator already exists")
    user = tx.create("user", {"email": email, "role": role, "password_hash": hash_password(password), "active": True})
    tx.audit(actor or "bootstrap", "user_created", {"user_id": user["id"], "role": role})
    return public_user(user)


def login(tx, email, password, client_address):
    """Return errors as data so failed-attempt counters commit before HTTP errors."""
    normalized = email.strip().lower()
    rate_id = hashlib.sha256((client_address + "|" + normalized).encode()).hexdigest()
    attempts = tx.get("login_rate", rate_id, {"count": 0, "since": tx.now})
    if tx.now - attempts["since"] > 900:
        attempts = {"count": 0, "since": tx.now}
    if attempts["count"] >= 5:
        return {"error": "login_rate", "status": 429}
    user = next((u for u in tx.list("user") if u["email"] == normalized and u.get("active")), None)
    if not user:
        # Match the expensive password path without storing an invented account.
        hashlib.scrypt(password[:256].encode(), salt=b"jarvis-login-pad", n=16384, r=8, p=1)
    if not user or not verify_password(password, user["password_hash"]):
        tx.put("login_rate", rate_id, {**attempts, "count": attempts["count"] + 1})
        tx.audit("anonymous", "login_rejected", {"rate_id": rate_id})
        return {"error": "invalid_credentials", "status": 401}
    tx.delete("login_rate", rate_id)
    raw = secrets.token_urlsafe(40)
    csrf = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    tx.put("session", hashlib.sha256(raw.encode()).hexdigest(), {
        "user_id": user["id"], "csrf": csrf, "expires_at": tx.now + 8 * 3600})
    tx.audit(user, "login_succeeded", {"user_id": user["id"]})
    return {"user": public_user(user), "csrf_token": csrf, "session_token": raw}


def current_actor(tx, request, *, write=False, roles=None):
    raw = request.cookies.get("jarvis_session", "")
    session = tx.get("session", hashlib.sha256(raw.encode()).hexdigest()) if raw else None
    user = tx.get("user", session["user_id"]) if session else None
    if not session or session["expires_at"] < tx.now or not user or not user.get("active"):
        raise PolicyError("authentication_required", "Sign in to continue", 401)
    if roles and user["role"] not in roles:
        raise PolicyError("forbidden", "Your role cannot perform this action", 403)
    if write and not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), session["csrf"].encode()):
        raise PolicyError("csrf", "A valid CSRF token is required", 403)
    return public_user(user), session
