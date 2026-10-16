"""Invoke the deployed read-only Guardian and save the actual remote response."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campuspulse.models import AnalysisResult, Document  # noqa: E402
from scripts.deploy import RESOURCE_PATTERN, make_client, verify_operations, write_json  # noqa: E402


def resource_parts(name: str) -> tuple[str, str]:
    match = RESOURCE_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError("Use the complete projects/.../locations/.../reasoningEngines/... resource name.")
    return match.group(1), match.group(2)


async def invoke(*, resource_name: str, document: Document, repository_url: str,
                 client_factory=make_client) -> dict[str, Any]:
    project, location = resource_parts(resource_name)
    client = client_factory(project, location)
    remote = client.runtimes.get(name=resource_name)
    verify_operations(remote)
    started = datetime.now(timezone.utc).isoformat()
    response = await remote.async_query(document=document.model_dump(mode="json"), repository_url=repository_url)
    result = AnalysisResult.model_validate(response)
    if not result.live:
        raise ValueError("The remote agent returned an offline result.")
    result_json = result.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(result_json, sort_keys=True).encode("utf-8")).hexdigest()
    return {"status": "remote_response_received", "resource_name": resource_name,
            "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "repository_url": repository_url, "document_sha256": document.sha256,
            "response_sha256": digest, "analysis": result_json}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    resource = parser.add_mutually_exclusive_group(required=True)
    resource.add_argument("--resource")
    resource.add_argument("--state", type=Path)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "cloud-invocation.json")
    args = parser.parse_args(argv)
    try:
        from campuspulse.parsing import parse_document
        from campuspulse.security import validate_repository_url
        name = args.resource
        if args.state:
            name = json.loads(args.state.read_text(encoding="utf-8"))["resource_name"]
        resource_parts(name)
        repository = validate_repository_url(args.repository)
        # Bound bytes before loading user-provided input; parser enforces its own limits too.
        with args.file.open("rb") as stream:
            content = stream.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("Assignment exceeds the upload limit.")
        document = parse_document(content, args.file.name)
        evidence = asyncio.run(invoke(resource_name=name, document=document, repository_url=repository.url))
        write_json(args.output, evidence)
        print(json.dumps({"status": evidence["status"], "resource_name": name,
                          "findings": len(evidence["analysis"]["findings"]),
                          "warnings": len(evidence["analysis"]["warnings"]), "output": str(args.output)}))
        return 0
    except Exception:
        print("Remote invocation failed or returned an invalid analysis. Check resource, runtime permissions, "
              "input and service configuration. No invocation evidence was written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
