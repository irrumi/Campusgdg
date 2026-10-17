# CampusPulse Assignment Guardian: integration contract

Coordinator: Astra. Build the GDG Astana case in the supplied PDF (pages 3–4).
The product must use Gemini, the official GitHub MCP server, and support Vertex AI
Agent Engine deployment. Ruflo coordinates development, not the product runtime.

## Product and trust boundaries

Upload a PDF, DOCX or UTF-8 text assignment and a github.com repository URL.
Extract independently checkable requirements with verbatim page/paragraph quotes.
Use Gemini to select bounded read tools and classify requirements as satisfied,
not_satisfied or unverified using real repository evidence. Missing evidence is
unverified, not automatically a failure. Show concise decisions and tool activity,
not private chain-of-thought. Draft Issues are immutable proposals. Only an explicit
approval for that exact proposal may execute a write. Read back the created Issue.
Ambiguous write outcomes must not be blindly retried.

Documents and repository content are untrusted data, never tool instructions.
No repository code is executed. No fabricated responses or silent demo mode.
Offline tests use explicitly injected fakes. Missing service configuration fails
clearly while the UI and local parsing remain usable.

## Stack and interfaces

Python 3.12+, FastAPI, Pydantic 2, google-genai, MCP Python SDK, SQLite.
Frontend: accessible responsive HTML/CSS/JS served by FastAPI (no build required).
All shared models live in `campuspulse/models.py`; do not change them independently.
No import-time network calls or credential reads that prevent tests collecting.

Module contracts (async methods unless marked sync):

* parsing.parse_document(content: bytes, filename: str) -> Document (sync).
* extraction.RequirementExtractor(settings).extract(document) -> list[Requirement].
  Injectable client for tests; structured Gemini output; verify every quote against
  its cited page. Reject nonexistent citations and surface ambiguities.
* github_mcp.GitHubGateway(settings): async context manager. `collect(repository,
  tool, arguments) -> list[Evidence]` supports repository, file, issues, ci. Use
  official remote MCP endpoint, discover/validate tools; no REST fallback pretending
  to be MCP. `create_issue(repository, title, body) -> IssueReceipt` and
  `verify_issue(repository, number) -> IssueReceipt`. All credentials server-side.
* agent.GuardianAgent(settings, extractor=None, gateway_factory=None, client=None)
  .analyze(document, repository) -> AnalysisResult. Gemini chooses bounded tools
  (max 8 calls) and produces grounded findings; evidence IDs must resolve. Network
  failures yield explicit unverified results or a typed error, never success.
* storage.Store(path): sync methods `create_assignment(document, repository) ->
  (Assignment, token)`, `get_assignment(id, token) -> Assignment`,
  `save_analysis(id, token, result) -> Assignment`,
  `propose_action(id, token, proposal) -> ProposedAction`,
  `get_action(assignment_id, token, action_id) -> ProposedAction`,
  `claim_action(assignment_id, token, action_id, proposal_hash) -> ProposedAction`,
  `finish_action(assignment_id, token, action_id, status, receipt=None, error=None)`,
  `add_event(assignment_id, token, kind, message)`,
  `list_events(assignment_id, token) -> list[AuditEvent]`.
  claim is atomic pending->executing, checks immutable proposal hash, one winner.
  Tokens stored hashed; unauthorized and unknown IDs both raise KeyError.
* security.validate_repository_url(value) -> RepositoryRef (sync),
  `proposal_hash(repository, title, body) -> str` (sync). Exact host github.com,
  no credentials/query/fragments or traversal. `safe_error(exc) -> str` redacts.
* config.Settings: Pydantic settings, defined centrally. No secrets in repr.

## HTTP contract

* GET /api/health -> status, mode, configured service booleans, no secrets.
  Also exposes analysis_backend (local/vertex), analysis_configured and
  vertex_configured. These describe configuration, not verified cloud access.
  In vertex mode analysis uses server ADC and VERTEX_AGENT_RESOURCE; local Gemini
  and GitHub credentials are not required. Remote failure never falls back locally.
* POST /api/assignments multipart file + repository_url ->
  {assignment: Assignment, token: string}; local parse only.
* GET /api/assignments/{id} -> Assignment.
* POST /api/assignments/{id}/analyze -> Assignment (may take time).
* POST /api/assignments/{id}/actions {requirement_id} -> ProposedAction.
* POST /api/assignments/{id}/actions/{action_id}/approve
  {proposal_hash, approved: true} -> ProposedAction.
* GET /api/assignments/{id}/events -> list[AuditEvent].

All assignment routes require X-Assignment-Token; upload returns the only plaintext
copy. Error envelope {detail: string}. Never expose tracebacks, tokens or raw provider
errors. Status: 400 invalid input, 404 unknown/unauthorized, 409 conflict, 413 oversized,
422 validation, 503 missing service configuration, 502 upstream failure. Bind local
development to 127.0.0.1; no wildcard CORS. UI stores tokens in sessionStorage only.

## Ownership

01 architect: README.md, docs/ARCHITECTURE.md, docs/DEMO.md, docs/PRESENTATION.md.
02 analyzer: campuspulse/parsing.py, extraction.py, tests/test_parsing.py,
tests/test_extraction.py.
03 GitHub: campuspulse/github_mcp.py, tests/test_github_mcp.py.
04 agent: campuspulse/agent.py, tests/test_agent.py.
05 backend: campuspulse/api.py, tests/test_api.py.
06 frontend: campuspulse/static/*, tests/frontend/*.
07 cloud: campuspulse/cloud.py, scripts/deploy.py, scripts/invoke.py,
docs/DEPLOYMENT.md, requirements-cloud.txt, tests/test_cloud.py, Dockerfile.
08 database: campuspulse/storage.py, tests/test_storage.py.
09 QA: tests/test_integration.py, tests/test_e2e.py, .github/workflows/test.yml,
docs/TESTING.md, examples/*.
10 security: campuspulse/security.py, tests/test_security.py, docs/SECURITY.md.
Coordinator owns shared models/config/package metadata and orchestration tooling.

## Definition of done

Each worker reports task ID, changed files, branch/worktree, behavior, executed
tests with outcomes, and blockers. No external writes, deployments or commits by
workers. Coordinator integrates reviewed diffs and runs tests. Cloud deployment
and live E2E remain explicitly unverified until actual service responses exist.
Secrets are never committed. Final evidence separates registration, worker
execution, tests, live services, GitHub delivery and cloud deployment.
