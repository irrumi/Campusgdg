# Presentation outline

Suggested length: six slides and a short demo. Use screenshots and numbers only
from a recorded run. [VERIFICATION.md](VERIFICATION.md) holds the current evidence;
keep the slides aligned with it rather than repeating a changing test count.

## 1. The problem: requirements are separated from evidence

An assignment describes expected behavior, while the evidence sits across source
files, Issues, and CI. A reviewer must connect them manually and distinguish an
actual gap from something that cannot yet be checked.

**Show:** one real requirement and its source document. Do not invent adoption,
time savings, grading improvements, or user research.

## 2. The product: a checklist that can be inspected

CampusPulse accepts an assignment and a GitHub repository URL. It extracts quoted
requirements, gathers bounded repository evidence, and presents `satisfied`,
`not_satisfied`, or `unverified` findings with concise explanations.

**Show:** a real finding beside its source quote and repository evidence when a live
analysis exists. If a provider fails, show the actual service error.
Label any offline test output as an injected-fake test.

## 3. The agent: Gemini chooses bounded reads

Gemini performs structured requirement extraction and chooses repository, file,
Issue, and CI reads through the official GitHub MCP server. The application checks
source references, evidence IDs, and the tool budget. It never runs repository code.

**Show:** the [architecture diagram](ARCHITECTURE.md#product-data-flow) and the
[recorded official MCP reads](../artifacts/live-github-mcp.json). All four read
categories passed against the recorded repository. This verifies gateway access;
it does not prove a successful Gemini analysis or assignment compliance.

## 4. Human control: proposal before action

A recommendation can become an immutable Issue proposal. The user reviews the
exact repository, title, and body. The backend checks explicit approval, the
proposal hash, the write setting, and an atomic claim before dispatch. A matching
readback establishes success; an uncertain result remains `unknown`.

**Show:** the Issue preview and separate approval control. The standard demo stops
at preview. A real write needs repository-owner authorization and a real receipt.

## 5. Engineering and deployment

The stack is Python, FastAPI, Pydantic, SQLite, `google-genai`, and the MCP Python
SDK, with a browser UI that needs no frontend build. The cloud wrapper exposes only
`async_query`, invokes the actual agent, and forces GitHub writes off. Deployment
uses the pinned `google-cloud-agentplatform` 2.5.0 source-package API and an Agent
Engine `reasoningEngines` resource. The local SDK/dry run is verified; deployment
and invocation still need real service responses.

Exactly ten native Codex workers ran in waves of at most three, linked to Ruflo
3.56.10 agent/task records and isolated worktrees. The initial Claude OAuth failures
are recorded separately and do not count as implementation work. These development
workers are separate from the product's Gemini agent.

**Show:** completed implementation and check evidence. If no deployed resource and
successful invocation exist, label cloud deployment **unverified**.

## 6. Evidence, limitations, next steps

Report local tests, browser checks, live Gemini/MCP access, approved writes, and
cloud deployment in separate rows. Explain the limits: text-bearing input,
bounded repository coverage, possible ambiguity, and no guarantee of external
assignment acceptance.

Project `campusgdg-511817` is known, but Google account authentication, ADC, and IAM
access still block cloud verification. The local Gemini Developer API is verified
separately using an API key. No real Issue write
has been performed; one requires explicit approval of its exact proposal. GitHub
push and final browser/integration outcomes belong in the canonical verification
record. Cloud login/access must precede deployment and invocation under the
[deployment runbook](DEPLOYMENT.md).

**Close:** invite the reviewer to inspect one requirement, its quote, its evidence,
and the proposed next action. Link to the [demo runbook](DEMO.md) for the procedure.
