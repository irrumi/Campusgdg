"""Offline adversarial input tests; no real credentials or network calls."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from campuspulse.cloud import CloudConfigurationError
from campuspulse.extraction import ExtractionConfigurationError, ExtractionError
from campuspulse.github_mcp import (
    GitHubConfigurationError,
    GitHubMCPError,
    GitHubWriteDisabledError,
    GitHubWriteOutcomeUnknown,
)
from campuspulse.models import RepositoryRef
from campuspulse.parsing import DocumentLimitError, DocumentParseError
from campuspulse.security import RepositoryValidationError, proposal_hash, safe_error, validate_repository_url
from campuspulse.storage import _proposal_hash as storage_proposal_hash


@pytest.mark.parametrize(("value", "owner", "name"), [
    ("https://github.com/octocat/Hello-World", "octocat", "Hello-World"),
    ("https://github.com/o/r", "o", "r"),
    ("HTTPS://GITHUB.COM/Some-Org/project_2.0", "Some-Org", "project_2.0"),
    ("https://github.com/org/repo/", "org", "repo"),
    ("https://github.com/org/repo.git", "org", "repo"),
    ("https://github.com/org/repo.git/", "org", "repo"),
    ("https://github.com/org/.github", "org", ".github"),
    (f"https://github.com/{'a' * 39}/{'r' * 100}.git/", "a" * 39, "r" * 100),
])
def test_repository_url_has_one_canonical_identity(value, owner, name):
    repository = validate_repository_url(value)
    assert repository == RepositoryRef(owner=owner, name=name, url=f"https://github.com/{owner}/{name}")
    assert validate_repository_url(repository.url) == repository


@pytest.mark.parametrize("value", [
    None, 42, b"https://github.com/a/b", SimpleNamespace(url="https://github.com/a/b"),
    "", "github.com/a/b", "//github.com/a/b", "http://github.com/a/b", "git://github.com/a/b",
    "ssh://git@github.com/a/b", "git@github.com:a/b", "https:github.com/a/b",
    "https:///github.com/a/b", "https://github.com", "https://github.com/a", "https://github.com/a/",
    "https://github.com//b", "https://github.com//a/b", "https://github.com/a//b",
    "https://github.com/a/b//", "https://github.com/a/b/tree/main", "https://github.com/a/b/issues",
    "https://api.github.com/a/b", "https://www.github.com/a/b", "https://github.com.evil.test/a/b",
    "https://evil.test/github.com/a/b", "https://github.com./a/b", "https://127.0.0.1/a/b",
    "https://[::1]/a/b", "https://github.com:443/a/b", "https://github.com:8443/a/b",
    "https://user:password@github.com/a/b", "https://user@github.com/a/b", "https://@github.com/a/b",
    "https://github.com@evil.test/a/b", "https://evil.test@github.com/a/b",
    "https://github.com/a/b?token=fictional-secret", "https://github.com/a/b?",
    "https://github.com/a/b#fragment", "https://github.com/a/b#", "https://github.com/a/b?#",
    "https://github.com/a/b;param", "https://github.com/a/.", "https://github.com/a/..",
    "https://github.com/../b", "https://github.com/a/../b", "https://github.com/a/./b",
    "https://github.com/a/.git", "https://github.com/a/..git", "https://github.com/a/...git",
    "https://github.com/a/b.git.git", "https://github.com/-a/b", "https://github.com/a-/b",
    "https://github.com/a--b/r", "https://github.com/a_b/r", "https://github.com/a.b/r",
    f"https://github.com/{'a' * 40}/b", f"https://github.com/a/{'b' * 101}",
    "https://github.com/a/b c", "https://github.com/a/b+", "https://github.com/a/b&c",
    "https://github.com/a/b\\", "https://github.com\\@evil.test/a/b", "https:\\github.com\\a\\b",
    "https://github.com/a/%2e%2e", "https://github.com/a/%252e%252e", "https://github.com/a/b%2fc",
    "https://github.com/a%2fb/c", "https://github.com/a/b%5cc", "https://github%2ecom/a/b",
    "https://github.com/a/%62", "https://github.com/a/b%00", "https://github.com/a/b%0d%0a",
    "https://github.com/a/b%", "https://gіthub.com/a/b", "https://github.com/а/b",
    "https://github.com/a/р", "https://github.com/a/b\u202eexe", "https://github.com/a／b",
    "https://github.com/a/b\u200b", "https://github.com/a/b\ud800", "https://github.com/a/K",
    "https://github.com/a/İ", "https://github.com/a/ı", "https://github.com/a/ſ",
    pytest.param("https://github.com/a/" + "b" * 100_000, id="oversized-url"),
])
def test_repository_url_rejects_ambiguous_or_off_host_targets(value):
    with pytest.raises(RepositoryValidationError) as caught:
        validate_repository_url(value)
    assert str(caught.value) == "Use a valid HTTPS GitHub repository URL (https://github.com/owner/repo)."


@pytest.mark.parametrize("character", [*(chr(value) for value in range(33)), "\x7f", "\u00a0", "\ufeff"])
@pytest.mark.parametrize("position", ["before", "host", "path", "after"])
def test_repository_url_never_silently_discards_invisible_characters(character, position):
    parts = {
        "before": character + "https://github.com/a/b",
        "host": "https://github.com" + character + "/a/b",
        "path": "https://github.com/a/" + character + "b",
        "after": "https://github.com/a/b" + character,
    }
    with pytest.raises(RepositoryValidationError):
        validate_repository_url(parts[position])


def test_hash_has_exact_storage_wire_format_and_unicode():
    repository = validate_repository_url("https://github.com/Some-Org/repo")
    title = "Қазақша 🧪"
    body = 'First line\n"quoted"\\path\r\n'
    wire = ('{"body":"First line\\n\\"quoted\\"\\\\path\\r\\n",'
            '"repository":{"name":"repo","owner":"Some-Org",'
            '"url":"https://github.com/Some-Org/repo"},"title":"Қазақша 🧪"}')
    expected = hashlib.sha256(wire.encode("utf-8")).hexdigest()
    assert proposal_hash(repository, title, body) == expected
    assert proposal_hash(repository, title, body) == storage_proposal_hash(repository, title, body)
    assert len(expected) == 64
    assert set(expected) <= set("0123456789abcdef")


@pytest.mark.parametrize(("title", "body"), [
    ("title", "body"), ("title ", "body"), ("title", "body "), ("title", "body\n"),
    ("Title", "body"), ("title", "body\r\n"), ("titlé", "body"), ("title\u0301", "body"),
    ("title", "\x00body"), ("ti", "tlebody"),
])
def test_hash_preserves_exact_approved_content(title, body):
    repository = validate_repository_url("https://github.com/org/repo")
    digest = proposal_hash(repository, title, body)
    assert digest == storage_proposal_hash(repository, title, body)
    assert (digest == proposal_hash(repository, "title", "body")) == ((title, body) == ("title", "body"))


@pytest.mark.parametrize("change", [
    {"owner": "other"}, {"name": "other"}, {"url": "https://github.com/other/repo"},
    {"owner": "Org"}, {"url": "https://github.com/org/repo/"},
])
def test_hash_binds_every_repository_field_without_repairing_it(change):
    repository = validate_repository_url("https://github.com/org/repo")
    original = proposal_hash(repository, "title", "body")
    changed = repository.model_copy(update=change)
    assert proposal_hash(changed, "title", "body") != original
    assert proposal_hash(changed, "title", "body") == storage_proposal_hash(changed, "title", "body")


@pytest.mark.parametrize(("exception_type", "expected"), [
    (RepositoryValidationError, "Use a valid HTTPS GitHub repository URL (https://github.com/owner/repo)."),
    (DocumentLimitError, "The document exceeds the supported processing limits."),
    (DocumentParseError,
     "The document could not be parsed. Upload a supported, unencrypted document containing text."),
    (ExtractionConfigurationError, "Gemini is not configured."),
    (ExtractionError, "Assignment requirements could not be extracted. Check the document or try again later."),
    (GitHubConfigurationError, "GitHub access is not configured."),
    (GitHubMCPError, "GitHub could not complete the request."),
    (GitHubWriteDisabledError, "GitHub Issue creation is disabled."),
    (GitHubWriteOutcomeUnknown,
     "The GitHub Issue creation outcome is unknown. Inspect GitHub before making a new proposal."),
    (CloudConfigurationError, "Cloud analysis is not configured."),
    (TimeoutError, "The service request timed out."),
    (ConnectionError, "The upstream service is unavailable."),
    (PermissionError, "This operation is not permitted."),
    (KeyError, "The requested assignment or action was not found."),
    (ValueError, "The request contains invalid data."),
    (TypeError, "The request contains invalid data."),
    (RuntimeError, "The request could not be completed."),
    (OSError, "The request could not be completed."),
])
def test_safe_errors_are_fixed_even_for_known_application_types(exception_type, expected):
    secrets = "Authorization: Bearer fictional-token; api_key=fake-key; password=private; C:\\private\\key.json"
    exception = exception_type(secrets)
    exception.add_note(secrets)
    exception.__cause__ = RuntimeError(secrets)
    exception.response = SimpleNamespace(text=secrets, headers={"Authorization": secrets})
    assert safe_error(exception) == expected
    assert safe_error(exception_type("different arbitrary provider text")) == expected


def test_safe_error_never_stringifies_or_traverses_exception_data():
    class HostileProviderError(RuntimeError):
        def __str__(self):
            raise AssertionError("Must not render provider errors")

        def __repr__(self):
            raise AssertionError("Must not render provider errors")

        def __getattribute__(self, name):
            if name in {"args", "response", "request", "__cause__", "__context__", "__notes__"}:
                raise AssertionError("Must not inspect provider data")
            return super().__getattribute__(name)

    assert safe_error(HostileProviderError("fictional-secret")) == "The request could not be completed."
    group = ExceptionGroup("fictional-secret", [HostileProviderError("fictional-secret")])
    assert safe_error(group) == "The request could not be completed."


def test_application_subclasses_inherit_fixed_messages():
    class SpecificConfigurationError(ExtractionConfigurationError):
        pass

    assert safe_error(SpecificConfigurationError("fictional-secret")) == "Gemini is not configured."


def test_same_class_name_in_another_module_is_not_application_type():
    class ExtractionConfigurationError(RuntimeError):
        pass

    assert safe_error(ExtractionConfigurationError("fictional-secret")) == "The request could not be completed."


@pytest.mark.parametrize(("name", "expected"), [
    ("AgentError", "Repository analysis could not be completed."),
    ("AgentConfigurationError", "Analysis services are not configured."),
])
def test_agent_errors_can_be_classified_without_importing_agent(name, expected):
    # Explicit fake type demonstrates the circular-import-free message mapping;
    # safe_error never grants permissions or decides retryability based on it.
    exception_type = type(name, (Exception,), {"__module__": "campuspulse.agent"})
    assert safe_error(exception_type("fictional-secret")) == expected
