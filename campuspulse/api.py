"""Local HTTP interface and the sole application path for approved GitHub writes."""
from __future__ import annotations

import asyncio
import hmac
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, StrictBool, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from campuspulse.config import Settings
from campuspulse.github_mcp import GitHubConfigurationError, GitHubGateway, GitHubWriteDisabledError
from campuspulse.models import ActionProposal, Assignment, AuditEvent, IssueReceipt, Model, ProposedAction
from campuspulse.parsing import MAX_UPLOAD_BYTES, DocumentLimitError, DocumentParseError, parse_document
from campuspulse.security import proposal_hash, safe_error, validate_repository_url
from campuspulse.storage import Store


class ProposalRequest(Model):
    requirement_id: str = Field(min_length=1, max_length=200)


class ApprovalRequest(Model):
    proposal_hash: str = Field(min_length=1, max_length=128)
    approved: StrictBool

    @field_validator("approved")
    @classmethod
    def explicit_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit approval is required")
        return value


class UploadResponse(Model):
    assignment: Assignment
    token: str


class APIError(HTTPException):
    """Only fixed application-authored messages may be returned verbatim."""


def _log_analysis_failure(exc: Exception) -> None:
    """Record bounded error categories, never exception text, requests or secrets."""
    categories = []
    current: BaseException | None = exc
    for _ in range(5):
        if current is None:
            break
        code = getattr(current, "code", None)
        categories.append((type(current).__name__, code if type(code) is int else None))
        current = current.__context__
    logging.getLogger(__name__).warning("Analysis failed: %s", categories)


