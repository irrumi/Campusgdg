"""Check Google Cloud access without printing credentials or creating resources."""
import json
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession


def main():
    config = {"location": "us-central1"}
    for name in ("agent.connection.json", "vertex.config.json"):
        path = Path(__file__).with_name(name)
        if path.exists():
            config.update(json.loads(path.read_text(encoding="utf-8")))
    project = config["project"]
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    urls = {
        "project": f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}",
        "billing": f"https://cloudbilling.googleapis.com/v1/projects/{project}/billingInfo",
        "vertex_api": f"https://serviceusage.googleapis.com/v1/projects/{project}/services/aiplatform.googleapis.com",
        "agents": f"https://{config['location']}-aiplatform.googleapis.com/v1/projects/{project}/locations/{config['location']}/reasoningEngines",
    }
    for label, url in urls.items():
        response = session.get(url, timeout=30)
        data = response.json()
        if label == "vertex_api" and response.ok:
            data = {"state": data.get("state")}
        if label == "billing" and response.ok:
            data = {"billingEnabled": data.get("billingEnabled")}
        print(label, response.status_code, json.dumps(data, ensure_ascii=False)[:3000], flush=True)


if __name__ == "__main__":
    main()
