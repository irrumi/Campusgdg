"""Custom read-only MCP for public GitHub repositories; no credentials or writes.

This is our MCP server backed by anonymous GitHub REST reads, not GitHub's official
authenticated MCP. Each review pins file reads and CI to one observed commit.
Run: python -m agent.public_github_mcp (stdio).
"""
from __future__ import annotations

import base64
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
from itertools import count
import os
from pathlib import Path
import re
import sys
from typing import Literal
from urllib.parse import quote

from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
import requests

from campuspulse.github_mcp import GitHubMCPError, _path, _ref
from campuspulse.models import Evidence
from campuspulse.security import validate_repository_url

TOOL = "read_public_github"
MAX_BYTES = 384_000
mcp = Server("campuspulse-public-github-readonly")
_evidence_ids = count(1)


def _get(path: str, params: dict | None = None):
    with requests.Session() as http:
        http.trust_env = False  # Fixed public host, direct TLS; never inherit auth/netrc.
        with http.get("https://api.github.com" + path, params=params,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "CampusPulse-public-MCP"},
                      timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f"Public GitHub read returned HTTP {response.status_code}; no evidence collected.")
            body = bytearray()
            for chunk in response.iter_content(16_384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("Public GitHub response exceeds the bounded read limit.")
            return json.loads(body)


def read_public_github(repository_url: str, kind: str, arguments: dict,
                       revision: str = "", max_chars: int = 6000) -> dict:
    """Read repository listing, a UTF-8 file, bounded issues or CI from one public repository.

    The caller binds repository_url. Paths stay in that repository. No credentials,
    private repositories, external URLs, code execution or write operations exist.
    A truncated response is partial evidence, never proof that an implementation is absent.
    """
    repository = validate_repository_url(repository_url)
    if kind not in {"repository", "file", "issues", "ci"} or type(max_chars) is not int or not 500 <= max_chars <= 6000:
        raise ValueError("Invalid bounded read operation.")
    if not isinstance(arguments, dict):
        raise ValueError("Read arguments must be an object.")
    allowed = {"repository": {"ref"}, "file": {"path", "ref", "sha"},
               "issues": {"state", "perPage", "issue_number"}, "ci": {"perPage", "page"}}[kind]
    if set(arguments) - allowed:
        raise ValueError("Unsupported read arguments.")
    base = f"/repos/{repository.owner}/{repository.name}"
    if revision and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Invalid commit identity.")
    requested_ref = arguments.get("sha") or arguments.get("ref")
    if requested_ref:
        _ref(requested_ref)
    if revision and requested_ref and requested_ref not in {revision, "HEAD"}:
        raise ValueError("This review is pinned to its first commit; omit ref/sha.")
    if not revision:
        commit = _get(base + "/commits/" + quote(requested_ref or "HEAD", safe=""))
        revision = commit.get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("GitHub did not return a valid commit identity.")
    source = repository.url + "/tree/" + revision
    if kind in {"repository", "file"}:
        path = _path(arguments.get("path", ""), empty=kind == "repository")
        payload = _get(base + "/contents/" + quote(path, safe="/"), {"ref": revision})
        if isinstance(payload, list):
            content = json.dumps({"entries": [{"path": item.get("path"), "type": item.get("type")}
                                              for item in payload[:50]], "bounded_listing": True}, ensure_ascii=False)
        elif isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("type") == "file":
            content = base64.b64decode("".join(payload.get("content", "").split()), validate=True).decode("utf-8")
            source = repository.url + "/blob/" + revision + "/" + quote(path, safe="/")
        else:
            raise ValueError("GitHub did not return a readable UTF-8 file or directory.")
    else:
        count = arguments.get("perPage", 5)
        page = arguments.get("page", 1)
        if type(count) is not int or not 1 <= count <= 20 or type(page) is not int or not 1 <= page <= 5:
            raise ValueError("Invalid page bounds.")
        if kind == "ci":
            raw = _get(base + "/actions/runs", {"head_sha": revision, "per_page": count, "page": page})
            runs = raw.get("workflow_runs", [])
            payload = {"bounded_listing": True, "head_sha": revision, "workflow_runs": [
                {key: run.get(key) for key in ("id", "name", "status", "conclusion", "head_sha", "head_branch",
                                             "created_at", "updated_at", "html_url")}
                for run in runs[:count]]}
            source = repository.url + "/actions"
        elif "issue_number" in arguments:
            number = arguments["issue_number"]
            if type(number) is not int or not 1 <= number <= 2**31 - 1:
                raise ValueError("Invalid issue identity.")
            raw = _get(base + f"/issues/{number}")
            payload = {key: raw.get(key) for key in ("number", "title", "state", "body")}
            source = repository.url + f"/issues/{number}"
        else:
            state = arguments.get("state", "OPEN")
            if state not in {"OPEN", "CLOSED"}:
                raise ValueError("Invalid issue state.")
            raw = _get(base + "/issues", {"state": state.lower(), "per_page": count})
            payload = {"bounded_listing": True, "issues": [
                {key: item.get(key) for key in ("number", "title", "state")} for item in raw[:count]]}
            source = repository.url + "/issues"
        content = json.dumps(payload, ensure_ascii=False)
    truncated = len(content) > max_chars
    evidence = Evidence(id=f"e{next(_evidence_ids)}", tool=kind, source_url=source, summary=content[:max_chars],
                        data={"content": content[:max_chars], "arguments": arguments,
                              "commit_sha": revision, "truncated": truncated,
                              "transport": "custom-public-github-mcp"})
    return {"commit_sha": revision, "evidence": [evidence.model_dump(mode="json")]}


class _PublicRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository_url: str = Field(max_length=164)
    kind: Literal["repository", "file", "issues", "ci"]
    arguments: dict
    revision: str = Field(default="", max_length=40)
    max_chars: int = Field(default=6000, ge=500, le=6000)


@mcp.list_tools()
async def public_tools():
    return [Tool(name=TOOL, description=read_public_github.__doc__, inputSchema=_PublicRead.model_json_schema(),
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))]


