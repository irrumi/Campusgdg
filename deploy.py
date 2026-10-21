"""Deploy CampusAgent to Vertex AI Agent Engine (Reasoning Engine).
Usage: python deploy.py --project PROJECT --bucket gs://BUCKET [--location us-central1]
"""
import argparse
import json
import re
from pathlib import Path

from agent.agent import CampusAgent


def deployment_config(bucket: str, github_secret: str | None = None, secret_version: str = "1") -> dict:
    """Build an SDK 2.5 config without credentials, file reads or cloud calls."""
    if not bucket.startswith("gs://") or not bucket[5:].strip("/"):
        raise ValueError("Use a gs:// staging bucket.")
    if github_secret and (not re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", github_secret)
                          or not secret_version.isdigit() or int(secret_version) < 1):
        raise ValueError("Use a Secret Manager secret ID and a positive numeric version.")
    return {
        "staging_bucket": bucket,
        "requirements": ["google-genai>=2.31,<3", "requests>=2.31,<3", "pypdf>=6.21,<7", "cloudpickle>=3,<4",
                         "google-cloud-agentplatform[agent_engines]==2.5.0", "pydantic-settings>=2.7,<3", "mcp>=1.10,<2",
                         "httpx>=0.28,<1", "jsonschema>=4.23,<5", "tzdata"],
        "extra_packages": ["agent", "campuspulse"],
        "env_vars": {"GITHUB_TOKEN": {"secret": github_secret, "version": secret_version}} if github_secret else {},
        "display_name": "campuspulse-ai",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--model-location", default="global")
    parser.add_argument("--create-bucket", action="store_true", help="Create the staging bucket if missing")
    parser.add_argument("--github-secret", help="Secret Manager secret ID, never its value")
    parser.add_argument("--secret-version", default="1", help="Pinned numeric Secret Manager version")
    parser.add_argument("--dry-run", action="store_true", help="Validate SDK configuration without contacting Google Cloud")
    args = parser.parse_args(argv)
    try:
        config = deployment_config(args.bucket, args.github_secret, args.secret_version)
    except ValueError as exc:
        parser.error(str(exc))

    from agentplatform import types
    types.AgentRuntimeConfig.model_validate(config)
    if args.dry_run:
        print(json.dumps({"cloud_contacted": False, "config": config}, indent=2))
        return 0

    if args.create_bucket:
        from google.cloud import storage
        storage_client = storage.Client(project=args.project)
        bucket = storage_client.bucket(args.bucket.removeprefix("gs://").rstrip("/"))
        if not bucket.exists():
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            storage_client.create_bucket(bucket, location=args.location)
            print("Created staging bucket:", bucket.name, flush=True)

    import agentplatform
    client = agentplatform.Client(project=args.project, location=args.location)
    remote = client.runtimes.create(
        agent=CampusAgent(project=args.project, location=args.model_location, model=args.model), config=config)
    resource = remote.api_resource.name
    print("Deployed:", resource)
    state = {"project": args.project, "location": args.location, "model": args.model,
             "model_location": args.model_location, "agent_engine_resource": resource}
    Path(__file__).with_name("vertex.config.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
