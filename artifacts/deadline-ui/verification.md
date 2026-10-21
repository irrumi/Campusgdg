# Deadline mode verification — 2026-10-22

## Automated checks

Latest source: `.venv/Scripts/python.exe -m pytest -q` — **842 passed**, 30.85 seconds.
Two dependency deprecation warnings (Starlette/httpx and Agent Platform/Pydantic) remain.
Ruff passed for the changed Python source and tests. Frontend Design Premium's strict
static audit returned zero findings; its latest JSON is in ignored `tmp/deadline-pr-ui-audit.json`.
Static results are not a claim of full accessibility or browser compliance.

The full Streamlit regression follows real UI code through upload selection, review,
effort estimates, planning, application, calendar navigation, returning with estimates
preserved, the one-hour constraint, reapplication without duplicates and scoped Undo.
External-service boundaries are explicit test fakes. PDF parsing, planning, calendar
revisions and source/constraint validation use production functions.

Initial Windows sandbox runs could not create asyncio's loopback socket-pair.
A minimal reproduction stopped in `socket.accept` while initializing the event loop.
Completed tests ran with local socket access outside that sandbox, not against live services.

## Browser inspection

Browser harness: `tests/deadline_browser_app.py`, explicitly labelled **TEST ONLY** in
both source and UI. It injects fake review/service responses; real GCP/GitHub are not called.
Do not use these screenshots or fixtures as proof of cloud deployment or live MCP execution.

Inspected with the in-app browser at 1280×900 and 390×844:

- Desktop before/after timelines show an existing block on 23 October being moved to
  24 October after the one-hour daily restriction. Unchanged sessions stay in place.
- The replaced/new intervals are highlighted with text, not just color.
- Applying disables the same Apply action and marks the plan as applied locally.
- The regular calendar contains the planned sessions and retains the unrelated lecture.
- On mobile the timelines stack in reading order. Measured document width equals
  viewport width (390px); no horizontal document overflow was observed.
- Native tabs, radio navigation and buttons remain in the accessibility tree.
  Full keyboard, screen-reader, touch and 200% zoom matrices were not performed.

Evidence: `replanning-laptop.jpg`, `replanning-mobile.jpg`.

## Live-service boundary

The following initial-stage limitations are historical. The user-assisted review
and actual public MCP reads recorded below supersede the corresponding pending items.

No credentials were read, no cloud resources were deployed, and no Issues were created.
The current shell had no project/token/Agent Engine/API-key-hash configuration.
Actual Gemini/GitHub MCP invocation and rollout of this revision remain unverified.
For the existing-gateway local UI, follow `docs/LOCAL_DEMO.md`. A cloud rollout is
a separate operator action outside this rehearsal.

## Existing gateway rehearsal (real requests, no injected responses)

- Direct HTTPS GET `/api/v1/chat`: HTTP 200,
  `{"status":"ok","backend":"vertex-agent-engine"}`.
- A real read-only chat POST without an application key: HTTP 401,
  `Invalid or missing API key`. `AGENT_API_KEY` was absent in the invoking process.
- Default proxy routing had timed out; direct routing succeeded with TLS verification
  enabled. The launcher provides an explicit `--direct` option scoped to its child process.
- Started the production UI on `127.0.0.1:8502` with the existing HTTPS gateway.
  Existing processes on 8501/8000 were left intact. Restarted only our 8502 process
  to load the modified planning module.
- In a clean real browser session, uploaded `examples/deadline-demo/assignment.txt`
  and submitted the real repository URL. The actual gateway returned HTTP 401.
  UI retained the file/repository, offered explicit retry and did not fabricate a review.
  Screenshot: `gateway-auth-required.jpg`.
- Left the settings form open for the user to enter the **existing gateway key**.
  No Gemini/GitHub keys were requested by the default launcher.

The current UI executes the real `plan_before_deadline` function locally after a review,
labels the receipt `execution: local`, and does not mark cloud connectivity verified by
a local calculation. The regression exercises planning/replanning with the URL/key cleared;
only analysis crosses the explicitly faked service boundary in that test.

Full live analysis, tool receipts from the cloud agent and live replanning after that
analysis remain pending application authentication and runtime compatibility verification.
No claim of successful live Gemini/MCP or full live rehearsal is made.
Ruff passed; strict static audit has zero findings (`tmp/deadline-gateway-ui-audit.json`).

