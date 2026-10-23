"""Check local Blueprint wiring without contacting Render or creating resources.

Run with apps/ai-engine/.venv/bin/python deploy/render/validate_blueprint.py.
This complements, and does not replace, Render's official schema validation.
"""

from pathlib import Path
import shlex
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
services = blueprint["services"]
databases = blueprint["databases"]
groups = blueprint["envVarGroups"]


def unique_by_name(resources):
    result = {resource["name"]: resource for resource in resources}
    assert len(result) == len(resources), "Duplicate resource names"
    return result


service_map = unique_by_name(services)
database_map = unique_by_name(databases)
group_map = unique_by_name(groups)
all_names = [*service_map, *database_map, *group_map]
assert len(all_names) == len(set(all_names)), "Names overlap across resource types"

assert blueprint["previews"]["generation"] == "off"
assert {r["region"] for r in services + databases} == {"frankfurt"}
assert all(r["plan"] != "free" for r in services + databases)
assert {r["name"] for r in services if r["type"] == "web"} == {
    "ignitic-frontend", "ignitic-backend"
}


def env_keys(service):
    keys = {item["key"] for item in service.get("envVars", []) if "key" in item}
    for item in service.get("envVars", []):
        if "fromGroup" in item:
            keys.update(x["key"] for x in group_map[item["fromGroup"]]["envVars"])
    return keys


reference_count = 0
for resource in services + databases:
    if resource.get("type") != "keyvalue" and resource in services:
        assert resource["autoDeployTrigger"] == "off"
    if resource.get("type") == "keyvalue" or resource in databases:
        assert resource["ipAllowList"] == [], "Datastore must be private"
    for item in resource.get("envVars", []):
        modes = set(item) & {
            "value", "sync", "generateValue", "fromService", "fromDatabase", "fromGroup"
        }
        assert len(modes) == 1, f"Ambiguous env declaration: {item.get('key')}"
        if "fromService" in item:
            ref = item["fromService"]
            target = service_map[ref["name"]]
            assert ref["type"] == target["type"]
            if "envVarKey" in ref:
                assert ref["envVarKey"] in env_keys(target)
            else:
                assert ref["property"] in {"host", "port", "hostport", "connectionString"}
            reference_count += 1
        if "fromDatabase" in item:
            ref = item["fromDatabase"]
            assert ref["name"] in database_map
            assert ref["property"] in {
                "host", "port", "user", "password", "database", "connectionString"
            }
            reference_count += 1
        if "fromGroup" in item:
            assert item["fromGroup"] in group_map
            reference_count += 1
        if item.get("key", "").endswith(("API_KEY", "API_SECRET", "CLIENT_SECRET", "TOKEN")):
            assert item.get("value", "") == "", "External credentials must not be embedded"
    if resource.get("runtime") == "docker":
        base = ROOT / resource.get("rootDir", ".")
        assert (base / resource["dockerfilePath"]).is_file()
        assert (base / resource["dockerContext"]).is_dir()
    if "dockerCommand" in resource:
        command = shlex.split(resource["dockerCommand"])
        assert command[:2] == ["/bin/sh", "-ec"] and len(command) == 3
        subprocess.run(["/bin/sh", "-n"], input=command[2], text=True, check=True)

assert env_keys(service_map["ignitic-backend"]) >= {"ENCRYPTION_KEY", "JWT_SECRET"}
assert env_keys(service_map["ignitic-ai-engine"]) >= {"PASS_ENCRYPTION_FERNET_KEY", "JWT_SECRET"}
for resource in groups:
    assert all(set(item) <= {"key", "value", "generateValue"} for item in resource["envVars"])
for resource in services:
    if "disk" in resource:
        assert resource["numInstances"] == 1
        assert resource["disk"]["mountPath"].startswith("/")
        assert resource["disk"]["sizeGB"] > 0
subprocess.run(["/bin/sh", "-n", str(ROOT / "deploy/render/start-neo4j.sh")], check=True)
print(f"Local Blueprint checks passed: {len(services)} services, {len(databases)} database, {reference_count} references")
