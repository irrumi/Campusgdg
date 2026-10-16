"""Read-only official GitHub MCP smoke check. Credentials stay in the environment."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from campuspulse.config import Settings
from campuspulse.github_mcp import GitHubGateway
from campuspulse.models import RepositoryRef


async def main():
    settings = Settings(_env_file=None)
    repository = RepositoryRef(owner="irrumi", name="Campusgdg", url="https://github.com/irrumi/Campusgdg")
    checks = []
    async with GitHubGateway(settings) as gateway:
        for kind, arguments in [("repository", {}), ("file", {"path": "README.md"}),
                                ("issues", {"perPage": 2}), ("ci", {"perPage": 2})]:
            try:
                evidence = await gateway.collect(repository, kind, arguments)
                checks.append({"kind": kind, "success": True, "evidence_count": len(evidence),
                               "sources": [item.source_url for item in evidence],
                               "mcp_tools": [item.data.get("mcp_tool") for item in evidence]})
            except Exception as exc:
                checks.append({"kind": kind, "success": False, "error_type": type(exc).__name__})
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "transport": "official GitHub MCP",
              "repository": repository.url, "read_only": True, "checks": checks}
    Path("artifacts/live-github-mcp.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not all(item["success"] for item in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
