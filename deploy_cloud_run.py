"""Deploy the authenticated API gateway using local Google ADC, without exporting keys."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile

import google.auth
from google.auth.transport.requests import Request

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "vertex.config.json"
CONFIG = {"location": "us-central1", "model_location": "global", "model": "gemini-3.1-flash-lite"}
for config_file in (ROOT / "agent.connection.json", CONFIG_PATH):
    if config_file.exists():
        CONFIG.update(json.loads(config_file.read_text(encoding="utf-8")))
PROJECT = CONFIG["project"]
REGION = CONFIG["location"]
SERVICE = "campus-agent-api"
GCLOUD = shutil.which("gcloud") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")


def run(*args, capture=False):
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    with tempfile.TemporaryDirectory(prefix="campus-gcloud-") as temporary:
        token_file = Path(temporary) / "access-token"
        token_file.write_text(credentials.token, encoding="utf-8")
        command = [str(GCLOUD), *args, f"--project={PROJECT}", "--quiet", f"--access-token-file={token_file}"]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=capture, check=True)
        return result.stdout if capture else None


def ensure_account(account_id):
    email = f"{account_id}@{PROJECT}.iam.gserviceaccount.com"
    accounts = json.loads(run("iam", "service-accounts", "list", "--format=json", capture=True))
    if not any(account["email"] == email for account in accounts):
        run("iam", "service-accounts", "create", account_id, f"--display-name={account_id}")
    return email


def main():
    variables = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.strip().startswith("#"):
            variables[key.strip()] = value.strip()
    key_hash = variables.get("CAMPUS_API_KEY_SHA256", "")
    if len(key_hash) != 64 or any(character not in "0123456789abcdefABCDEF" for character in key_hash):
        raise RuntimeError("A valid CAMPUS_API_KEY_SHA256 is required in .env")
    run("services", "enable", "run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com")
    runtime = ensure_account("campus-agent-run")
    builder = ensure_account("campus-agent-builder")
    for email, role in ((runtime, "roles/aiplatform.user"), (builder, "roles/run.builder")):
        run("projects", "add-iam-policy-binding", PROJECT, f"--member=serviceAccount:{email}", f"--role={role}", "--condition=None", "--format=none")
    environment = {
        "GOOGLE_CLOUD_PROJECT": PROJECT,
        "GOOGLE_CLOUD_LOCATION": CONFIG.get("model_location", "global"),
        "GOOGLE_CLOUD_MODEL": CONFIG["model"],
        "AGENT_ENGINE_RESOURCE": CONFIG["agent_engine_resource"],
        "CAMPUS_API_KEY_SHA256": key_hash,
        "RATE_LIMIT_PER_MIN": variables.get("RATE_LIMIT_PER_MIN", "10"),
    }
    with tempfile.TemporaryDirectory(prefix="campus-run-") as temporary:
        environment_file = Path(temporary) / "environment.yaml"
        # JSON is valid YAML; only the hash, never the API key, is deployed.
        environment_file.write_text(json.dumps(environment), encoding="utf-8")
        source = Path(temporary) / "source"
        source.mkdir()
        shutil.copyfile(ROOT / "Dockerfile.agent-api", source / "Dockerfile")
        shutil.copyfile(ROOT / "requirements-cloudrun.txt", source / "requirements-cloudrun.txt")
        shutil.copytree(ROOT / "agent", source / "agent", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "campuspulse", source / "campuspulse", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        run("run", "deploy", SERVICE, f"--source={source}", f"--region={REGION}",
            f"--service-account={runtime}", f"--build-service-account=projects/{PROJECT}/serviceAccounts/{builder}",
            f"--env-vars-file={environment_file}", "--allow-unauthenticated", "--port=8080",
            "--memory=1Gi", "--cpu=1", "--min-instances=0", "--max-instances=2",
            "--concurrency=8", "--timeout=300")
    url = run("run", "services", "describe", SERVICE, f"--region={REGION}", "--format=value(status.url)", capture=True).strip()
    CONFIG.update(api_url=url + "/api/v1/chat", cloud_run_service=SERVICE, cloud_run_region=REGION)
    CONFIG_PATH.write_text(json.dumps(CONFIG, indent=2) + "\n", encoding="utf-8")
    # Keep a portable, non-secret connection file alongside the application.
    (ROOT / "agent.connection.json").write_text(json.dumps({"api_url": CONFIG["api_url"], "agent_engine_resource": CONFIG["agent_engine_resource"], "project": PROJECT}, indent=2) + "\n", encoding="utf-8")
    print("API endpoint:", CONFIG["api_url"], flush=True)


if __name__ == "__main__":
    main()