class FrontendFiles(StaticFiles):
    """Serve JavaScript consistently even when Windows registers it as plain text."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, FileResponse) and Path(path).suffix.lower() in {".js", ".mjs"}:
            response.media_type = "text/javascript"
            response.headers["content-type"] = "text/javascript; charset=utf-8"
        return response


def _upload_limit(settings: Settings) -> int:
    return max(0, min(settings.max_upload_bytes, MAX_UPLOAD_BYTES))


def _same_origin(origin: str, scheme: str, host: str) -> bool:
    """Compare full browser origins; localhost ports are separate origins too."""
    try:
        supplied, expected = urlsplit(origin), urlsplit(f"{scheme}://{host}")
        return (
            supplied.scheme in {"http", "https"}
            and supplied.scheme == expected.scheme
            and supplied.hostname == expected.hostname
            and (supplied.port or (443 if supplied.scheme == "https" else 80))
            == (expected.port or (443 if expected.scheme == "https" else 80))
            and supplied.username is None
            and supplied.password is None
            and not supplied.path
            and not supplied.query
            and not supplied.fragment
            and bool(supplied.hostname)
            and not any(char.isspace() for char in origin)
        )
    except ValueError:
        return False


class RequestGuard:
    """Bound bodies before multipart spooling and reject cross-origin mutations."""

    def __init__(self, app, settings_factory):
        self.app = app
        self.settings_factory = settings_factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        headers = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value.decode("latin-1"))
        origins = headers.get(b"origin", [])
        hosts = headers.get(b"host", [])
        try:
            host = urlsplit(f"http://{hosts[0]}") if len(hosts) == 1 else None
            hostname = host.hostname if host else None
            # Accessing port rejects invalid port syntax before it can reach Origin checks.
            valid_host = host is not None and (host.port is None or 0 < host.port < 65536)
            valid_host = valid_host and not (host.username or host.password or host.path or host.query or host.fragment)
            valid_host = valid_host and not any(char.isspace() for char in hosts[0])
        except ValueError:
            hostname, valid_host = None, False
        allowed_hosts = {item.casefold().strip("[]") for item in self.settings_factory().allowed_hosts}
        if not valid_host or hostname not in allowed_hosts:
            await JSONResponse({"detail": "This service requires a configured trusted host."}, status_code=400)(
                scope, receive, guarded_send
            )
            return
        if scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, guarded_send)
            return
        if (
            len(origins) > 1
            or (origins and (len(hosts) != 1 or not _same_origin(origins[0], scope["scheme"], hosts[0])))
            or headers.get(b"sec-fetch-site") == ["cross-site"]
        ):
            await JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=400)(
                scope, receive, guarded_send
            )
            return
        is_upload = scope["path"].rstrip("/") == "/api/assignments"
        # Multipart framing gets a small separate budget. The file has its own exact limit.
        limit = _upload_limit(self.settings_factory()) + 65_536 if is_upload else 16_384
        lengths = headers.get(b"content-length", [])
        try:
            if len(lengths) > 1 or (lengths and (int(lengths[0]) < 0 or int(lengths[0]) > limit)):
                raise ValueError
        except ValueError:
            await JSONResponse({"detail": "Request body exceeds the size limit."}, status_code=413)(
                scope, receive, guarded_send
            )
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await JSONResponse({"detail": "Request body exceeds the size limit."}, status_code=413)(
                    scope, receive, guarded_send
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, guarded_send)


def _draft(assignment: Assignment, requirement_id: str) -> ActionProposal:
    analysis = assignment.analysis
    if analysis is None:
        raise APIError(409, "Analyze the assignment before proposing an Issue.")
    requirements = [item for item in analysis.requirements if item.id == requirement_id]
    findings = [item for item in analysis.findings if item.requirement_id == requirement_id]
    if len(requirements) != 1 or len(findings) != 1:
        raise APIError(409, "A unique analyzed requirement and finding are required.")
    requirement, finding = requirements[0], findings[0]
    if not finding.recommendation or not finding.recommendation.strip():
        raise APIError(409, "This finding has no recommended action.")
    title = "Assignment: " + " ".join(requirement.title.split())
    quote = "\n".join(f"> {line}" for line in requirement.source.quote.splitlines())
    evidence = {item.id: item for item in analysis.evidence}
    sources = "\n".join(
        f"- {evidence[item].source_url}" for item in finding.evidence_ids if item in evidence
    ) or "No repository evidence was available; verify the finding before implementing changes."
    body = (
        f"## Requirement\n{requirement.description}\n\n"
        f"## Assignment source\n{assignment.document.filename}, page/paragraph {requirement.source.page}\n"
        f"{quote}\n\n## Finding\nStatus: {finding.status.value}\n{finding.explanation}\n\n"
        f"## Recommended action\n{finding.recommendation}\n\n## Repository evidence\n{sources}"
    )
    if len(body) > 20_000 or "\x00" in title + body:
        raise APIError(409, "This recommendation cannot fit a valid Issue proposal.")
    return ActionProposal(requirement_id=requirement_id, title=title[:200], body=body)


def _receipt_matches(action: ProposedAction, receipt: IssueReceipt, number: int) -> bool:
    return (
        receipt.verified is True
        and receipt.number == number
        and receipt.url == f"{action.repository.url}/issues/{number}"
        and receipt.title == action.title
        and receipt.body == action.body
    )


def create_app(settings=None, store=None, agent=None, gateway_factory=None) -> FastAPI:
    """Construct an app; injectable services are explicit test seams, never demo fallbacks."""
    supplied_store, supplied_agent = store, agent

    def runtime_settings() -> Settings:
        nonlocal settings
        if settings is None:
            settings = Settings()
        return settings

    def runtime_store():
        nonlocal store
        if store is None:
            store = Store(runtime_settings().database_path)
        return store

    def timeout() -> float:
        seconds = runtime_settings().request_timeout_seconds
        if not math.isfinite(seconds) or not 0 < seconds <= 300:
            raise APIError(503, "Request timeout must be between 0 and 300 seconds.")
        return seconds

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            if supplied_store is None and store is not None:
                store.close()

    application = FastAPI(title="CampusPulse Assignment Guardian", lifespan=lifespan)
    application.add_middleware(RequestGuard, settings_factory=runtime_settings)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "Request validation failed."}, status_code=422)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        # Framework/parser errors can contain untrusted input; return only our fixed messages.
        detail = exc.detail if isinstance(exc, APIError) else "The request could not be processed."
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"detail": safe_error(exc)}, status_code=502)

    def token_header(value: Annotated[str | None, Header(alias="X-Assignment-Token")] = None) -> str:
        if not value or len(value) > 512:
            raise APIError(404, "Resource not found.")
        return value


    def load_assignment(assignment_id: str, token: str) -> Assignment:
        try:
            return runtime_store().get_assignment(assignment_id, token)
        except KeyError:
            raise APIError(404, "Resource not found.") from None

    def event(assignment_id: str, token: str, kind: str, message: str):
        runtime_store().add_event(assignment_id, token, kind, message)

    @application.get("/api/health")
    async def health():
        config = runtime_settings()
        return {
            "status": "ok",
            "mode": "live" if config.gemini_configured and config.github_configured else "unconfigured",
            "gemini_configured": config.gemini_configured,
            "github_configured": config.github_configured,
            "allow_github_writes": config.allow_github_writes,
        }

    @application.post("/api/assignments", response_model=UploadResponse)
    async def upload(request: Request):
        try:
            async with request.form(max_files=1, max_fields=1, max_part_size=8192) as form:
                file, repository_url = form.get("file"), form.get("repository_url")
                if (set(form) != {"file", "repository_url"} or len(form.multi_items()) != 2
                        or not isinstance(file, UploadFile) or not isinstance(repository_url, str)):
                    raise APIError(422, "Provide one file and one repository_url.")
                try:
                    repository = validate_repository_url(repository_url)
                except ValueError:
                    raise APIError(400, "Provide a valid HTTPS github.com owner/repository URL.") from None
                content = await file.read(_upload_limit(runtime_settings()) + 1)
                if len(content) > _upload_limit(runtime_settings()):
                    raise APIError(413, "Assignment file exceeds the upload limit.")
                document = await run_in_threadpool(parse_document, content, file.filename or "")
        except DocumentLimitError:
            raise APIError(413, "Assignment document exceeds supported limits.") from None
        except DocumentParseError:
            raise APIError(400, "Cannot parse this assignment. Use a text-based PDF, DOCX, TXT or MD file.") from None
        assignment, token = runtime_store().create_assignment(document, repository)
        event(assignment.id, token, "upload", "Assignment parsed locally; no remote tools were called.")
        return UploadResponse(assignment=assignment, token=token)

    @application.get("/api/assignments/{assignment_id}", response_model=Assignment)
    async def assignment(assignment_id: str, token: str = Depends(token_header)):
        return load_assignment(assignment_id, token)

    @application.post("/api/assignments/{assignment_id}/analyze", response_model=Assignment)
    async def analyze(assignment_id: str, token: str = Depends(token_header)):
        nonlocal agent
        current = load_assignment(assignment_id, token)
        config = runtime_settings()
        if supplied_agent is None and not (config.gemini_configured and config.github_configured):
            raise APIError(503, "Configure Gemini and GitHub MCP before analyzing assignments.")
        from campuspulse.agent import AgentConfigurationError, AgentRateLimitError, GuardianAgent
        from campuspulse.extraction import ExtractionConfigurationError, ExtractionRateLimitError

        deadline = timeout()
        event(assignment_id, token, "analysis_started", "Repository analysis requested.")
        try:
            if agent is None:
                agent = GuardianAgent(config, gateway_factory=gateway_factory)
            async with asyncio.timeout(deadline):
                result = await agent.analyze(current.document, current.repository)
            saved = runtime_store().save_analysis(assignment_id, token, result)
        except (AgentRateLimitError, ExtractionRateLimitError) as exc:
            _log_analysis_failure(exc)
            event(assignment_id, token, "analysis_failed", safe_error(exc))
            raise APIError(429, safe_error(exc)) from None
        except (AgentConfigurationError, ExtractionConfigurationError, GitHubConfigurationError) as exc:
            _log_analysis_failure(exc)
            event(assignment_id, token, "analysis_failed", safe_error(exc))
            raise APIError(503, safe_error(exc)) from None
        except Exception as exc:
            _log_analysis_failure(exc)
            event(assignment_id, token, "analysis_failed", safe_error(exc))
            raise APIError(502, safe_error(exc)) from None
        event(assignment_id, token, "analysis_completed", "Analysis saved with its source references and evidence.")
        return saved

    @application.post("/api/assignments/{assignment_id}/actions", response_model=ProposedAction)
    async def propose(assignment_id: str, payload: ProposalRequest, token: str = Depends(token_header)):
        current = load_assignment(assignment_id, token)
        draft = _draft(current, payload.requirement_id)
        try:
            action = runtime_store().propose_action(assignment_id, token, draft)
        except ValueError:
            raise APIError(409, "The finding cannot be proposed in its current state.") from None
        event(assignment_id, token, "action_proposed", f"Issue proposal {action.id} saved; approval is required.")
        return action

    @application.post(
        "/api/assignments/{assignment_id}/actions/{action_id}/approve", response_model=ProposedAction
    )
    async def approve(assignment_id: str, action_id: str, payload: ApprovalRequest, token: str = Depends(token_header)):
        load_assignment(assignment_id, token)
        try:
            action = runtime_store().get_action(assignment_id, token, action_id)
        except KeyError:
            raise APIError(404, "Resource not found.") from None
        supplied_hash = payload.proposal_hash.encode("utf-8")
        expected_hash = proposal_hash(action.repository, action.title, action.body).encode("ascii")
        stored_hash = action.proposal_hash.encode("utf-8")
        if not (hmac.compare_digest(stored_hash, supplied_hash) and hmac.compare_digest(stored_hash, expected_hash)):
            event(assignment_id, token, "approval_rejected", f"Proposal {action.id}: approval hash mismatch.")
            raise APIError(409, "Approval does not match the immutable Issue proposal.")
        if action.status != "pending":
            event(assignment_id, token, "approval_rejected", f"Proposal {action.id}: action already claimed.")
            raise APIError(409, "This action has already been claimed and cannot be retried.")
        config = runtime_settings()
        if not config.allow_github_writes:
            event(assignment_id, token, "approval_rejected", f"Proposal {action.id}: writes are disabled.")
            raise APIError(409, "GitHub writes are disabled.")
        if gateway_factory is None and not config.github_configured:
            event(assignment_id, token, "approval_rejected", f"Proposal {action.id}: GitHub is not configured.")
            raise APIError(503, "Configure GitHub MCP before approving Issues.")
        deadline = timeout()
        try:
            action = runtime_store().claim_action(assignment_id, token, action_id, payload.proposal_hash)
        except KeyError:
            raise APIError(404, "Resource not found.") from None
        except ValueError:
            raise APIError(409, "This proposal has changed or has already been claimed.") from None
        attempted = False
        receipt = None
        try:
            event(assignment_id, token, "action_approved", f"Exact Issue proposal {action.id} approved and claimed.")
            async with asyncio.timeout(deadline):
                async with (gateway_factory or GitHubGateway)(config) as gateway:
                    attempted = True
                    created = await gateway.create_issue(action.repository, action.title, action.body)
                    receipt = await gateway.verify_issue(action.repository, created.number)
                    if not _receipt_matches(action, receipt, created.number):
                        raise ValueError("Issue readback does not match the approved proposal")
        except BaseException as exc:
            # After invocation, a timeout, cancellation, malformed receipt, or provider
            # error may hide a committed write. Preserve unknown; never execute again.
            state = "unknown" if attempted else "failed"
            message = (
                "GitHub write outcome is unknown. Inspect GitHub before proposing another action."
                if attempted else safe_error(exc)
            )
            runtime_store().finish_action(assignment_id, token, action_id, state, error=message)
            event(assignment_id, token, f"action_{state}", f"Issue proposal {action.id}: {message}")
            if isinstance(exc, asyncio.CancelledError) or not isinstance(exc, Exception):
                raise
            code = 503 if isinstance(exc, (GitHubConfigurationError, GitHubWriteDisabledError)) else 502
            raise APIError(code, message) from None
        completed = runtime_store().finish_action(assignment_id, token, action_id, "succeeded", receipt=receipt)
        event(assignment_id, token, "action_succeeded", f"Issue proposal {action.id} created and read back exactly.")
        return completed

    @application.get("/api/assignments/{assignment_id}/events", response_model=list[AuditEvent])
    async def events(assignment_id: str, token: str = Depends(token_header)):
        load_assignment(assignment_id, token)
        return runtime_store().list_events(assignment_id, token)

    static = Path(__file__).with_name("static")
    application.mount("/static", FrontendFiles(directory=static, check_dir=False), name="static")

    @application.get("/", include_in_schema=False)
    async def index():
        path = static / "index.html"
        if not path.is_file():
            raise APIError(503, "The CampusPulse interface is not installed.")
        return FileResponse(path, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    return application


app = create_app()
