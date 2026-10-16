# CampusPulse · Assignment Guardian

CampusPulse compares an assignment document with evidence from a GitHub repository.
It turns quoted requirements into a reviewable checklist, explains gaps and unknowns,
and prepares GitHub Issue proposals for explicit approval.

The product contract uses Gemini through `google-genai`, the official GitHub MCP
server through the MCP Python SDK, FastAPI, and SQLite. Vertex AI Agent Engine is
the cloud deployment target. Ruflo coordinates development; it is separate from
the product's analysis agent.

## Verification status

This documentation was prepared against the shared models, settings, package
manifest, and [integration contract](docs/CONTRACT.md). At the worker 01 handoff,
feature modules were being implemented in separate worktrees. The application
commands below require the integrated implementation and have **not been smoke
tested by this documentation worker**.

No live Gemini analysis, GitHub MCP read or Issue write, or Vertex AI deployment
is established by these docs. Cloud project configuration and credentials remain
blockers for live cloud verification. Offline tests, when run, must be reported
separately from external service results.

## Workflow

1. Upload a text-bearing PDF, DOCX, or UTF-8 TXT/Markdown assignment and an HTTPS
   `github.com/owner/repository` URL. Parsing is local.
2. Gemini extracts individual requirements with quotes tied to source pages or
   numbered document sections. The extractor checks the quotes against the source.
3. Gemini chooses bounded repository, file, Issue, and CI reads through GitHub MCP.
   Findings cite collected evidence and use three statuses:

   | Status | Meaning |
   | --- | --- |
   | `satisfied` | Available evidence supports the requirement. |
   | `not_satisfied` | Available evidence supports a specific gap. |
   | `unverified` | Evidence is missing, ambiguous, inaccessible, or insufficient. |

4. Review source quotes, evidence links, concise explanations, and tool activity.
5. Request an Issue proposal from a finding's recommendation. Review its exact
   repository, title, and body before approving that immutable proposal.

The analysis agent has no Issue-writing tool. An approved action can write only
when server-side writes are enabled; completion also requires an Issue readback.
An ambiguous remote outcome is `unknown` and must not be blindly retried.

## Local setup

Requires Python 3.12 or newer. From the integrated repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn campuspulse.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. This command depends on the backend worker's
`campuspulse.api:app` entry point. The frontend is served by FastAPI and requires
no Node build. Keep `.env` private; it is excluded from Git.

For a service-backed analysis, configure one Gemini path and a GitHub credential:

| Setting | Purpose / checked-in default |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API credential, when using the API-key path. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false`; enable for the Vertex AI credential path. |
| `GOOGLE_CLOUD_PROJECT` | Project ID for Vertex AI; no project is supplied. |
| `GOOGLE_CLOUD_LOCATION` | `us-central1`; Agent Engine deployment region. |
| `GEMINI_LOCATION` | `global`; Gemini location, separate from the deployment region. |
| `GEMINI_MODEL` | `gemini-3.8-flash`; live model access is not verified here. |
| `GITHUB_TOKEN` | Server-side credential for the intended repository. |
| `ALLOW_GITHUB_WRITES` | `false`; keep disabled for a read-only demonstration. |
| `DATABASE_PATH` | `data/campuspulse.sqlite3`. |

The Vertex AI path additionally requires valid Application Default Credentials.
Configuration flags indicate supplied settings, not successful authentication or
service availability. Missing service configuration must leave local parsing and
the interface usable while analysis returns a clear error.

The settings also define a 10 MiB upload limit, an eight-call tool budget, and a
60-second request timeout. Provider credentials remain on the server. The browser
holds only an assignment-scoped bearer token in `sessionStorage`; treat that token
as access to the uploaded assignment.

## Checks and documentation

After all worker changes are integrated, run:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

These are verification commands, not a claim that the current revision passed.
Tests using injected fake clients establish local behavior, not live service access.

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Demo runbook and evidence checklist](docs/DEMO.md)
- [Presentation outline](docs/PRESENTATION.md)
- [Shared interfaces and worker ownership](docs/CONTRACT.md)
- [Ten-worker assignment specification](docs/swarm.json)

Cloud deployment instructions, security findings, and test results are owned by
their respective workers and must be reviewed with the integrated implementation.
Installing cloud dependencies alone does not deploy the service.

## Scope and limits

CampusPulse reviews evidence; it does not execute repository code or certify that
an assignment will be accepted. A README assertion alone is not proof of runtime
behavior. Scanned documents require text extraction outside this workflow; there
is no promised OCR path. Bounded tool reads may miss evidence, and uncertain
findings remain `unverified`.

This is a local prototype with assignment tokens, not a multi-user identity system.
Public hosting requires a separate operational review. There is no silent sample
mode: unavailable upstream services must never be presented as successful analysis.
