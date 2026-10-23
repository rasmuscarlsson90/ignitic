"""Non-deploying checks for the Lite resource and safety boundaries."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_render_lite_is_one_public_app_and_one_private_persistent_database():
    config = yaml.safe_load((ROOT / "render-lite.yaml").read_text())
    assert len(config["services"]) == len(config["databases"]) == 1
    web, db = config["services"][0], config["databases"][0]
    assert web["type"] == "web" and web["runtime"] == "docker"
    assert web["numInstances"] == 1 and web["plan"] != "free"
    assert db["ipAllowList"] == [] and db["diskSizeGB"] >= 5
    assert db["postgresMajorVersion"] == "16" and db["plan"] != "free"
    assert web["region"] == db["region"]
    assert web["autoDeployTrigger"] == config["previews"]["generation"] == "off"
    app = ROOT / web["rootDir"]
    assert (app / web["dockerfilePath"]).is_file()
    assert (app / web["dockerContext"]).is_dir()
    env = {item["key"]: item for item in web["envVars"]}
    assert env["JARVIS_DATABASE_URL"]["fromDatabase"]["name"] == db["name"]
    assert env["JARVIS_DATABASE_URL"]["fromDatabase"]["property"] == "connectionString"
    assert env["JARVIS_ALLOW_LIVE"]["value"] == "false"
    assert env["JARVIS_WORKER_ENABLED"]["value"] == "true"
    assert env["JARVIS_SESSION_SECRET"]["generateValue"] is True
    assert env["JARVIS_ENCRYPTION_KEY"]["sync"] is False
    assert env["JARVIS_BOOTSTRAP_PASSWORD"]["sync"] is False
    assert not any(key.endswith(("API_KEY", "SECRET_KEY")) for key in env)


def test_compose_lite_has_no_public_database_and_uses_separate_storage():
    config = yaml.safe_load((ROOT / "docker-compose.lite.yml").read_text())
    assert set(config["services"]) == {"web", "postgres"}
    web, db = config["services"]["web"], config["services"]["postgres"]
    assert not db.get("ports")
    assert db["volumes"] == ["jarvis-lite-postgres:/var/lib/postgresql/data"]
    assert web["ports"] == ["127.0.0.1:8012:8000"]
    assert web["environment"]["JARVIS_ALLOW_LIVE"] == "false"
    assert web["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert web["restart"] == db["restart"] == "unless-stopped"
