"""Bounded GitHub access through the official remote MCP server only.

The agent-facing collect interface exposes four read operations. Issue creation is
a separate application-only operation; the API must authorize its exact proposal.
The runtime tools/list schema is authoritative, not a copied tool signature.
See https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import re
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncContextManager, Callable
from urllib.parse import quote, urlsplit

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

from campuspulse.config import Settings
from campuspulse.models import Evidence, IssueReceipt, RepositoryRef

OFFICIAL_ENDPOINT = "https://api.githubcopilot.com/mcp/"
MAX_RESPONSE_BYTES = 512_000
MAX_EVIDENCE_CHARS = 16_000
MAX_PAGE_SIZE = 20
_READ_TOOLS = frozenset({"get_file_contents", "list_issues", "issue_read", "actions_list"})
_KNOWN_TOOLS = _READ_TOOLS | {"issue_write"}
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


class GitHubMCPError(RuntimeError):
    """A safe-to-display upstream or MCP contract failure."""


class GitHubConfigurationError(GitHubMCPError):
    """Configuration is missing or points outside the official service."""


class GitHubWriteDisabledError(PermissionError):
    """The application has not enabled GitHub writes."""


class GitHubWriteOutcomeUnknown(GitHubMCPError):
    """A write may have happened. Persist unknown and never blindly retry."""


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _repository(value: RepositoryRef) -> RepositoryRef:
    # Do not trust RepositoryRef itself: shared models intentionally have no URL validator.
    if not isinstance(value, RepositoryRef):
        raise ValueError("A validated repository reference is required.")
    if not _OWNER.fullmatch(value.owner) or not _REPO.fullmatch(value.name):
        raise ValueError("Invalid GitHub repository identity.")
    if value.name in {".", ".."} or "--" in value.owner:
        raise ValueError("Invalid GitHub repository identity.")
    canonical = f"https://github.com/{value.owner}/{value.name}"
    if value.url.rstrip("/").casefold() != canonical.casefold():
        raise ValueError("Repository URL does not match its owner and name.")
    return RepositoryRef(owner=value.owner, name=value.name, url=canonical)


def _path(value: Any, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 1024 or (not value and not empty):
        raise ValueError("A bounded repository-relative path is required.")
    if value and (
        value.startswith("/") or "\\" in value or "%" in value or ":" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Path must stay inside the selected repository.")
    return value


def _ref(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise ValueError("Invalid Git reference.")
    if (value.startswith(("/", "-")) or value.endswith(("/", ".")) or ".." in value
            or "@{" in value or any(c.isspace() or ord(c) < 32 or c in "\\~^:?*[%" for c in value)
            or any(part in {"", ".", ".."} or part.endswith(".lock") for part in value.split("/"))):
        raise ValueError("Invalid Git reference.")
    return value


def _integer(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}.")
    return value


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise GitHubMCPError("GitHub MCP returned invalid JSON data.") from None


def _check_schema(schema: Any) -> Any:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise GitHubMCPError("GitHub MCP advertised an unsupported input schema.")
    if len(_json(schema)) > 64_000:
        raise GitHubMCPError("GitHub MCP advertised an oversized schema.")
    pending = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    raise GitHubMCPError("GitHub MCP schema contains an external reference.")
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        return validator(schema)
    except SchemaError:
        raise GitHubMCPError("GitHub MCP advertised an invalid input schema.") from None


def _payload(result: Any) -> Any:
    """Decode MCP data without fetching resource links or executing repository text."""
    if _field(result, "isError", False):
        raise GitHubMCPError("GitHub MCP could not complete the requested operation.")
    structured = _field(result, "structuredContent")
    blocks = _field(result, "content", [])
    if not isinstance(blocks, list) or len(blocks) > 256:
        raise GitHubMCPError("GitHub MCP returned invalid content blocks.")
    # Check the complete response, including content duplicated by structuredContent.
    raw = {"structuredContent": _plain(structured), "content": [_plain(b) for b in blocks]}
    if len(_json(raw).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise GitHubMCPError("GitHub MCP response exceeded the size limit.")
    if structured is not None:
        return structured
    decoded: list[Any] = []
    for block in blocks:
        kind = _field(block, "type")
        if kind == "text":
            text = _field(block, "text")
            if not isinstance(text, str):
                raise GitHubMCPError("GitHub MCP returned invalid text content.")
            try:
                decoded.append(json.loads(text))
            except (ValueError, RecursionError):
                decoded.append({"text": text})
        elif kind == "resource":
            resource = _field(block, "resource", {})
            text = _field(resource, "text")
            blob = _field(resource, "blob")
            if text is None and isinstance(blob, str):
                try:
                    text = base64.b64decode(blob, validate=True).decode("utf-8")
                except (ValueError, binascii.Error, UnicodeDecodeError):
                    text = None
            decoded.append({"text": text, "content_available": isinstance(text, str),
                            "uri": str(_field(resource, "uri", ""))})
        elif kind == "resource_link":
            decoded.append({"uri": str(_field(block, "uri", "")),
                            "content_available": False, "note": "Resource link was not fetched."})
        else:
            decoded.append({"content_available": False, "note": "Non-text content omitted."})
    if not decoded:
        raise GitHubMCPError("GitHub MCP returned no evidence.")
    return decoded[0] if len(decoded) == 1 else decoded


def _file_payload(payload: Any) -> Any:
    """Expose UTF-8 file bytes as data for Gemini, preserving non-text limitations."""
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("content"), list):
        return {**payload, "content": _payload({"content": payload["content"]})}
    if payload.get("encoding") == "base64" and isinstance(payload.get("content"), str):
        try:
            encoded = "".join(payload["content"].split())
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return {**payload, "content": None, "content_available": False,
                    "note": "File content is not valid base64 UTF-8 text."}
        return {**payload, "content": decoded, "encoding": "utf-8", "content_available": True}
    return payload


@asynccontextmanager
async def _official_session(settings: Settings, *, read_only: bool):
    # Lazy import keeps local parsing usable when service dependencies are unavailable.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = settings.github_token.get_secret_value() if settings.github_token else ""
    if not token or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise GitHubConfigurationError("Configure a valid server-side GITHUB_TOKEN for GitHub MCP.")
    headers = {"Authorization": f"Bearer {token}",
               "X-MCP-Toolsets": "repos,issues,actions" if read_only else "issues",
               "X-MCP-Readonly": "true" if read_only else "false"}
    timeout = timedelta(seconds=settings.request_timeout_seconds)
    async with streamablehttp_client(OFFICIAL_ENDPOINT, headers=headers, timeout=timeout,
                                     sse_read_timeout=timeout) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            yield session


class GitHubGateway:
    """Async MCP gateway. Tests may explicitly inject ``session_factory``.

    A factory accepts keyword ``read_only`` and returns an async context manager
    yielding a session with initialize(), list_tools(cursor=...), and call_tool().
    It never bypasses argument validation, discovery, or write settings.
    """

    def __init__(self, settings: Settings, *,
                 session_factory: Callable[..., AsyncContextManager[Any]] | None = None):
        self.settings = settings
        self._factory = session_factory
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[bool, Any] = {}
        self._tools: dict[bool, dict[str, tuple[Any, Any]]] = {}
        self._expected: dict[tuple[str, int], tuple[str, str]] = {}

    async def __aenter__(self) -> GitHubGateway:
        if self._stack is not None:
            raise GitHubMCPError("GitHub gateway is already open.")
        if self.settings.github_mcp_url != OFFICIAL_ENDPOINT:
            raise GitHubConfigurationError("Only the official GitHub MCP endpoint is supported.")
        timeout = self.settings.request_timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
            raise GitHubConfigurationError("GitHub MCP timeout must be between 0 and 300 seconds.")
        self._stack = AsyncExitStack()
        try:
            await self._open(read_only=True)
        except BaseException:
            await self._close()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._close()

    async def _close(self) -> None:
        stack, self._stack = self._stack, None
        self._sessions.clear()
        self._tools.clear()
        self._expected.clear()
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                raise GitHubMCPError("GitHub MCP connection could not close cleanly.") from None

    async def _open(self, *, read_only: bool) -> Any:
        if self._stack is None:
            raise GitHubMCPError("Use GitHubGateway inside an async context manager.")
        if read_only in self._sessions:
            return self._sessions[read_only]
        try:
            manager = (self._factory(read_only=read_only) if self._factory is not None
                       else _official_session(self.settings, read_only=read_only))
            session = await self._stack.enter_async_context(manager)
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await session.initialize()
                tools = await self._discover(session, read_only=read_only)
        except GitHubMCPError:
            raise
        except Exception:
            raise GitHubMCPError("Could not connect to the official GitHub MCP server.") from None
        self._sessions[read_only] = session
        self._tools[read_only] = tools
        return session

    async def _discover(self, session: Any, *, read_only: bool) -> dict[str, tuple[Any, Any]]:
        discovered: dict[str, tuple[Any, Any]] = {}
        cursor = None
        seen: set[str] = set()
        count = 0
        for _ in range(16):
            response = await session.list_tools() if cursor is None else await session.list_tools(cursor=cursor)
            listed = _field(response, "tools")
            if not isinstance(listed, list):
                raise GitHubMCPError("GitHub MCP tool discovery returned an invalid response.")
            count += len(listed)
            if count > 512:
                raise GitHubMCPError("GitHub MCP tool discovery exceeded the size limit.")
            for tool in listed:
                name = _field(tool, "name")
                if name not in _KNOWN_TOOLS or (read_only and name not in _READ_TOOLS):
                    continue
                if name in discovered:
                    raise GitHubMCPError("GitHub MCP advertised duplicate tools.")
                hint = _field(_field(tool, "annotations", {}), "readOnlyHint")
                if name in _READ_TOOLS and hint is not True:
                    raise GitHubMCPError("GitHub MCP did not identify a required tool as read-only.")
                discovered[name] = (tool, _check_schema(_field(tool, "inputSchema")))
            cursor = _field(response, "nextCursor")
            if cursor is None:
                return discovered
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                raise GitHubMCPError("GitHub MCP tool discovery returned an invalid cursor.")
            seen.add(cursor)
        raise GitHubMCPError("GitHub MCP tool discovery exceeded the page limit.")

    def _validate(self, read_only: bool, name: str, arguments: dict[str, Any]) -> None:
        if name not in self._tools.get(read_only, {}):
            raise GitHubMCPError("The required GitHub MCP tool is unavailable.")
        tool, validator = self._tools[read_only][name]
        properties = _field(tool, "inputSchema").get("properties", {})
        if not isinstance(properties, dict) or set(arguments) - properties.keys():
            raise GitHubMCPError("GitHub MCP tool schema is incompatible with this operation.")
        try:
            validator.validate(arguments)
        except Exception as exc:
            if isinstance(exc, (ValidationError, SchemaError)):
                raise GitHubMCPError("Arguments do not satisfy the discovered GitHub MCP schema.") from None
            raise GitHubMCPError("GitHub MCP tool schema could not be validated.") from None

    async def _call(self, name: str, arguments: dict[str, Any], *, write: bool = False) -> Any:
        if write:
            if name != "issue_write" or arguments.get("method") != "create":
                raise GitHubWriteDisabledError("Only approved issue creation is supported.")
            if not self.settings.allow_github_writes:
                raise GitHubWriteDisabledError("GitHub writes are disabled.")
        elif name not in _READ_TOOLS:
            raise GitHubWriteDisabledError("Write tools are not available to evidence collection.")
        session = await self._open(read_only=not write)
        self._validate(not write, name, arguments)
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                result = await session.call_tool(name, arguments=arguments)
            return _payload(result)
        except Exception:
            if write:
                raise GitHubWriteOutcomeUnknown(
                    "Issue creation outcome is unknown; verify on GitHub before any new attempt."
                ) from None
            raise GitHubMCPError("GitHub MCP could not return valid evidence for this operation.") from None

    async def collect(self, repository: RepositoryRef, tool: str,
                      arguments: dict[str, Any]) -> list[Evidence]:
        """Read repository root, a file, issues, or CI; caller cannot select raw MCP tools.

        repository: ref?; file: path, ref?, sha?; issues: state?, perPage?,
        issue_number?; ci: perPage?, page?, resource_id? (workflow ID/file name).
        Results are bounded previews and list operations never prove global absence.
        """
        repository = _repository(repository)
        allowed = {"repository": {"ref"}, "file": {"path", "ref", "sha"},
                   "issues": {"state", "perPage", "issue_number"},
                   "ci": {"perPage", "page", "resource_id"}}
        if tool not in allowed or not isinstance(arguments, dict) or set(arguments) - allowed[tool]:
            raise ValueError("Unsupported evidence tool or arguments.")
        params: dict[str, Any] = {"owner": repository.owner, "repo": repository.name}
        source = repository.url
        if tool in {"repository", "file"}:
            name = "get_file_contents"
            params["path"] = "" if tool == "repository" else _path(arguments.get("path"))
            if "ref" in arguments:
                params["ref"] = _ref(arguments["ref"])
            if "sha" in arguments:
                sha = arguments["sha"]
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                    raise ValueError("A full commit SHA is required.")
                if "ref" in arguments:
                    raise ValueError("Specify either ref or sha, not both.")
                params["sha"] = sha
            if tool == "file":
                ref = params.get("sha", params.get("ref", "HEAD"))
                source += f"/blob/{quote(ref, safe='')}/{quote(params['path'], safe='/')}"
        elif tool == "issues":
            source += "/issues"
            if "issue_number" in arguments:
                if set(arguments) != {"issue_number"}:
                    raise ValueError("Issue detail reads only accept issue_number.")
                number = _integer(arguments["issue_number"], "issue_number", 2**31 - 1)
                name = "issue_read"
                params.update(method="get", issue_number=number)
                source += f"/{number}"
            else:
                name = "list_issues"
                params["perPage"] = _integer(arguments.get("perPage", MAX_PAGE_SIZE), "perPage", MAX_PAGE_SIZE)
                if "state" in arguments:
                    state = arguments["state"]
                    if state not in {"OPEN", "CLOSED", "open", "closed"}:
                        raise ValueError("Issue state must be OPEN or CLOSED.")
                    params["state"] = state.upper()
        else:
            name = "actions_list"
            params.update(method="list_workflow_runs",
                          perPage=_integer(arguments.get("perPage", MAX_PAGE_SIZE), "perPage", MAX_PAGE_SIZE),
                          page=_integer(arguments.get("page", 1), "page", 5))
            if "resource_id" in arguments:
                params["resource_id"] = _path(arguments["resource_id"])
            source += "/actions"
        payload = await self._call(name, params)
        if name == "get_file_contents":
            payload = _file_payload(payload)
        rendered = _json(payload)
        truncated = len(rendered) > MAX_EVIDENCE_CHARS
        preview = rendered[:MAX_EVIDENCE_CHARS]
        summary = preview + ("\n[Evidence truncated; missing text is not evidence of absence.]" if truncated else "")
        return [Evidence(tool=tool, source_url=source, summary=summary,
                         data={"mcp_tool": name, "arguments": params, "truncated": truncated,
                               "bounded_read": True, "content": preview})]

    async def create_issue(self, repository: RepositoryRef, title: str, body: str) -> IssueReceipt:
        """Execute one already-approved write. Never retries or marks it verified."""
        if not self.settings.allow_github_writes:
            raise GitHubWriteDisabledError("GitHub writes are disabled.")
        repository = _repository(repository)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Issue title must contain between 1 and 200 characters.")
        if not isinstance(body, str) or not body.strip() or len(body) > 20_000:
            raise ValueError("Issue body must contain between 1 and 20000 characters.")
        if "\x00" in title + body or "\n" in title or "\r" in title:
            raise ValueError("Issue content contains invalid characters.")
        payload = await self._call("issue_write", {"method": "create", "owner": repository.owner,
                                  "repo": repository.name, "title": title, "body": body}, write=True)
        try:
            issue = self._issue_object(payload, method="create")
            number, url = self._issue_identity(repository, issue)
            if "title" in issue and issue["title"] != title:
                raise GitHubMCPError("Created issue title does not match the approved proposal.")
            if "body" in issue and issue["body"] != body:
                raise GitHubMCPError("Created issue body does not match the approved proposal.")
        except Exception:
            raise GitHubWriteOutcomeUnknown(
                "Issue creation returned an unverified identity; verify on GitHub before any new attempt."
            ) from None
        self._expected[(repository.url.casefold(), number)] = (title, body)
        return IssueReceipt(number=number, url=url, title=title, body=body, verified=False)

    @staticmethod
    def _issue_object(payload: Any, *, method: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GitHubMCPError("GitHub MCP returned an invalid issue response.")
        if "method" in payload and payload["method"] != method:
            raise GitHubMCPError("GitHub MCP returned a different issue operation.")
        # Both modern issue_read and issue_write wrap their DTO in issue.
        if isinstance(payload.get("issue"), dict):
            return payload["issue"]
        return payload

    @staticmethod
    def _issue_identity(repository: RepositoryRef, issue: dict[str, Any]) -> tuple[int, str]:
        url = issue.get("html_url") or issue.get("url")
        if not isinstance(url, str) or len(url) > 2048:
            raise GitHubMCPError("GitHub MCP did not return an issue URL.")
        try:
            parts = urlsplit(url)
        except ValueError:
            raise GitHubMCPError("GitHub MCP returned an invalid issue URL.") from None
        prefix = f"/{repository.owner}/{repository.name}/issues/"
        if (parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment
                or not parts.path.casefold().startswith(prefix.casefold())):
            raise GitHubMCPError("Issue response does not belong to the selected repository.")
        suffix = parts.path[len(prefix):]
        if not re.fullmatch(r"[1-9][0-9]{0,9}", suffix):
            raise GitHubMCPError("GitHub MCP returned an invalid issue number.")
        number = int(suffix)
        if "number" in issue and (type(issue["number"]) is not int or issue["number"] != number):
            raise GitHubMCPError("Issue number and URL do not agree.")
        return number, f"{repository.url}/issues/{number}"

    async def verify_issue(self, repository: RepositoryRef, number: int) -> IssueReceipt:
        repository = _repository(repository)
        number = _integer(number, "issue_number", 2**31 - 1)
        payload = await self._call("issue_read", {"method": "get", "owner": repository.owner,
                                  "repo": repository.name, "issue_number": number})
        issue = self._issue_object(payload, method="get")
        actual, url = self._issue_identity(repository, issue)
        title, body = issue.get("title"), issue.get("body")
        if actual != number or "number" not in issue or not isinstance(title, str) or not isinstance(body, str):
            raise GitHubMCPError("GitHub MCP issue readback is incomplete or does not match its identity.")
        expected = self._expected.get((repository.url.casefold(), number))
        if expected is not None and (title, body) != expected:
            raise GitHubMCPError("Issue readback does not match the approved title and body.")
        return IssueReceipt(number=number, url=url, title=title, body=body, verified=True)
