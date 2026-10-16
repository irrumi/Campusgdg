"""Offline SQLite persistence, authorization, and approval race checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import sqlite3
from threading import Barrier

import pytest

import campuspulse.storage as storage
from campuspulse.models import (
    ActionProposal,
    AgentStep,
    AnalysisResult,
    Document,
    DocumentPage,
    Evidence,
    Finding,
    IssueReceipt,
    ProposedAction,
    RepositoryRef,
    Requirement,
    SourceReference,
)
from campuspulse.storage import Store


@pytest.fixture
def repository():
    return RepositoryRef(owner="campus", name="guardian", url="https://github.com/campus/guardian")


@pytest.fixture
def document():
    return Document(
        filename="assignment.txt",
        pages=[DocumentPage(number=1, text="Provide a README. Пример тапсырма.")],
        sha256="a" * 64,
    )


def _analysis(repository):
    requirement = Requirement(
        id="req-1",
        title="README",
        description="Provide a README",
        source=SourceReference(page=1, quote="Provide a README."),
    )
    evidence = Evidence(
        id="evidence-1", tool="repository", source_url=repository.url, summary="README absent"
    )
    return AnalysisResult(
        requirements=[requirement],
        findings=[Finding(
            requirement_id=requirement.id,
            status="not_satisfied",
            explanation="The repository listing has no README",
            evidence_ids=[evidence.id],
            recommendation="Add a README describing setup",
        )],
        evidence=[evidence],
        steps=[AgentStep(kind="tool", message="Read repository listing", tool="repository")],
        model="explicit-offline-fixture",
        live=False,
        warnings=["Offline test evidence"],
        usage={"calls": 1},
    )


@pytest.fixture
def analysis(repository):
    return _analysis(repository)


def _ready_assignment(store, document, repository):
    assignment, token = store.create_assignment(document, repository)
    return store.save_analysis(assignment.id, token, _analysis(repository)), token


@pytest.fixture
def proposal():
    return ActionProposal(requirement_id="req-1", title="Add README", body="Explain setup.\nПример.")


def test_all_records_survive_restart(tmp_path, document, repository, analysis, proposal):
    database = tmp_path / "nested" / "durable.sqlite3"
    with Store(database) as store:
        assignment, token = _ready_assignment(store, document, repository)
        saved = store.save_analysis(assignment.id, token, analysis)
        action = store.propose_action(assignment.id, token, proposal)
        store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        receipt = IssueReceipt(
            number=12, url=f"{repository.url}/issues/12", title=proposal.title,
            body=proposal.body, verified=True,
        )
        finished = store.finish_action(assignment.id, token, action.id, "succeeded", receipt=receipt)
        first = store.add_event(assignment.id, token, "created", "Assignment stored")
        second = store.add_event(assignment.id, token, "verified", "Issue read back")

    with Store(database) as restarted:
        assert restarted.get_assignment(assignment.id, token) == saved
        assert restarted.get_action(assignment.id, token, action.id) == finished
        assert restarted.list_events(assignment.id, token) == [first, second]
        assert finished.status == "succeeded"
        assert finished.receipt == receipt

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        stored_hash = connection.execute("SELECT token_hash FROM assignments").fetchone()[0]
        assert stored_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in "\n".join(connection.iterdump())
    assert token.encode() not in database.read_bytes()


def test_memory_store_and_returned_models_are_independent(document, repository, analysis, proposal):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        store.save_analysis(assignment.id, token, analysis)
        action = store.propose_action(assignment.id, token, proposal)
        document.pages[0].text = "changed"
        repository.name = "changed"
        analysis.requirements[0].title = "changed"
        assignment.document.filename = "changed"
        proposal.body = "changed"
        action.body = "changed"
        action.repository.name = "changed"
        action.status = "succeeded"
        loaded = store.get_assignment(assignment.id, token)
        stored_action = store.get_action(assignment.id, token, action.id)
        assert loaded.document.filename == "assignment.txt"
        assert loaded.document.pages[0].text != "changed"
        assert loaded.repository.name == "guardian"
        assert loaded.analysis.requirements[0].title == "README"
        assert stored_action.body == "Explain setup.\nПример."
        assert stored_action.repository.name == "guardian"
        assert stored_action.status == "pending"


@pytest.mark.parametrize("method", [
    "get_assignment", "save_analysis", "propose_action", "get_action", "claim_action",
    "finish_action", "add_event", "list_events",
])
def test_every_operation_hides_unknown_and_unauthorized_ids(
    document, repository, analysis, proposal, method
):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        extra = {
            "get_assignment": (), "save_analysis": (analysis,), "propose_action": (proposal,),
            "get_action": (action.id,), "claim_action": (action.id, action.proposal_hash),
            "finish_action": (action.id, "unknown"), "add_event": ("test", "event"), "list_events": (),
        }[method]
        errors = []
        for assignment_id, supplied_token in [
            (assignment.id, "wrong"), ("missing", token), (assignment.id, None),
            (assignment.id, "' OR 1=1 --"), ("' OR 1=1 --", token),
        ]:
            with pytest.raises(KeyError) as error:
                getattr(store, method)(assignment_id, supplied_token, *extra)
            errors.append(error.value.args)
        assert errors == [("Resource not found",)] * len(errors)
        assert store.get_assignment(assignment.id, token) == assignment
        assert store.get_action(assignment.id, token, action.id).status == "pending"
        assert store.list_events(assignment.id, token) == []


def test_auth_always_compares_fixed_length_hashes(monkeypatch, document, repository):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        actual_compare = storage.hmac.compare_digest
        calls = []

        def compare(left, right):
            calls.append((len(left), len(right)))
            return actual_compare(left, right)

        monkeypatch.setattr(storage.hmac, "compare_digest", compare)
        store.get_assignment(assignment.id, token)
        for assignment_id in (assignment.id, "missing"):
            with pytest.raises(KeyError):
                store.get_assignment(assignment_id, "incorrect")
        assert calls == [(64, 64), (64, 64), (64, 64)]


def test_tokens_and_actions_cannot_cross_assignments(document, repository, proposal):
    with Store(":memory:") as store:
        first, first_token = _ready_assignment(store, document, repository)
        second, second_token = store.create_assignment(document, repository)
        action = store.propose_action(first.id, first_token, proposal)
        assert first_token != second_token
        with pytest.raises(KeyError):
            store.get_assignment(first.id, second_token)
        with pytest.raises(KeyError):
            store.get_action(second.id, second_token, action.id)
        with pytest.raises(KeyError):
            store.claim_action(second.id, second_token, action.id, action.proposal_hash)
        with pytest.raises(KeyError):
            store.finish_action(second.id, second_token, action.id, "failed")
        assert store.list_events(second.id, second_token) == []


def test_proposal_hash_binds_exact_content_and_repository(document, repository, proposal):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        canonical = json.dumps(
            {"repository": repository.model_dump(mode="json"), "title": proposal.title, "body": proposal.body},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        assert action.proposal_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        for change in ({"title": proposal.title + " "}, {"body": proposal.body + "\n"}):
            changed = store.propose_action(assignment.id, token, proposal.model_copy(update=change))
            assert changed.proposal_hash != action.proposal_hash
        other_repo = repository.model_copy(update={"name": "another", "url": "https://github.com/campus/another"})
        other, other_token = _ready_assignment(store, document, other_repo)
        changed = store.propose_action(other.id, other_token, proposal)
        assert changed.proposal_hash != action.proposal_hash


def test_untrusted_action_metadata_is_replaced(document, repository, proposal):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        forged = ProposedAction(
            **proposal.model_dump(), id="injected", assignment_id="another", status="succeeded",
            repository=RepositoryRef(owner="other", name="repo", url="https://github.com/other/repo"),
            proposal_hash="forged", error="forged",
        )
        action = store.propose_action(assignment.id, token, forged)
        assert action.id != "injected"
        assert action.assignment_id == assignment.id
        assert action.repository == repository
        assert action.proposal_hash != "forged"
        assert action.status == "pending"
        assert action.error is None


@pytest.mark.parametrize("supplied_hash", ["incorrect", "", "другой", None])
def test_wrong_hash_does_not_claim_action(document, repository, proposal, supplied_hash):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        with pytest.raises(ValueError, match="hash"):
            store.claim_action(assignment.id, token, action.id, supplied_hash)
        assert store.get_action(assignment.id, token, action.id).status == "pending"
        claimed = store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        assert claimed.status == "executing"


@pytest.mark.parametrize("separate_connections", [False, True])
def test_concurrent_approvals_have_exactly_one_winner(
    tmp_path, document, repository, proposal, separate_connections
):
    database = tmp_path / "race.sqlite3"
    with ExitStack() as stack:
        primary = stack.enter_context(Store(database))
        assignment, token = _ready_assignment(primary, document, repository)
        action = primary.propose_action(assignment.id, token, proposal)
        stores = [stack.enter_context(Store(database)) for _ in range(8)] if separate_connections else [primary] * 8
        barrier = Barrier(len(stores))

        def approve(store):
            barrier.wait(timeout=10)
            try:
                return store.claim_action(assignment.id, token, action.id, action.proposal_hash).status
            except ValueError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            outcomes = list(executor.map(approve, stores))
        assert outcomes.count("executing") == 1
        assert outcomes.count("conflict") == 7

    with Store(database) as restarted:
        assert restarted.get_action(assignment.id, token, action.id).status == "executing"
        with pytest.raises(ValueError, match="claimed"):
            restarted.claim_action(assignment.id, token, action.id, action.proposal_hash)


@pytest.mark.parametrize("status", ["succeeded", "failed", "unknown"])
def test_terminal_actions_are_never_retried(tmp_path, document, repository, proposal, status):
    database = tmp_path / "terminal.sqlite3"
    with Store(database) as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        receipt = IssueReceipt(
            number=1, url=f"{repository.url}/issues/1", title=proposal.title,
            body=proposal.body, verified=True,
        ) if status == "succeeded" else None
        finished = store.finish_action(
            assignment.id, token, action.id, status, receipt=receipt,
            error=None if status == "succeeded" else "Stable safe error",
        )
    with Store(database) as store:
        with pytest.raises(ValueError, match="claimed"):
            store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        with pytest.raises(ValueError, match="executing"):
            store.finish_action(assignment.id, token, action.id, "unknown")
        assert store.get_action(assignment.id, token, action.id) == finished


def test_finish_checks_state_and_rolls_back_invalid_transition(document, repository, proposal):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        with pytest.raises(ValueError, match="executing"):
            store.finish_action(assignment.id, token, action.id, "failed")
        assert store.get_action(assignment.id, token, action.id).status == "pending"
        store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        for status in ("pending", "executing", "unsupported", "succeeded"):
            with pytest.raises(ValueError):
                store.finish_action(assignment.id, token, action.id, status)
        assert store.get_action(assignment.id, token, action.id).status == "executing"
        assert store.finish_action(assignment.id, token, action.id, "unknown").status == "unknown"


def test_sql_failure_rolls_back_and_connection_remains_usable(tmp_path, document, repository, proposal):
    database = tmp_path / "rollback.sqlite3"
    with Store(database) as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TRIGGER injected_failure AFTER UPDATE OF status ON actions
                BEGIN SELECT RAISE(ABORT, 'test failure'); END"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="test failure"):
            store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        assert store.get_action(assignment.id, token, action.id).status == "pending"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER injected_failure")
        assert store.claim_action(assignment.id, token, action.id, action.proposal_hash).status == "executing"


def test_proposal_payload_is_immutable_in_sqlite(tmp_path, document, repository, proposal):
    database = tmp_path / "immutable.sqlite3"
    with Store(database) as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        with sqlite3.connect(database) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("UPDATE actions SET proposal_json = ? WHERE id = ?", ("{}", action.id))
        assert store.get_action(assignment.id, token, action.id) == action


def test_sql_looking_content_round_trips_as_data(document, repository):
    payload = "'); DROP TABLE assignments; --"
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(
            assignment.id, token, ActionProposal(requirement_id="req-1", title=payload, body=payload)
        )
        event = store.add_event(assignment.id, token, payload, payload)
        assert store.get_action(assignment.id, token, action.id).body == payload
        assert store.list_events(assignment.id, token) == [event]
        assert store.get_assignment(assignment.id, token) == assignment


def test_proposals_require_a_stored_requirement_and_recommendation(document, repository, analysis, proposal):
    with Store(":memory:") as store:
        assignment, token = store.create_assignment(document, repository)
        with pytest.raises(ValueError, match="Analyze"):
            store.propose_action(assignment.id, token, proposal)
        store.save_analysis(assignment.id, token, analysis)
        with pytest.raises(ValueError, match="recommendation"):
            store.propose_action(
                assignment.id, token, proposal.model_copy(update={"requirement_id": "fabricated"})
            )
        analysis.requirements = []
        store.save_analysis(assignment.id, token, analysis)
        with pytest.raises(ValueError, match="recommendation"):
            store.propose_action(assignment.id, token, proposal)
        analysis = _analysis(repository)
        analysis.findings[0].recommendation = None
        store.save_analysis(assignment.id, token, analysis)
        with pytest.raises(ValueError, match="recommendation"):
            store.propose_action(assignment.id, token, proposal)
        store.save_analysis(assignment.id, token, _analysis(repository))
        assert store.propose_action(assignment.id, token, proposal).status == "pending"


@pytest.mark.parametrize("change", [
    {"verified": False}, {"title": "Different title"}, {"body": "Different body"},
    {"url": "https://github.com/other/guardian/issues/1"},
    {"url": "https://github.com/campus/other/issues/1"},
    {"url": "https://github.com/campus/guardian/issues/2"},
    {"url": "http://github.com/campus/guardian/issues/1"},
    {"url": "https://github.com.evil.example/campus/guardian/issues/1"},
    {"url": "https://github.com/campus/guardian/issues/1?query=value"},
    {"url": "https://github.com/campus/guardian/issues/1#fragment"},
    {"url": "https://user@github.com/campus/guardian/issues/1"},
    {"url": "https://github.com:443/campus/guardian/issues/1"},
])
def test_success_requires_an_exact_verified_receipt(document, repository, proposal, change):
    with Store(":memory:") as store:
        assignment, token = _ready_assignment(store, document, repository)
        action = store.propose_action(assignment.id, token, proposal)
        store.claim_action(assignment.id, token, action.id, action.proposal_hash)
        receipt = IssueReceipt(
            number=1, url=f"{repository.url}/issues/1", title=proposal.title,
            body=proposal.body, verified=True,
        ).model_copy(update=change)
        with pytest.raises(ValueError, match="Receipt"):
            store.finish_action(assignment.id, token, action.id, "succeeded", receipt=receipt)
        assert store.get_action(assignment.id, token, action.id).status == "executing"
        assert store.finish_action(assignment.id, token, action.id, "unknown").status == "unknown"


def test_newer_schema_is_rejected_without_changes(tmp_path):
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="newer"):
        Store(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []


def test_failed_migration_leaves_no_partial_schema(tmp_path):
    database = tmp_path / "migration.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE actions (legacy_value TEXT)")
        connection.execute("INSERT INTO actions VALUES (?)", ("preserved",))
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        Store(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == [("actions",)]
        assert connection.execute("SELECT legacy_value FROM actions").fetchall() == [("preserved",)]