## Legacy protocol compatibility

The user observed a successful old-format response rather than a `deadline` object.
Added an explicit compatibility retry, retaining the existing key and file. A new
review sends real JSON tasks through the same cloud chat gateway and actually calls
our Custom Public GitHub MCP. It reuses Guardian's source and evidence validation;
generic chat answers, duplicate JSON keys and invented citations cannot confirm work.

Two integration defects found during user-assisted attempts were fixed:

- Progress callbacks now run on the Streamlit script thread, while HTTP runs in a
  worker. A real UI-code regression checks that an empty extraction reaches the
  document-specific error rather than `NoSessionContext` / a generic failure.
- The published gateway's **actual message limit is 4000 characters**, confirmed from
  its source at commit `e1fc985`. Earlier compatibility code had assumed the new local
  limit and produced HTTP 413. Complete documents, schemas and evidence now travel in
  the already supported `student_context.campuspulse_review`; instructions alone are
  in `message`. Tests enforce this exact 4000-character contract and check that a
  longer document reaches context unchanged. Context has an explicit 200000-character
  local cap; no silent assignment truncation occurs.

Real stdio MCP smoke (no token): root listing, README (5116 chars), gateway source
(3967 chars) and CI were retrieved for the pinned commit
`e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2`. CI run `39668268608` reported
`completed/success`; a failed CI is not claimed. File forwarding/planner gaps are
the observable code problems in the prepared assignment.

The custom server uses the low-level MCP Python SDK, avoiding FastMCP's implicit
dotenv settings loader. A second real stdio read of README passed after this change.
The child receives no application/provider keys and its HTTP client disables implicit
environment/netrc authentication. Files and CI stay pinned to one observed commit;
partial data and public-only access are disclosed in the report.

The initial compatibility review needed the grounding/context fixes recorded next.
No private credentials were requested or printed. GCP and existing user sessions
were not restarted or modified for these compatibility fixes.

## User-assisted grounding rehearsal and contextual clarification

In the user's separately authenticated 8502 browser, the uploaded text required README
setup commands and a successful latest CI run. The user supplied an actual receipt for
the complete README read at `e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2`
(source ID `fea4ebbdef8742c880c0a07bcc91956d`, retrieved
`2026-10-21T22:31:06.760235+00:00`, `truncated: false`). The initial model verdict
failed grounding, while CI had an overbroad extraction ambiguity. The prior report
did not record the exact README rejection reason; it is not inferred here.

Added exact grounding diagnostics, Russian UI messages, actual ambiguity text, a separate
all-sources expander, short review-scoped source IDs, explicit citation fields and one
bounded repair using the same evidence. Literal JSON field fragments help quote CI
without reformatting its source. All quotes remain subject to strict source matching.

An extraction ambiguity can now be reviewed once after actual evidence collection.
Resolution needs real source IDs/exact quotes; original ambiguity and explanation stay
in verification receipts. Genuine missing author criteria remain unverified. This stage
changes no assignment wording and does not itself confirm a requirement; final findings
still undergo grounding. Tests reject invented resolution sources/quotes and duplicate
resolutions, and preserve an unspecified performance threshold.

The user subsequently reported **README confirmed**, then **CI confirmed**, in the
same existing gateway session. These are user-assisted observations, not an automated
inspection of that authenticated browser. The agent's exact deployed model remains
undisclosed. A two-requirement file whose requirements are fulfilled has no pending work;
it must not be forced to produce a workload for a replanning demo.

Independent real stdio MCP checks after these changes retrieved the full README
(5116 characters, source `e1`) and actual CI run `39668268608` (`Offline checks`,
`main`, `completed`, `success`, created `2026-10-21T20:12:26Z`) for the same commit.
Every supplied JSON quote option was verified as an exact substring of that tool text.
No model conclusion was substituted for the run result.

The updated UI explanations and sources expander were inspected in a separate
**explicitly fake browser-test harness**, not the user's session. Screenshot:
`verification-explanations-test.jpg`. Previous responsive replanning inspection remains
recorded above. Full live application/replanning after the user's authenticated review
remains pending; no cloud deployment, new credentials or external write occurred.

## Rehearsal using the user's actual exported four-requirement review

