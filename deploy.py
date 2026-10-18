"""Deploy CampusAgent to Vertex AI Agent Engine (Reasoning Engine).
Usage: python deploy.py --project PROJECT --bucket gs://BUCKET [--location us-central1]
"""
import argparse
import json
from pathlib import Path

import vertexai
from vertexai import agent_engines

from agent.agent import CampusAgent

config_path = Path(__file__).with_name("vertex.config.json")
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
p = argparse.ArgumentParser()
p.add_argument("--project", default=config.get("project"), required=not config.get("project"))
p.add_argument("--bucket", required=True)
p.add_argument("--location", default=config.get("location", "us-central1"))
p.add_argument("--model", default=config.get("model", "gemini-3.1-flash-lite"))
p.add_argument("--model-location", default=config.get("model_location", "global"))
p.add_argument("--create-bucket", action="store_true", help="Create the staging bucket if missing")
a = p.parse_args()

if a.create_bucket:
    from google.cloud import storage
    storage_client = storage.Client(project=a.project)
    bucket = storage_client.bucket(a.bucket.removeprefix("gs://").rstrip("/"))
    if not bucket.exists():
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        storage_client.create_bucket(bucket, location=a.location)
        print("Created staging bucket:", bucket.name, flush=True)

vertexai.init(project=a.project, location=a.location, staging_bucket=a.bucket)
remote = agent_engines.create(
    CampusAgent(project=a.project, location=a.model_location, model=a.model),
    requirements=["google-genai", "requests", "pypdf", "cloudpickle", "google-cloud-aiplatform[agent_engines]"],
    extra_packages=["agent"],
    display_name="campuspulse-ai",
)
print("Deployed:", remote.resource_name)
config.update(project=a.project, location=a.location, model=a.model, model_location=a.model_location,
              agent_engine_resource=remote.resource_name)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
