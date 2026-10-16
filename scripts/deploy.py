"""Explicit source deployment to an Agent Engine reasoningEngines resource."""
from __future__ import annotations

import argparse
from contextlib import chdir
import hashlib
import importlib.metadata
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campuspulse.cloud import operation_schemas  # noqa: E402

SDK_VERSION = "2.5.0"
SOURCE_FILES = tuple(f"campuspulse/{name}.py" for name in (
    "__init__", "config", "models", "parsing", "extraction", "security", "github_mcp", "agent", "cloud"
)) + ("requirements-cloud.txt",)
RESOURCE_PATTERN = re.compile(r"projects/([a-zA-Z0-9-]+)/locations/([a-z0-9-]+)/reasoningEngines/([0-9]+)")


def source_manifest(root: Path = ROOT) -> list[dict[str, Any]]:
    """An explicit source allowlist excludes dotenv, ADC, SQLite, VCS and caches."""
    entries = []
    for name in SOURCE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or unsafe deployment source: {name}")
        content = path.read_bytes()
        entries.append({"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    if sum(item["bytes"] for item in entries) > 8_000_000:
        raise ValueError("Deployment source exceeds the 8 MB inline source limit.")
    return entries


def build_config(*, project: str, location: str, service_account: str, secret_id: str,
                 secret_version: str = "1", model: str = "gemini-3.8-flash",
                 gemini_location: str = "global", source_packages: list[str] | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("Use a valid Google Cloud project ID.")
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", location):
        raise ValueError("Use an Agent Engine region, such as us-central1.")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", secret_id) or not re.fullmatch(r"[1-9][0-9]*", secret_version):
        raise ValueError("Use a Secret Manager secret ID and pinned numeric version.")
    if not re.fullmatch(r"[a-z0-9-]+@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com", service_account):
        raise ValueError("Use a custom runtime service account email.")
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model) or gemini_location not in {"global", "us", "eu"}:
        raise ValueError("Use a Gemini model ID and supported Gemini location: global, us or eu.")
    return {
        "source_packages": source_packages or list(SOURCE_FILES),
        "entrypoint_module": "campuspulse.cloud",
        "entrypoint_object": "root_agent",
        "class_methods": operation_schemas(),
        "requirements_file": "requirements-cloud.txt",
        "python_version": "3.12",
        "display_name": "CampusPulse Assignment Guardian",
        "description": "Read-only assignment analysis with Gemini and the official GitHub MCP server.",
        "labels": {"app": "campuspulse", "mode": "read-only"},
        "agent_framework": "custom",
        "service_account": service_account,
        "min_instances": 0,
        "max_instances": 2,
        "container_concurrency": 2,
        "env_vars": {
            "CAMPUSPULSE_PROJECT": project,
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GEMINI_MODEL": model,
            "GEMINI_LOCATION": gemini_location,
            "ALLOW_GITHUB_WRITES": "false",
            "GITHUB_TOKEN": {"secret": secret_id, "version": secret_version},
        },
    }


def verify_operations(remote: Any) -> list[dict[str, Any]]:
    schemas = remote.operation_schemas()
    if not schemas or len(schemas) != 1 or schemas[0].get("name") != "async_query" or schemas[0].get("api_mode") != "async":
        raise ValueError("Remote operation schema does not match the read-only API.")
    return schemas


def check_sdk(config: dict[str, Any]) -> None:
    """Validate against the installed public SDK schema without loading ADC."""
    if importlib.metadata.version("google-cloud-agentplatform") != SDK_VERSION:
        raise ValueError("Install requirements-cloud.txt to use the verified cloud SDK version.")
    from agentplatform import types
    types.AgentRuntimeConfig.model_validate(config)


def make_client(project: str, location: str) -> Any:
    import agentplatform
    return agentplatform.Client(project=project, location=location)


def write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def deploy(config: dict[str, Any], *, project: str, location: str, state_path: Path,
           root: Path = ROOT, client_factory=make_client) -> dict[str, Any]:
    manifest = source_manifest(root)
    state = {"status": "starting", "project": project, "location": location,
             "created_at": datetime.now(timezone.utc).isoformat(), "source_manifest": manifest}
    # Refuse to overwrite an earlier attempt: failures may leave a real resource.
    write_json(state_path, state, exclusive=True)
    try:
        client = client_factory(project, location)
        # Only allowlisted files enter the SDK archive, even if checkout has secrets.
        with tempfile.TemporaryDirectory(prefix="campuspulse-source-") as folder:
            staging = Path(folder)
            for item in manifest:
                target = staging / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / item["path"], target)
            # SDK 2.5.0 archives relative to cwd and rejects files outside it.
            # This function is a CLI operation, not a concurrent web handler.
            source_config = dict(config, source_packages=list(SOURCE_FILES))
            with chdir(staging):
                remote = client.runtimes.create(config=source_config)
        name = remote.api_resource.name
        if not isinstance(name, str) or RESOURCE_PATTERN.fullmatch(name) is None:
            raise ValueError("Deployment returned an invalid reasoningEngines resource name.")
        # Save identity before the readback, so a readback failure is recoverable.
        state.update(status="created", resource_name=name)
        write_json(state_path, state)
        readback = client.runtimes.get(name=name)
        state.update(status="deployed", operation_schemas=verify_operations(readback),
                     invocation_verified=False)
        write_json(state_path, state)
        return state
    except Exception:
        state["status"] = "needs_inspection"
        write_json(state_path, state)
        raise RuntimeError("Deployment outcome needs inspection; check state and cloud operations before retrying.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--github-secret", required=True, help="Secret ID, never its value")
    parser.add_argument("--secret-version", default="1")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--gemini-location", default="global", choices=("global", "us", "eu"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--deploy", action="store_true", help="Create a real billable cloud resource")
    mode.add_argument("--dry-run", action="store_true", help="Default: local plan only, no ADC or cloud calls")
    parser.add_argument("--check-sdk", action="store_true", help="Also validate installed SDK schema locally")
    parser.add_argument("--state", type=Path, default=ROOT / "data" / "cloud-deployment.json")
    args = parser.parse_args(argv)
    try:
        config = build_config(project=args.project, location=args.location,
                              service_account=args.service_account, secret_id=args.github_secret,
                              secret_version=args.secret_version, model=args.model, gemini_location=args.gemini_location)
        manifest = source_manifest()
        if args.check_sdk or args.deploy:
            check_sdk(config)
        if not args.deploy:
            print(json.dumps({"mode": "dry-run", "cloud_contacted": False, "credentials_checked": False,
                              "config": config, "source_manifest": manifest}, indent=2))
            return 0
        state = deploy(config, project=args.project, location=args.location, state_path=args.state)
        print(json.dumps({"status": state["status"], "resource_name": state["resource_name"],
                          "invocation_verified": False, "state_file": str(args.state)}))
        return 0
    except (ValueError, FileExistsError, importlib.metadata.PackageNotFoundError, ImportError, RuntimeError):
        # No arbitrary SDK error text (which can include request content/headers).
        print("Cloud preflight/deployment failed. Check source files, SDK, arguments and deployment state; "
              "inspect cloud operations before retrying a deployment.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
