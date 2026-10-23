#!/usr/bin/env python3
"""Check the local Ignitic stack without invoking LLMs or third-party tools.

Run after starting all services: python scripts/smoke-stack.py
To also prove successful frontend -> backend authentication on an EMPTY local
Docker development database, activate Go and add --with-local-account. This
creates one synthetic verified user directly in PostgreSQL and removes it in a
finally block. Registration, verification email and external integrations are
never called. Ordinary login audit records are retained.
"""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)
import uuid


ROOT = Path(__file__).resolve().parents[1]


class SmokeError(Exception):
    pass


def check(condition, description):
    if not condition:
        raise SmokeError(description)
    print(f"PASS {description}")


def local_url(value):
    parsed = urlparse(value)
    if (parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("Only loopback HTTP(S) service URLs are allowed")
    return value.rstrip("/")


def guard_local_request(value):
    parsed = urlparse(value)
    if (parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password):
        raise SmokeError("Only loopback HTTP(S) request targets without embedded credentials are allowed")


class LocalRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        guard_local_request(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def local_secret():
    if os.environ.get("JWT_SECRET"):
        return os.environ["JWT_SECRET"]
    config = ROOT / ".env"
    if config.is_file():
        for line in config.read_text().splitlines():
            if line.startswith("JWT_SECRET="):
                return line.partition("=")[2].strip().strip("\"'")
    raise SmokeError("JWT_SECRET must be set or present in the ignored root .env")


def token(secret):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

    now = int(time.time())
    header = encode({"alg": "HS256", "typ": "JWT"})
    payload = encode({
        "user_id": str(uuid.uuid4()), "email": "stack-smoke@example.com",
        "role": "user", "name": "Local stack smoke", "iat": now, "exp": now + 120,
    })
    message = header + b"." + payload
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), message, hashlib.sha256).digest()
    ).rstrip(b"=")
    return (message + b"." + signature).decode()


class Client:
    def __init__(self):
        # Every target is guarded as loopback. Do not use externally routed proxies.
        self.opener = build_opener(
            ProxyHandler({}), LocalRedirectHandler(), HTTPCookieProcessor(CookieJar()),
        )

    def request(self, url, payload=None, headers=None, form=False):
        guard_local_request(url)
        headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if payload is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            data = (urlencode(payload) if form else json.dumps(payload)).encode()
        try:
            response = self.opener.open(Request(url, data=data, headers=headers), timeout=90)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    def json(self, url, payload=None, headers=None, form=False):
        status, response_headers, body = self.request(url, payload, headers, form)
        return status, response_headers, json.loads(body) if body else None


class SignInForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id"):
            self.inputs[attrs["id"]] = attrs.get("type")


def psql(sql):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c",
         'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
        cwd=ROOT, input=sql, text=True, capture_output=True,
    )
    if result.returncode:
        raise SmokeError("Local PostgreSQL fixture operation failed; secret-bearing SQL is omitted")
    return result.stdout.strip()


@contextmanager
def local_account():
    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not docker_host.startswith("unix://"):
        raise SmokeError("Account fixtures require a local Unix-socket Docker daemon")
    context = subprocess.run(
        ["docker", "context", "inspect"], text=True, capture_output=True, check=True,
    )
    endpoint = json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise SmokeError("Account fixtures are forbidden on remote Docker contexts")
    if psql("SELECT count(*) FROM users;") != "0":
        raise SmokeError("Account fixtures require an empty local development users table")

    user_id = str(uuid.uuid4())
    email = f"stack-smoke-{uuid.uuid4().hex}@example.com"
    password = secrets.token_urlsafe(32)
    # Use the same bcrypt implementation as the backend. The password is stdin,
    # never a command argument, environment variable, log entry or committed file.
    with tempfile.TemporaryDirectory(prefix="ignitic-smoke-") as directory:
        source = Path(directory) / "hash.go"
        source.write_text('''package main
import ("fmt"; "io"; "os"; "golang.org/x/crypto/bcrypt")
func main() {
 p, err := io.ReadAll(os.Stdin); if err != nil { panic(err) }
 h, err := bcrypt.GenerateFromPassword(p, bcrypt.DefaultCost); if err != nil { panic(err) }
 fmt.Print(string(h))
}
''')
        hashed = subprocess.run(
            ["go", "run", str(source)], cwd=ROOT / "apps/backend",
            input=password, text=True, capture_output=True, check=True,
        ).stdout
    # All interpolated values are internally generated, not user-supplied SQL.
    psql(f"""INSERT INTO users
        (id, email, password, first_name, last_name, role, is_active, email_verified)
        VALUES ('{user_id}', '{email}', '{hashed}', 'Stack', 'Smoke', 'user', true, true);""")
    try:
        yield email, password
    finally:
        psql(f"DELETE FROM users WHERE id = '{user_id}' AND email = '{email}';")
        check(psql(f"SELECT count(*) FROM users WHERE id = '{user_id}';") == "0",
              "synthetic local account removed")


