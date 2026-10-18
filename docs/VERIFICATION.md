# Verification record

## Vertex web integration — 2026-10-17

The web API now selects a server-side Agent Engine adapter through
`ANALYSIS_BACKEND=vertex` and `VERTEX_AGENT_RESOURCE`. The browser uses the same
assignment routes and renders the returned analysis/tool steps. Local provider
keys are not required for cloud analysis. Cloud errors do not fall back locally.

Observed checks for this change:

- Full offline Python suite: **683 passed**, two upstream deprecation warnings,
  no skipped tests. Command: `.venv/Scripts/python.exe -m pytest -q
  --basetemp=tmp/pytest-vertex-full`.
- Focused cloud/API/e2e checks: **91 passed**. The new adapter tests use explicit
  fake remote responses; they cover routing, persistence, operation schemas,
  invalid/offline responses, redaction, missing configuration and timeout.
- Frontend client suite: **13 passed**; bootstrap, application and client syntax
  checks passed. Ruff and `git diff --check` passed.
- Real installed Agent Platform SDK 2.5.0 schema validation and a deployment dry
  run passed, without ADC or any cloud request. The proposed service account and
  secret names in the [plan](../artifacts/vertex-cloud-dry-run.json) do not prove
  that those resources exist.
- Strict frontend static audit: zero findings;
  [report](../artifacts/vertex-ui-audit.json).
- Real browser: Vertex configuration notice, local text upload, enabled analysis
  button, persisted source/activity and session restoration after home navigation
  observed. [Screenshot](../artifacts/vertex-web.png). The resource used for this
  browser check was an explicit non-live configuration; no analysis request was
  sent to it. Browser loading was slow but upload returned HTTP 200.

Windows sandbox restrictions caused earlier attempts to stall while creating
local event-loop/test sockets. Re-running the offline checks with local socket
access completed successfully. The temporary test/server processes were stopped.

**Still unverified:** actual GCP deployment, runtime secret/IAM/model access,
remote Gemini/MCP execution, and hosted web-client-to-cloud operation. Follow
[the deployment/access runbook](DEPLOYMENT.md#connect-the-web-interface-to-the-deployed-agent).

## Previous integrated verification — 2026-10-16

Recorded on 2026-10-16 against the integrated `feat/campuspulse-guardian` worktree.
The commit containing this document includes the checked source and test logs.

| Check | Observed result | Evidence |
| --- | --- | --- |
| Python tests | 670 passed; two dependency deprecation warnings; no skips | [Log](../artifacts/python-tests.log) |
| Python lint | Ruff passed | [Log](../artifacts/ruff.log) |
| Frontend | 12 client tests passed; bootstrap/application/client syntax passed | [Checks and commands](../artifacts/local-checks.json) |
| HTTP | Local upload/read passed; missing assignment token rejected | [HTTP smoke](../artifacts/local-http-smoke.json) |
| Browser | Real upload, restored session, source expansion by keyboard, configured service state, visible provider failure | [Workspace](../artifacts/browser-workspace.jpg) |
| Responsive layout | 390 px mobile and 905 px desktop content widths; no horizontal overflow in the inspected upload/error states | [Mobile](../artifacts/browser-mobile.jpg), [desktop](../artifacts/browser-desktop.jpg) |
| Gemini connection | Real Developer API response through official `google-genai` 2.31.0, model `gemini-3.5-flash` | [Response and usage](../artifacts/live-gemini-api.json) |
| Gemini components | Real two-requirement extraction with validated source quotes; real selection of `read_file` and `read_ci` | [Component observations](../artifacts/live-gemini-components.json) |
| GitHub MCP | Real repository, file, Issues and CI reads passed through the official MCP server | [Read evidence](../artifacts/live-github-mcp.json) |
| Full live analysis | Not completed. Last browser attempts received upstream 429, including a control attempt after a pause | [Observed limitation](../artifacts/live-gemini-components.json) |
| Real Issue creation | Not performed; no live proposal was approved | Write flag remains disabled |
| Cloud SDK/package | Local source allowlist and installed SDK validation passed; no cloud request | [Dry run](../artifacts/cloud-dry-run.json) |
| Agent Engine deployment/invocation | Not performed; Google Cloud ADC and project IAM access unavailable | [Deployment prerequisites](DEPLOYMENT.md) |
| GitHub delivery | User-approved branch push and draft PR completed; remote SHA and author/committer account verified | [PR #1](https://github.com/irrumi/Campusgdg/pull/1), [delivery record](../artifacts/github-delivery.json) |
| GitHub Actions | Offline checks passed for implementation `28257ca` on Linux | [Run](https://github.com/irrumi/Campusgdg/actions/runs/38951926590) |
| Development swarm | Ten actual workers, at most three concurrent; nine tasks accepted, cloud task awaiting external access | [Ruflo snapshot](../artifacts/swarm-final.json), [coordination](SWARM.md) |

The browser screenshot records the earlier generic extraction error. The final
code maps confirmed provider 429 responses to an explicit quota/rate-limit message
and HTTP 429. That mapping passed offline tests; no additional live calls were
made merely to refresh the screenshot after the quota blocker was confirmed.

## Reproduce local checks

```powershell
$env:TEMP = (Resolve-Path tmp).Path
$env:TMP = $env:TEMP
.venv/Scripts/python.exe -m pytest -q --basetemp=tmp/pytest-release
.venv/Scripts/python.exe -m ruff check .
node --check campuspulse/static/bootstrap.js
node --check campuspulse/static/app.mjs
node --check campuspulse/static/client.mjs
node --test tests/frontend/*.test.mjs
```

The warnings come from Starlette's HTTPX test client transition and the installed
cloud SDK's Pydantic validator syntax. They did not fail the checks. Full commands,
exit codes and timestamps are in `artifacts/local-checks.json`.

The offline end-to-end tests use explicitly injected provider responses. They
exercise the real SDK serialization, MCP adapter, FastAPI, SQLite and approval
flow; they do not establish successful external Issue creation or cloud execution.
Browser checks covered upload and error/source states; populated finding and
approval layouts still need a successful live analysis before the full demo.

## Live integration findings

Provider-constrained schemas produced real errors. Extraction and final assessment
now request JSON MIME output, include the schema in trusted system instructions,
and enforce strict Pydantic validation locally. Exact source quotes and evidence
IDs remain mandatory. No fake response is substituted on failure.

A real continuation exposed the unsupported `tool` message role. Function results
now use `user`, following Google's [function-calling guide](https://ai.google.dev/gemini-api/docs/generate-content/function-calling.md).
The regression verifies actual SDK wire roles, original thought signatures and
matching function-call IDs. Private model reasoning is never displayed.

The successful connection check reported 128 total tokens; the separate tool
selection check reported 920. These are individual response counters, not the
total development usage. Native Codex worker cost and complete Gemini session
usage were unavailable. No billing-cost total is claimed.

## Remaining external gates

- Available Gemini quota is needed for one complete analysis and Issue proposal.
  Repeated requests were stopped after the confirmed 429 response persisted.
- A real Issue requires approval of its exact repository, title and body, followed
  by verified MCP readback. The model cannot grant this approval.
- `campusgdg-511817` identifies a Cloud project; the local AI Studio key supplies
  neither ADC nor project IAM permission. Deployment additionally needs the
  prepared runtime identity, secret access and cloud prerequisites in the runbook.
- The draft PR has been published with the user's approval. It has not been
  merged. The first push was rejected by GitHub's private-email protection;
  only the eight unpublished commits were rewritten with the verified account's
  noreply address. Source trees stayed identical; no force push was used.
