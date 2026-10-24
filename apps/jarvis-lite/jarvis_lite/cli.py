"""Reproducible demo initialization and explicit operator bootstrap."""
import argparse
import os
from pathlib import Path
import secrets
import sys

from cryptography.fernet import Fernet
from dotenv import load_dotenv
import uvicorn

from . import auth, policy
from .store import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-demo")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8020")))
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "init-demo":
        Path(".local").mkdir(exist_ok=True, mode=0o700)
        if Path(".env").exists():
            print("Preserved existing .env; use bootstrap-admin if an operator is missing.")
            return
        password = secrets.token_urlsafe(24)
        values = {
            "JARVIS_ENV": "development", "JARVIS_DATABASE_URL": "sqlite:///.local/jarvis.db",
            "JARVIS_SESSION_SECRET": secrets.token_urlsafe(48), "JARVIS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "JARVIS_ALLOW_LIVE": "false", "JARVIS_WORKER_ENABLED": "true",
            "JARVIS_BOOTSTRAP_EMAIL": "admin@example.com", "JARVIS_BOOTSTRAP_PASSWORD": password,
            "JARVIS_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        }
        fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
        fd = os.open(".local/admin-password", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(password + "\n")
        print("DEMO configured. Operator: admin@example.com. Password saved securely in .local/admin-password.")
        return
    load_dotenv()
    if args.command == "bootstrap-admin":
        password = os.getenv("JARVIS_BOOTSTRAP_PASSWORD") or sys.stdin.readline().rstrip("\n")
        db = Database(os.environ["JARVIS_DATABASE_URL"])
        with db.transaction() as tx:
            if tx.list("user"):
                raise SystemExit("Operators already exist. Use the authenticated admin API instead.")
            policy.initialize(tx)
            auth.create_user(tx, args.email, password, "admin", bootstrap=True)
        db.close()
        print("Administrator created; no credentials logged.")
    else:
        uvicorn.run("jarvis_lite.app:create_app", factory=True, host=args.host, port=args.port,
                    workers=1, proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
