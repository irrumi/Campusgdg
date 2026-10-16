/* Optional, offline DOM smoke. No browser or external service is controlled.
   npm install --prefix tests/frontend/.dom-runtime --no-save --package-lock=false linkedom@0.18.13
   node tests/frontend/dom-smoke.mjs
   The production interface needs no dependency installation or build. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { parseHTML } = require("./.dom-runtime/node_modules/linkedom");
const { window, document } = parseHTML(await readFile(new URL("../../campuspulse/static/index.html", import.meta.url), "utf8"));
globalThis.window = window;
globalThis.document = document;
window.HTMLElement.prototype.scrollIntoView = () => {};
const stored = new Map();
window.sessionStorage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) };
const repository = { owner: "offline", name: "test", url: "https://github.com/offline/test" };
const assignment = { id: "dom-assignment", repository, document: { filename: "brief.txt", pages: [{ number: 1, text: "Document setup." }] }, analysis: null };
const analysis = { requirements: [{ id: "req", title: "Setup guide", description: "Document setup.", source: { page: 1, quote: "Document setup." } }], findings: [{ requirement_id: "req", status: "not_satisfied", explanation: "Setup is missing.", evidence_ids: ["ev"], recommendation: "Add setup instructions." }], evidence: [{ id: "ev", tool: "file", source_url: `${repository.url}/blob/main/README.md`, summary: "Only a title exists.", retrieved_at: "2026-10-16T12:00:00Z" }], steps: [{ kind: "tool", tool: "file", message: "Read README.", timestamp: "2026-10-16T12:00:00Z" }], warnings: [], live: false };
const action = { id: "dom-action", assignment_id: assignment.id, requirement_id: "req", repository, title: "Assignment: Setup guide", body: "Exact body\n<img src=x onerror=alert(1)>", proposal_hash: "dom-hash", status: "pending" };
const requests = [];
globalThis.fetch = async (path, options) => {
  requests.push({ path, options });
  let value;
  if (path === "/api/health") value = { mode: "live", gemini_configured: true, github_configured: true, allow_github_writes: true };
  else if (path === "/api/assignments") value = { assignment, token: "offline-token" };
  else if (path.endsWith("/events")) value = [{ kind: "offline_test", message: "Injected offline test event.", timestamp: "2026-10-16T12:00:00Z" }];
  else if (path.endsWith("/analyze")) value = { ...assignment, analysis };
  else if (path.endsWith("/actions")) value = action;
  else if (path.endsWith("/approve")) value = { ...action, status: "succeeded", receipt: { number: 1, url: `${repository.url}/issues/1`, title: action.title, body: action.body, verified: true } };
  else throw new Error(`Unexpected offline test path: ${path}`);
  return { ok: true, status: 200, json: async () => structuredClone(value) };
};
const $ = id => document.getElementById(id);
async function settled(check) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.ok(check(), "DOM did not reach the expected state");
}
function click(element) { element.dispatchEvent(new window.Event("click", { bubbles: true, cancelable: true })); }

await import("../../campuspulse/static/bootstrap.js");
await settled(() => document.documentElement.dataset.campuspulseBoot === "ready");
assert.equal($("service-label").textContent, "Services configured");
const input = $("assignment-file");
Object.defineProperty(input, "files", { value: [new File(["Document setup."], "brief.txt")] });
input.dispatchEvent(new window.Event("change"));
assert.equal($("file-label").textContent, "brief.txt");
$("repository-url").value = repository.url;
$("upload-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
await settled(() => !$("source-panel").hidden && !$("analyze-button").disabled);
assert.match($("source-pages").textContent, /Document setup/);
click($("analyze-button"));
await settled(() => $("requirements").querySelector("[data-propose]") && !$("analyze-button").disabled);
assert.match($("requirements").textContent, /Not satisfied/);
assert.match($("analysis-warnings").textContent, /Offline test analysis/);
click($("requirements").querySelector("[data-propose]"));
await settled(() => !$("action-preview").hidden && !$("approve-checkbox").disabled);
assert.equal($("issue-body").textContent, action.body);
assert.equal($("issue-body").querySelector("img"), null, "untrusted content stays text");
assert.equal(requests.filter(item => item.path.endsWith("/approve")).length, 0);
assert.equal($("approve-button").disabled, true);
$("approve-checkbox").checked = true;
$("approve-checkbox").dispatchEvent(new window.Event("change"));
assert.equal($("approve-button").disabled, false);
click($("approve-button"));
await settled(() => $("action-status").textContent === "Created");
assert.equal(requests.filter(item => item.path.endsWith("/approve")).length, 1);
assert.equal($("approve-button").hidden, true);
assert.match($("issue-receipt").textContent, /Open Issue #1/);
assert.equal($("error").hidden, true);
console.log("PASS: real DOM bootstrap, health, file change, upload, analysis, safe proposal rendering, separate approval, and receipt using injected offline transport.");