@mcp.call_tool()
async def call_public_tool(name: str, arguments: dict):
    if name != TOOL:
        return CallToolResult(isError=True, content=[TextContent(type="text", text="Undeclared public read operation.")])
    try:
        request = _PublicRead.model_validate(arguments)
        result = await asyncio.to_thread(read_public_github, **request.model_dump())
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structuredContent=result)
    except Exception:
        return CallToolResult(isError=True, content=[TextContent(type="text", text="Public GitHub read failed; no verified evidence.")])


class PublicGitHubGateway:
    """Real MCP client; its child receives neither the gateway key nor provider tokens."""

    def __init__(self):
        self.revision = ""
        self.repository_url = ""
        self.stack = AsyncExitStack()

    async def __aenter__(self):
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}}
        environment["PYTHONIOENCODING"] = "utf-8"
        server = StdioServerParameters(command=sys.executable, args=["-m", "agent.public_github_mcp"],
                                       cwd=str(Path(__file__).resolve().parents[1]), env=environment)
        try:
            streams = await self.stack.enter_async_context(stdio_client(server))
            self.session = await self.stack.enter_async_context(ClientSession(*streams, read_timeout_seconds=timedelta(seconds=60)))
            await self.session.initialize()
            tools = await self.session.list_tools()
            matches = [tool for tool in tools.tools if tool.name == TOOL]
            if len(matches) != 1 or not matches[0].annotations or not matches[0].annotations.readOnlyHint:
                raise GitHubMCPError("Custom public MCP did not advertise its read-only tool.")
            self.schema = matches[0].inputSchema
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args):
        await self.stack.aclose()

    async def collect(self, repository, kind, arguments, *, max_chars=6000):
        if self.repository_url and self.repository_url != repository.url:
            raise GitHubMCPError("A review cannot switch repositories.")
        self.repository_url = repository.url
        params = {"repository_url": repository.url, "kind": kind, "arguments": arguments,
                  "revision": self.revision, "max_chars": max_chars}
        validate(params, self.schema)  # Actual tools/list schema, not a copied signature.
        response = await self.session.call_tool(TOOL, params)
        if response.isError:
            raise GitHubMCPError("Custom public GitHub read failed; no evidence was collected.")
        data = response.structuredContent
        if data is None:
            texts = [part.text for part in response.content if part.type == "text"]
            data = json.loads("".join(texts))
        if len(json.dumps(data)) > 50_000 or not re.fullmatch(r"[a-f0-9]{40}", data.get("commit_sha", "")):
            raise GitHubMCPError("Custom public MCP returned an invalid snapshot.")
        if self.revision and self.revision != data["commit_sha"]:
            raise GitHubMCPError("Custom public MCP changed the reviewed commit.")
        self.revision = data["commit_sha"]
        return [Evidence.model_validate(item) for item in data["evidence"]]


if __name__ == "__main__":
    # Low-level SDK avoids FastMCP's implicit .env settings loader.
    async def serve():
        async with stdio_server() as (read, write):
            await mcp.run(read, write, mcp.create_initialization_options())

    asyncio.run(serve())
