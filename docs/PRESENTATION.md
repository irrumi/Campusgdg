# Presentation outline

Suggested length: six slides and a short demo. Use screenshots and numbers only
from a recorded run. At the documentation worker's handoff, application execution,
live integrations, and cloud deployment were not verified.

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

**Show:** a real finding beside its source quote and repository evidence. If live
services are unavailable, show the local upload and actual configuration state.

## 3. The agent: Gemini chooses bounded reads

Gemini performs structured requirement extraction and chooses repository, file,
Issue, and CI reads through the official GitHub MCP server. The application checks
source references, evidence IDs, and the tool budget. It never runs repository code.

**Show:** the [architecture diagram](ARCHITECTURE.md#product-data-flow), followed by
actual tool activity if available. A model response is a conclusion to inspect,
not independent proof that a requirement is met.

## 4. Human control: proposal before action

A recommendation can become an immutable Issue proposal. The user reviews the
exact repository, title, and body. The backend checks explicit approval, the
proposal hash, the write setting, and an atomic claim before dispatch. A matching
readback establishes success; an uncertain result remains `unknown`.

**Show:** the Issue preview and separate approval control. The standard demo stops
at preview. A real write needs repository-owner authorization and a real receipt.

## 5. Engineering and deployment

The stack is Python, FastAPI, Pydantic, SQLite, `google-genai`, and the MCP Python
SDK, with a browser UI that needs no frontend build. Vertex AI Agent Engine is the
target for hosted analysis. The cloud wrapper must invoke the actual agent and
must not expose an Issue-writing method.

Ruflo coordinates ten development roles; Codex executors implement the current
run's work. These development workers are separate from the product's Gemini
agent. Worktree ownership and shared contracts make the integration reviewable.

**Show:** completed implementation and check evidence. If no deployed resource and
successful invocation exist, label cloud deployment **unverified**.

## 6. Evidence, limitations, next steps

Report local tests, browser checks, live Gemini/MCP access, approved writes, and
cloud deployment in separate rows. Explain the limits: text-bearing input,
bounded repository coverage, possible ambiguity, and no guarantee of external
assignment acceptance.

At this handoff, missing cloud project configuration and credentials block live
cloud verification. Integration, local verification, live read analysis, and
deployment verification remain separate gates. Replace this status only with
observed results, not expected behavior.

**Close:** invite the reviewer to inspect one requirement, its quote, its evidence,
and the proposed next action. Link to the [demo runbook](DEMO.md) for the procedure.
