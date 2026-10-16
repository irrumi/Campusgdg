# Demo runbook

Use the [README](../README.md) setup commands from the integrated repository.
Check [VERIFICATION.md](VERIFICATION.md) before presenting: it records the current
revision, completed tests, browser checks, and blockers. This runbook is a procedure,
not evidence that every step has been performed.

Real official GitHub MCP repository, file, Issues, and CI reads are recorded in
[live-github-mcp.json](../artifacts/live-github-mcp.json). Gemini connection,
extraction and tool selection returned real responses; a complete analysis is
blocked by observed HTTP 429 responses. Cloud deployment/invocation and a real
approved Issue write are not yet established.

## Prepare

1. Choose a repository you are permitted to inspect and a text-bearing assignment
   that applies to it. Do not assume a preselected finding or a particular verdict.
2. Prepare the local environment. Keep `ALLOW_GITHUB_WRITES=false` for the default
   demonstration. Keep provider secrets out of screenshots and terminal output.
3. Start the local application and inspect `GET /api/health`. Record the response
   status and configured booleans. These booleans do not prove authentication.
4. Confirm the application revision and the checks actually completed. Report live
   service access and cloud deployment separately from offline test results.

## Five-minute walkthrough

| Time | Action | Evidence to show |
| --- | --- | --- |
| 0:00–0:40 | Explain the assignment-to-repository review problem. | The actual assignment and selected repository. |
| 0:40–1:20 | Upload the document and repository URL. | Parsed source and a successful local upload response. |
| 1:20–2:40 | Start analysis with configured services. | Real tool activity and the resulting checklist, or the actual service error. |
| 2:40–3:40 | Open a finding and its sources. | Quoted requirement, source reference, evidence URL, concise explanation. |
| 3:40–4:20 | Inspect uncertainty or a recommendation if present. | An honest status and its supporting evidence; no forced outcome. |
| 4:20–5:00 | Preview an Issue proposal where a recommendation exists. | Exact repository/title/body and the separate approval control. |

The model may return different findings or take longer than the presentation slot.
Do not promise a particular output or replace a failure with canned success. A
recording from an earlier run must be labelled with its date and revision.

## Optional approved Issue write

Only perform this step when the repository owner has authorized a real Issue write.
This runbook itself does not authorize one.

1. Enable server-side writes for the intended environment and use a credential with
   the required repository permission. Reload the server configuration if needed.
2. Create a proposal from an existing recommendation and inspect its exact content.
3. Approve that displayed proposal once. Approval supplies its `proposal_hash` and
   literal `approved: true`; the backend must claim the action atomically.
4. Show success only when the returned receipt has a verified readback matching the
   intended repository, title, and body. Record the real Issue URL.
5. If the result is `unknown`, stop and inspect the remote repository before any
   further action. Do not retry to obtain a better-looking demo result.

## When credentials or services are unavailable

Demonstrate the local upload and the explicit missing-service error once the
integrated app supports them. Explain that requirement extraction and repository
analysis require live providers. Do not show a fake checklist as a live result.

Offline tests with explicitly injected Gemini/MCP fakes can demonstrate local
control flow, proposal immutability, and duplicate prevention. Label that evidence
as offline. It establishes neither model quality nor access to a GitHub repository.

Project `campusgdg-511817` has been supplied. Authentication and access to that
project are still required; its ID is neither a credential nor evidence of IAM
permissions. With the Google Cloud CLI installed, the Windows helper starts the
interactive ADC flow and stores its files under ignored `tmp/gcloud-config`:

```powershell
.\scripts\google_login.ps1
```

Use an account authorized for this project, and keep subsequent cloud commands in
the same PowerShell session. For local Vertex-based analysis, set
`GOOGLE_GENAI_USE_VERTEXAI=true` and `GOOGLE_CLOUD_PROJECT=campusgdg-511817`, and
configure the server-side GitHub credential. The cloud runtime additionally needs
its own identity and Secret Manager access. Follow [DEPLOYMENT.md](DEPLOYMENT.md)
for the exact preparation, dry-run, deployment, and invocation commands.

The [cloud dry-run artifact](../artifacts/cloud-dry-run.json) is local SDK/source
packaging evidence. Present cloud deployment as unverified until an actual resource
and successful invocation exist. A local server or container does not establish it.

## Manual verification after integration

- Upload a supported document and verify the source text and repository identity.
- Confirm missing tokens cannot read or mutate an assignment.
- Check that every quoted source and evidence link matches the displayed finding.
- Confirm missing or insufficient evidence stays `unverified`.
- Verify the Issue preview contains the exact immutable content being approved.
- With writes disabled, verify no real Issue is created.
- Check narrow-screen layout, keyboard focus, readable errors, and safe rendering
  of document/repository text. Do not include secrets in captured evidence.

## Record the result

Use a report with distinct rows; leave an unperformed check as **not run**.

| Category | What counts as evidence |
| --- | --- |
| Local checks | Revision, exact commands, outcomes, and relevant failures. |
| Browser | Actual exercised flows, viewport/keyboard checks, and observations. |
| Gemini | Model, timestamp, real analysis response, and any limitations. |
| GitHub MCP reads | Repository, discovered/called tools, and actual returned evidence. |
| Issue write | Exact approved proposal, action state, Issue URL, matching readback. |
| Cloud | Project/region, deployed resource name, and invocation outcome. |
| Development coordination | Ruflo task records and executor results for each role. |

Write final outcomes in [VERIFICATION.md](VERIFICATION.md), with test scope in
[TESTING.md](TESTING.md). Include the [security limitations](SECURITY.md) when
explaining what the controls establish.

Worker registration is not task completion. Passing offline tests is not live E2E.
An Issue creation response without readback is not verified success. A deployment
script or dry run is not a deployed Agent Engine resource.
