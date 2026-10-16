# CampusPulse · Assignment Guardian

CampusPulse compares an assignment document with evidence from a GitHub repository.
It turns quoted requirements into a reviewable checklist, explains gaps and unknowns,
and prepares GitHub Issue proposals for explicit approval.

The implementation uses Gemini through `google-genai`, the official GitHub MCP
server through the MCP Python SDK, FastAPI, and SQLite. The cloud wrapper targets
an Agent Engine `reasoningEngines` resource. Ruflo coordinates development;
the product's analysis agent runs on Gemini.

## Verification status

The core implementation has passed offline component checks. Real repository,
file, Issues, and CI reads through the official GitHub MCP server passed on
2026-10-16; see [the recorded check](artifacts/live-github-mcp.json).
The [cloud dry run](artifacts/cloud-dry-run.json) validates local configuration
and source packaging without contacting Google Cloud.

The Gemini Developer API returned a real response through `google-genai` using
`gemini-3.5-flash`; see [the API check](artifacts/live-gemini-api.json).
Requirement extraction and function selection also returned real responses.
The full browser analysis remains incomplete because subsequent requests hit
provider quota/rate limits (HTTP 429), including a control attempt after a pause.
Cloud deployment and remote invocation remain blocked by Google Cloud
authentication and project access. `campusgdg-511817` is the supplied
project ID; it is not a credential or proof of IAM access. No real Issue write or
GitHub push is recorded. [VERIFICATION.md](docs/VERIFICATION.md) is the canonical
record of the final integrated tests, browser checks, artifacts, and remaining
blockers; component test totals are deliberately not repeated here.

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

Open [the local app](http://127.0.0.1:8000). FastAPI serves the HTML/CSS/JS frontend;
no Node build is required. Keep `.env` private; it is excluded from Git.

For a service-backed analysis, configure one Gemini path and a GitHub credential:

| Setting | Purpose / checked-in default |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API credential, when using the API-key path. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false`; enable for the Vertex AI credential path. |
| `GOOGLE_CLOUD_PROJECT` | Set to an accessible project, such as the supplied `campusgdg-511817`; blank by default. |
| `GOOGLE_CLOUD_LOCATION` | `us-central1`; Agent Engine deployment region. |
| `GEMINI_LOCATION` | `global`; Gemini location, separate from the deployment region. |
| `GEMINI_MODEL` | `gemini-3.5-flash`; verified with the Developer API. |
| `GITHUB_TOKEN` | Server-side credential for the intended repository. |
| `ALLOW_GITHUB_WRITES` | `false`; keep disabled for a read-only demonstration. |
| `DATABASE_PATH` | `data/campuspulse.sqlite3`. |
| `ALLOWED_HOSTS` | JSON list; defaults to `localhost`, `127.0.0.1`, and `[::1]`. |

The Vertex AI path additionally requires valid Application Default Credentials.
The local Developer API path uses `GEMINI_API_KEY` and does not require Cloud ADC.
JSON output is requested with a schema in the system instruction and validated
strictly by Pydantic, including exact source quotes and collected evidence IDs.
Configuration flags and the health endpoint's `mode` indicate supplied settings,
not successful authentication. Upload parses locally; missing Gemini or GitHub
configuration returns 503 when analysis is requested.

The settings also define a 10 MiB upload limit, an eight-call tool budget, and a
60-second request timeout. Provider credentials remain on the server. The browser
holds only an assignment-scoped bearer token in `sessionStorage`; treat that token
as access to the uploaded assignment.

## Checks and documentation

Run from the repository root. A writable temporary directory avoids the observed
Windows sandbox temporary-file failures:

```powershell
New-Item -ItemType Directory -Force .\tmp | Out-Null
$env:TEMP = (Resolve-Path .\tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

Tests with explicitly injected fake clients establish local behavior. Install
`requirements-cloud.txt` to include checks that otherwise skip the optional cloud
SDK. Use
[TESTING.md](docs/TESTING.md) for test scope and [VERIFICATION.md](docs/VERIFICATION.md)
for the commands and outcomes recorded against the integrated revision.

For cloud analysis, install `requirements-cloud.txt`, authenticate an account with
access to the supplied project, and follow [DEPLOYMENT.md](docs/DEPLOYMENT.md) for
runtime identity, Secret Manager, dry run, deployment, and invocation. The Windows
[login helper](scripts/google_login.ps1) scopes ADC to the ignored `tmp` directory;
run it in the same PowerShell session as subsequent cloud commands. Authentication
does not grant missing project permissions. The cloud wrapper exposes only
`async_query` and forces GitHub writes off.

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Demo runbook and evidence checklist](docs/DEMO.md)
- [Presentation outline](docs/PRESENTATION.md)
- [Shared interfaces and worker ownership](docs/CONTRACT.md)
- [Ten-worker assignment specification](docs/swarm.json)
- [Actual Ruflo execution and task states](docs/SWARM.md)
- [Security boundaries and limitations](docs/SECURITY.md)
- [Development task records](artifacts/swarm-manifest.json)

## Scope and limits

CampusPulse reviews evidence; it does not execute repository code or certify that
an assignment will be accepted. A README assertion alone is not proof of runtime
behavior. Scanned documents require text extraction outside this workflow; there
is no promised OCR path. Bounded tool reads may miss evidence, and uncertain
findings remain `unverified`.

This local prototype uses assignment tokens. Public hosting needs user identity,
repository authorization, and protected persistent storage. Assignment text and
repository evidence are stored in plaintext SQLite and sent to Gemini for analysis.
There is no silent sample mode or fallback that reports unavailable services as
successful analysis.