The user supplied `campuspulse-deadline.json` from their authenticated gateway session.
Its assignment SHA matches the actual prepared assignment
`3e762f047cb1ba93b3ab3752c4fa47838d611c56d1636e5689977333c4c6a5d7`.
The report has two satisfied findings (README and MCP registration) and two
not-satisfied findings (gateway file forwarding and the published planner's busy-time
handling), all referring to the published `e1fc985` snapshot. Local uncommitted fixes
do not change that published snapshot. The tools file is explicitly partial evidence;
the report's statement concerns the visible planning function, not all repository code.

Executed the real registered `plan_before_deadline` path via `run_deadline`, using this
unchanged analysis. Calendar events from the prepared pack and estimates of 180 minutes
per open requirement are explicitly **hypothetical participant inputs**, not model-derived
effort or external calendar evidence. The clock was still before the 22 October 17:00
study window. The local run produced:

- Initial: required 360 minutes, available 420, no deficit.
- Tomorrow capped at 60 minutes: available 360; the 23 October 20:00–21:00
  file-forwarding session moves to 24 October 20:00–21:00; five exact sessions remain.
- Tomorrow capped at zero: available 300; 60 minutes remain explicitly unscheduled.

Applied the initial and revised proposals to an isolated in-memory calendar with the
production `apply_plan` function. Checked no calendar collisions, no duplicate IDs,
all three unrelated demonstration events preserved, no work after the deadline,
rejection of repeated stale application, and exact restoration with production Undo.
No analysis responses were injected or regenerated for this run. No Gemini/GitHub
credentials were accessed, no network requests were necessary for these local planning
steps, and the user's actual Streamlit calendar was **not modified** by the script.

Recorded inputs, plans, tool receipts, assertions, and the source export hash are in
`live-report-rehearsal-2026-10-22.json`. This proves the actual exported review-to-planner
path and local calendar functions. Applying/replanning in the user's authenticated UI
remains a separate user-assisted verification step.

### Browser rehearsal from the actual export (port 8504)

A temporary launcher in ignored `tmp/report_rehearsal_app.py` opened the production
Streamlit UI with the actual exported analysis and prepared hypothetical calendar/effort
inputs. Its banner explicitly says this is a saved gateway review, no new model call,
and a separate local rehearsal. It supplies no fake service client or generated finding.
No key was transferred from the user's 8502 session.

Using actual browser controls, calculated the 360/420-minute plan, applied it, and
opened the ordinary calendar. Returned to the review, pressed the one-hour-tomorrow
action, observed the 23 October 20:00 block move to 24 October 20:00, and applied it.
The ordinary calendar showed exactly six study sessions plus all three demonstration
events, with the former 23 October late session absent. Apply became disabled after
application. A zero-hour constraint then produced the explicit 60-minute unscheduled
planner task in the UI. Returned to the feasible one-hour constraint and applied it.

Actual screenshots: `real-export-before.jpg`, `real-export-after.jpg`. These use real
saved model findings, not the earlier fake browser fixture. Their schedule and estimates
are still labelled hypothetical participant inputs. Port 8504 was left open as a local
deliverable; the original authenticated 8502 session was untouched. These observations
complete the export-to-UI planning rehearsal without claiming a fresh cloud invocation
or modification of the user's separate calendar.

## PR preparation: cloud packaging and complete offline checks

Aligned the chat gateway, root Agent Engine deployment CLI and requirements with
the already pinned `google-cloud-agentplatform` SDK 2.5.0. The gateway uses
`Client.runtimes.get`; the CLI uses `Client.runtimes.create` and reads the returned
`api_resource.name`. Deployment settings are explicit CLI arguments; importing the
CLI does not load local credential/configuration files. `--dry-run` validates the
actual SDK configuration without constructing a cloud client. No deployment was run.

New regressions exercise real SDK method binding around explicit fake get/query RPCs,
preserve deadline payloads through the gateway, validate Secret Manager references,
and verify the CLI's SDK result handling and credential-free dry run.

Final checks for the feature branch: **842 Python tests passed**, **13 frontend tests
passed**, JavaScript syntax checks passed for all three existing frontend entrypoints,
Ruff passed, and the strict static UI audit returned zero findings. The two previously
recorded dependency deprecation warnings remain. These offline checks are separate
from the user-assisted real gateway/MCP observations above.
An isolated build also produced the Python wheel and verified that it contains
`agent`, `campuspulse`, `campus_chat_ui` and the required UI assets. Build dependencies
were installed into a temporary isolated environment; no cloud deployment was involved.