def authenticated_frontend(frontend, backend, email, password):
    client = Client()
    status, _, body = client.json(backend + "/api/v1/auth/login",
                                  {"email": email, "password": password})
    check(status == 200 and body.get("user", {}).get("email") == email and body.get("token"),
          "backend login with synthetic local account")
    auth_token = body["token"]
    status, _, csrf = client.json(frontend + "/api/auth/csrf")
    check(status == 200 and bool(csrf.get("csrfToken")), "frontend CSRF protection")
    status, _, result = client.json(frontend + "/api/auth/callback/credentials", {
        "csrfToken": csrf["csrfToken"], "email": email, "password": password,
        "redirect": "false", "json": "true",
    }, form=True)
    check(status == 200 and "error=" not in result.get("url", "error="),
          "frontend credentials callback calls backend successfully")
    status, _, session = client.json(frontend + "/api/auth/session")
    session_email = session.get("user", {}).get("user", {}).get("email")
    check(status == 200 and session_email == email and bool(session.get("accessToken"))
          and not session.get("error"), "frontend session contains authenticated backend identity")
    return auth_token


def mcp_readiness(client, base, auth):
    headers = {"Authorization": auth, "Accept": "application/json, text/event-stream"}

    def rpc(payload):
        status, response_headers, body = client.request(base + "/business_analyst/", payload, headers)
        if response_headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = response_headers["Mcp-Session-Id"]
        if response_headers.get("Content-Type", "").startswith("text/event-stream"):
            messages = [json.loads(line[5:].strip()) for line in body.splitlines()
                        if line.startswith("data:")]
            result = next((message for message in messages if message.get("id") == payload.get("id")), {})
        else:
            result = json.loads(body) if body else {}
        return status, result

    status, result = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "ignitic-local-smoke", "version": "1.0"},
    }})
    check(status == 200 and "result" in result, "MCP authenticated protocol initialization")
    headers["MCP-Protocol-Version"] = result["result"]["protocolVersion"]
    status, _ = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check(status == 202, "MCP initialization notification")
    status, result = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = result.get("result", {}).get("tools", [])
    check(status == 200 and len(tools) == 7
          and "ba_unit_economics_breakeven" in {tool["name"] for tool in tools},
          "MCP exposes all seven business analysis tools (no tool execution)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=local_url, default="http://127.0.0.1:3000")
    parser.add_argument("--backend", type=local_url, default="http://127.0.0.1:8080")
    parser.add_argument("--ai-engine", type=local_url, default="http://127.0.0.1:8010")
    parser.add_argument("--mcp", type=local_url, default="http://127.0.0.1:8011")
    parser.add_argument("--with-local-account", action="store_true")
    args = parser.parse_args()
    client = Client()
    for name, base, expected in (("backend", args.backend, "healthy"),
                                 ("AI Engine", args.ai_engine, "healthy"),
                                 ("MCP", args.mcp, "ok")):
        status, _, body = client.json(base + "/health")
        check(status == 200 and body.get("status") == expected, name + " health")
    status, _, html = client.request(args.frontend + "/signin")
    form = SignInForm()
    form.feed(html)
    check(status == 200 and form.inputs.get("email") == "email"
          and form.inputs.get("password") == "password", "frontend sign-in form renders")
    status, _, providers = client.json(args.frontend + "/api/auth/providers")
    check(status == 200 and providers.get("credentials", {}).get("type") == "credentials",
          "frontend credentials provider")
    status, _, body = client.json(args.backend + "/api/v1/auth/login", {
        "email": f"missing-{uuid.uuid4().hex}@example.com", "password": secrets.token_urlsafe(32),
    })
    check(status == 401 and body.get("error") == "Invalid credentials",
          "backend rejects nonexistent local account against PostgreSQL")
    auth_token = token(local_secret())
    if args.with_local_account:
        with local_account() as (email, password):
            auth_token = authenticated_frontend(args.frontend, args.backend, email, password)
            cross_service_checks(client, args, auth_token)
    else:
        print("SKIP successful frontend-to-backend login (requires --with-local-account)")
        cross_service_checks(client, args, auth_token)
    print("Local stack smoke complete; no external LLM or third-party tool was invoked.")


def cross_service_checks(client, args, auth_token):
    auth = "Bearer " + auth_token
    headers = {"Authorization": auth}
    status, _, rmq = client.json(args.ai_engine + "/health/rmq")
    services = rmq.get("services", {})
    check(status == 200 and rmq.get("status") == "healthy" and len(services) == 2
          and all(service.get("is_consuming") and service.get("channel_open")
                  for service in services.values()), "AI Engine RabbitMQ consumers")
    status, _, workflows = client.json(args.ai_engine + "/api/v1/workflow-template/?limit=0", headers=headers)
    check(status == 200 and isinstance(workflows, list), "authenticated AI workflow templates read")
    mcp_readiness(client, args.mcp, auth)
    status, _, tools = client.json(args.ai_engine + "/api/v1/agents/available-tools", headers=headers)
    names = {tool.get("name") for tool in tools} if isinstance(tools, list) else set()
    check(status == 200 and "ba_unit_economics_breakeven" in names,
          "AI Engine lists actual MCP tools over HTTP")
    status, _, tools = client.json(args.backend + "/api/v1/agents/available-tools", headers=headers)
    names = {tool.get("name") for tool in tools} if isinstance(tools, list) else set()
    check(status == 200 and "ba_unit_economics_breakeven" in names,
          "backend -> AI Engine -> MCP lists actual tools")


if __name__ == "__main__":
    try:
        main()
    except (SmokeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        # Responses may contain session tokens. Never dump bodies or subprocess SQL.
        message = str(error) if isinstance(error, SmokeError) else type(error).__name__
        raise SystemExit("FAIL " + message)
