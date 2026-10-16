"""Small, deterministic trust-boundary helpers with no provider/client imports."""
from __future__ import annotations

import hashlib
import json
import re

from .models import RepositoryRef

_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/"
    r"([A-Za-z0-9_.-]{1,104})/?",
    re.ASCII | re.IGNORECASE,
)
_INVALID_REPOSITORY = "Use a valid HTTPS GitHub repository URL (https://github.com/owner/repo)."
_GENERIC_ERROR = "The request could not be completed."


class RepositoryValidationError(ValueError):
    """The supplied URL is outside the supported GitHub repository syntax."""


def validate_repository_url(value: str) -> RepositoryRef:
    """Accept one HTTPS github.com repository, never an arbitrary fetch target.

    Scheme/host case, one final slash, and one clone suffix are normalized. Owner
    and repository case is preserved. Inputs are never stripped, decoded, or
    URL-normalized before validation, so invisible characters and encoded path
    separators cannot be silently removed by a permissive URL parser.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 164:
        raise RepositoryValidationError(_INVALID_REPOSITORY)
    match = _URL.fullmatch(value)
    if match is None:
        raise RepositoryValidationError(_INVALID_REPOSITORY)
    owner, name = match.groups()
    if name.endswith(".git"):
        name = name[:-4]
    if ("--" in owner or not name or len(name) > 100 or name in {".", ".."}
            or name.endswith(".git")):
        raise RepositoryValidationError(_INVALID_REPOSITORY)
    return RepositoryRef(owner=owner, name=name, url=f"https://github.com/{owner}/{name}")


def proposal_hash(repository: RepositoryRef, title: str, body: str) -> str:
    """Bind the exact repository, title, and body using storage's canonical format.

    Do not strip whitespace, normalize Unicode, change case, or revalidate the
    stored repository here: approval must bind precisely what was displayed.
    This digest is a content identifier, not an authentication token/signature.
    """
    content = json.dumps(
        {"repository": repository.model_dump(mode="json"), "title": title, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Fully qualified exception types select fixed text only. This is deliberately
# not an authorization/retry decision. Provider imports here would create cycles
# (agent/API/cloud import security) and could make error handling itself fail
# when an optional provider dependency is absent. Never inspect exception text,
# attributes, request/response objects, causes, notes, or exception-group members.
_ERROR_MESSAGES: dict[tuple[str, str], str] = {
    (__name__, "RepositoryValidationError"): _INVALID_REPOSITORY,
    ("campuspulse.parsing", "DocumentLimitError"):
        "The document exceeds the supported processing limits.",
    ("campuspulse.parsing", "DocumentParseError"):
        "The document could not be parsed. Upload a supported, unencrypted document containing text.",
    ("campuspulse.extraction", "ExtractionConfigurationError"): "Gemini is not configured.",
    ("campuspulse.extraction", "ExtractionError"):
        "Assignment requirements could not be extracted. Check the document or try again later.",
    ("campuspulse.github_mcp", "GitHubConfigurationError"): "GitHub access is not configured.",
    ("campuspulse.github_mcp", "GitHubWriteDisabledError"): "GitHub Issue creation is disabled.",
    ("campuspulse.github_mcp", "GitHubWriteOutcomeUnknown"):
        "The GitHub Issue creation outcome is unknown. Inspect GitHub before making a new proposal.",
    ("campuspulse.github_mcp", "GitHubMCPError"): "GitHub could not complete the request.",
    ("campuspulse.agent", "AgentConfigurationError"): "Analysis services are not configured.",
    ("campuspulse.agent", "AgentError"): "Repository analysis could not be completed.",
    ("campuspulse.cloud", "CloudConfigurationError"): "Cloud analysis is not configured.",
}


def safe_error(exc: BaseException) -> str:
    """Return a stable public message without ever rendering the exception.

    Even application-defined exceptions may accidentally contain a provider
    token. Unknown types and exception groups receive the same generic message.
    HTTP status selection belongs to the caller, using actual exception types.
    """
    for exception_type in type(exc).__mro__:
        message = _ERROR_MESSAGES.get((exception_type.__module__, exception_type.__name__))
        if message is not None:
            return message
    if isinstance(exc, TimeoutError):
        return "The service request timed out."
    if isinstance(exc, ConnectionError):
        return "The upstream service is unavailable."
    if isinstance(exc, PermissionError):
        return "This operation is not permitted."
    if isinstance(exc, KeyError):
        return "The requested assignment or action was not found."
    if isinstance(exc, (ValueError, TypeError)):
        return "The request contains invalid data."
    return _GENERIC_ERROR
