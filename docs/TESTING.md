# Testing

The automated suite runs offline. It does not require Gemini, GitHub, or Google
Cloud credentials and does not create external Issues or cloud resources. Install
the cloud extra to exercise the deployment adapter against the real installed SDK;
an SDK serialization test is not a deployment.

## Run the checks

Use Python 3.12 or later and Node.js 22 or later. The frontend tests use Node's
built-in test runner and need no npm packages. Run from the repository root.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,cloud]"
New-Item -ItemType Directory -Force -Path tmp | Out-Null
$env:TEMP = (Resolve-Path -LiteralPath tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest
.\.venv\Scripts\python.exe -m ruff check .
node --check campuspulse/static/client.mjs
node --check campuspulse/static/app.mjs
node --test tests/frontend/*.test.mjs
```

Linux/macOS with Python 3.12 installed:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,cloud]'
mkdir -p tmp
export TMPDIR="$PWD/tmp"
.venv/bin/python -m pytest -q --basetemp=tmp/pytest
.venv/bin/python -m ruff check .
node --check campuspulse/static/client.mjs
node --check campuspulse/static/app.mjs
node --test tests/frontend/*.test.mjs
```

The temporary directory stays in the checkout for restricted Windows environments.
`--basetemp` is a disposable pytest directory; pytest clears it on the next run.
Use a distinct path for simultaneous test runs. Do not put valuable files there.
The CI workflow runs the same checks on Linux, Python 3.12, and Node.js 22.
Its result is only known after GitHub Actions actually runs.

For a focused cross-module check:

```sh
python -m pytest tests/test_integration.py tests/test_e2e.py -q --basetemp=tmp/pytest-integration
```

Use the virtual environment's Python executable if it is not activated.

## Coverage and boundaries

| Layer | What the tests exercise | What they do not establish |
| --- | --- | --- |
| Parser and extraction | Real PDF/DOCX/text parsing, limits, source citations, structured response validation | OCR support or reliable extraction from every document |
| Gemini agent | Bounded read-tool selection, evidence grounding, malformed output and failures | Model accuracy or availability on the live Gemini service |
| GitHub gateway | MCP discovery schemas, read/write separation, decoding, repository scope and receipt checks | Current remote permissions or service compatibility without a live check |
| Storage and approval | Real SQLite, hashed assignment tokens, immutable proposals, exact hash agreement, atomic claims and durable outcomes | Recovery of an unknown remote result without inspecting GitHub |
| HTTP integration | Upload → extraction → four evidence read kinds → findings → proposal → explicit approval → creation → exact readback | Browser rendering or network transport to provider services |
| Frontend client | Real request construction and session state with injected HTTP responses, separate approval, duplicate/uncertain-response guards | Browser accessibility, layout or a live provider workflow |
| Cloud adapter | Installed SDK contracts, serialization and preflight logic | ADC access, a successful upload, Agent Engine deployment or remote invocation |

`tests/test_integration.py` uses the actual `google-genai` client with an explicit
`httpx.MockTransport`. The model request and response serialization is real; every
Gemini response is scripted test data. Its `GitHubGateway` is also the production
implementation, with explicitly fake MCP sessions and real MCP SDK message types.
It does not run an MCP HTTP server. The parser, FastAPI routes and file-backed
SQLite store are real. Injected analysis results must report `live: false`.

The fake Gemini transport requires `generationConfig.responseMimeType` to be
`application/json` for extraction and final assessment, with the expected model
JSON Schema in the serialized system instruction. It rejects constrained
`responseSchema` and `responseJsonSchema` wire fields. The schema describes the
requested output to the model; strict Pydantic validation enforces it locally,
including rejecting undeclared fields in extraction and final assessment output.
This regression check follows the coordinator's live JSON-mode compatibility
probe. The automated suite itself supplies every model response and does not
repeat that live probe. A successful isolated provider request is separate from
a complete live analysis with repository evidence, and from cloud deployment.

The integration checks cover these failures at module boundaries:

- Upload and analysis never open a write session. Creating a proposal does not write.
- Approval requires the assignment token, the immutable proposal hash, literal
  JSON `true`, the trusted origin and server-side write enablement.
- Two overlapping HTTP approvals through separate SQLite connections permit one
  creation call. A repeated approval cannot create another Issue.
- A write that commits in the fake service but loses its reply remains `unknown`.
  Mismatched readback also remains `unknown`; neither can be reapproved.
- Unavailable evidence becomes `unverified`. Invented source citations fail
  analysis; invented evidence quotations cannot become actionable findings.
- Unicode, newline and whitespace-sensitive proposal hashes agree between the
  security and storage modules.

Despite its filename, `tests/test_e2e.py` is an offline HTTP application test. It
checks local upload, protected retrieval, persistence after app restart, and a clear
503 when analysis is unconfigured. It uses no injected agent or gateway and proves
there is no automatic demo fallback. It is not a browser or live-service E2E test.

## Live validation is separate

Passing offline checks does not establish live Gemini quality, live GitHub MCP
access, cloud deployment, or a working production browser session. Keep separate
records for each actually executed check. Do not place credentials, bearer tokens,
private assignment content or raw provider errors in committed test reports.

For a read-only live check, configure services as described in the main README,
keep `ALLOW_GITHUB_WRITES=false`, start the local app, and upload
[`examples/assignment.txt`](../examples/assignment.txt) with an authorized repository
URL. Check source quotes, real evidence URLs, tool activity and the returned
analysis mode. A repository containing only a README may leave CI unverified;
missing evidence is not a failing CI result. Configuration booleans in health
output only describe settings, not successful provider authentication.

A separate write check needs an explicitly authorized test repository and a
reviewed proposal. Compare its exact title/body with the created Issue and its
readback. Confirm that resubmitting the same approval is rejected. If the outcome
is unknown, inspect GitHub before any new action; do not retry automatically.

Browser review should cover upload, missing-service messages, findings and evidence
links, the exact proposal preview, keyboard use, narrow screens and the separate
approval interaction. Record the browser and actions actually exercised. Node
client tests alone do not count as this review.

Cloud deployment and invocation require a usable project, billing/permissions,
credentials and the preflight steps in [DEPLOYMENT.md](DEPLOYMENT.md). A project ID
alone is not proof of access. Do not report deployment until a real resource and a
successful invocation have been verified.
