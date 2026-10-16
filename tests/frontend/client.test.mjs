import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, GuardianClient, SESSION_KEY, configuration, safeGitHubUrl, statusFor, summarize, validRepositoryUrl } from "../../campuspulse/static/client.mjs";

// Explicit offline fixtures. No test invokes a live service or creates a real Issue.
const repository = { owner: "course", name: "project", url: "https://github.com/course/project" };
const assignment = { id: "assignment-one", repository, document: { filename: "brief.txt", pages: [{ number: 1, text: "Provide a setup guide." }] }, analysis: null };
const analysis = {
  requirements: [{ id: "req-one", title: "Setup guide", description: "Provide setup steps.", source: { page: 1, quote: "Provide a setup guide." } }],
  findings: [{ requirement_id: "req-one", status: "not_satisfied", explanation: "The repository README has no setup steps.", evidence_ids: ["ev-one"], recommendation: "Document setup." }],
  evidence: [{ id: "ev-one", source_url: `${repository.url}/blob/main/README.md`, tool: "file", summary: "README contains only a title." }], steps: [], live: false, warnings: [],
};
const action = { id: "action-one", assignment_id: assignment.id, requirement_id: "req-one", repository, title: "Assignment: Setup guide", body: "Exact body\n<script>untrusted document text</script>", proposal_hash: "immutable-hash", status: "pending" };
const token = "offline-assignment-token";
const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(value) });
function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key), values };
}
function authenticatedClient(transport, storage = memoryStorage()) {
  storage.setItem(SESSION_KEY, JSON.stringify({ id: assignment.id, token, locked: [] }));
  return new GuardianClient({ transport, storage });
}

test("offline end-to-end client flow uses exact HTTP shapes and requires separate approval", async () => {
  const requests = [];
  const storage = memoryStorage();
  const transport = async (path, options) => {
    requests.push({ path, options });
    const number = requests.length;
    if (number === 1) {
      assert.equal(path, "/api/health");
      assert.equal(options.headers["X-Assignment-Token"], undefined);
      return response({ gemini_configured: true, github_configured: true, allow_github_writes: true, mode: "live" });
    }
    if (number === 2) {
      assert.equal(path, "/api/assignments");
      assert.equal(options.method, "POST");
      assert.equal(options.headers["X-Assignment-Token"], undefined);
      assert.equal(options.headers["Content-Type"], undefined, "browser must supply the multipart boundary");
      assert.deepEqual([...options.body.keys()], ["file", "repository_url"]);
      assert.equal(options.body.get("file").name, "brief.txt");
      assert.equal(options.body.get("repository_url"), repository.url);
      return response({ assignment, token });
    }
    assert.equal(options.headers["X-Assignment-Token"], token);
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.redirect, "error");
    if (number === 3) { assert.equal(path, `/api/assignments/${assignment.id}`); return response(assignment); }
    if (number === 4) { assert.equal(path, `/api/assignments/${assignment.id}/analyze`); assert.equal(options.method, "POST"); return response({ ...assignment, analysis }); }
    if (number === 5) { assert.equal(path, `/api/assignments/${assignment.id}/actions`); assert.deepEqual(JSON.parse(options.body), { requirement_id: "req-one" }); return response(action); }
    if (number === 6) {
      assert.equal(path, `/api/assignments/${assignment.id}/actions/${action.id}/approve`);
      assert.deepEqual(JSON.parse(options.body), { proposal_hash: action.proposal_hash, approved: true });
      assert.deepEqual(JSON.parse(storage.getItem(SESSION_KEY)).locked, [action.requirement_id], "lock exists before request execution");
      return response({ ...action, status: "succeeded", receipt: { number: 1, url: `${repository.url}/issues/1`, title: action.title, body: action.body, verified: true } });
    }
    if (number === 7) { assert.equal(path, `/api/assignments/${assignment.id}/events`); return response([{ kind: "action_succeeded", message: "Offline action readback verified." }]); }
    throw new Error("Unexpected HTTP request");
  };
  const client = new GuardianClient({ transport, storage });
  assert.equal((await client.health()).mode, "live");
  assert.deepEqual(await client.upload(new File(["Provide a setup guide."], "brief.txt"), repository.url), assignment);
  assert.deepEqual(await client.assignment(), assignment);
  assert.deepEqual((await client.analyze()).analysis, analysis);
  const preview = await client.propose("req-one");
  assert.equal(requests.filter(item => item.path.endsWith("/approve")).length, 0, "preview has no write approval");
  assert.equal(preview.body, action.body, "exact proposal text is preserved");
  const result = await client.approve(preview, true);
  assert.equal(result.receipt.verified, true);
  assert.equal((await client.events())[0].kind, "action_succeeded");
  assert.equal(requests.length, 7);
  assert.deepEqual(Object.keys(JSON.parse(storage.getItem(SESSION_KEY))).sort(), ["id", "locked", "token"]);
});

test("only HTTPS links on the exact GitHub host can become anchors", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,test", "https://github.com.evil.test/a/b", "https://evil.test/github.com/a/b", "http://github.com/a/b", "https://github.com:444/a/b", "https://token@github.com/a/b", "https://github.com\\@evil.test/a/b", "https://github.com/a/ b", "//github.com/a/b", null]) assert.equal(safeGitHubUrl(value), null, String(value));
  assert.equal(safeGitHubUrl("https://github.com/a/b/blob/main/a.py#L1"), "https://github.com/a/b/blob/main/a.py#L1");
});

test("repository input rejects paths, queries, fragments, escapes and normalized traversal", () => {
  assert.equal(validRepositoryUrl(repository.url), true);
  assert.equal(validRepositoryUrl("https://github.com/course/project.git/"), true);
  for (const value of ["https://github.com/a/b/tree/main", "https://github.com/a/b?token=x", "https://github.com/a/b#x", "https://github.com/a/%2e%2e", "https://github.com/a/../course/project", "https:github.com/a/b", "https://github.com/a/.", "https://github.com/a/..", "https://github.com/-owner/repo"]) assert.equal(validRepositoryUrl(value), false, value);
});

test("invalid repository input never reaches the transport", async () => {
  let calls = 0;
  const client = new GuardianClient({ transport: async () => { calls += 1; } });
  await assert.rejects(client.upload(new File(["brief"], "brief.txt"), "https://evil.test/o/r"), ApiError);
  assert.equal(calls, 0);
});

test("approval requires literal true and an action from this assignment", async () => {
  let calls = 0;
  const client = authenticatedClient(async () => { calls += 1; return response(action); });
  for (const approved of [undefined, false, "true", 1]) await assert.rejects(client.approve(action, approved), /Explicit approval/);
  await assert.rejects(client.approve({ ...action, assignment_id: "other" }, true), /cannot be approved/);
  await assert.rejects(client.approve({ ...action, status: "succeeded" }, true), /cannot be approved/);
  assert.equal(calls, 0);
  assert.equal(client.isLocked("req-one"), false);
});

test("ambiguous network failure remains locked after reload and permits only history reads", async () => {
  let writes = 0;
  const storage = memoryStorage();
  const client = authenticatedClient(async () => { writes += 1; throw new Error("raw transport detail and secret must not leak"); }, storage);
  await assert.rejects(client.approve(action, true), error => error.status === 0 && !error.message.includes("secret"));
  assert.equal(client.isLocked("req-one"), true);
  const restored = new GuardianClient({ storage, transport: async (path, options) => { assert.equal(options.method, "GET"); assert.ok(path.endsWith("/events")); return response([{ kind: "action_unknown" }]); } });
  await assert.rejects(restored.approve(action, true), /cannot be approved/);
  assert.throws(() => restored.propose("req-one"), /already attempted/);
  assert.equal((await restored.events())[0].kind, "action_unknown");
  assert.equal(writes, 1);
});

test("simultaneous approval clicks send at most one write", async () => {
  let finish;
  let calls = 0;
  const client = authenticatedClient(async () => { calls += 1; return await new Promise(resolve => { finish = resolve; }); });
  const first = client.approve(action, true);
  await assert.rejects(client.approve(action, true), /cannot be approved/);
  finish(response({ ...action, status: "succeeded" }));
  await first;
  assert.equal(calls, 1);
});

test("unreadable approval response and server conflicts never enable retry", async () => {
  for (const result of [response({ detail: "This action has already been claimed." }, 409), { ok: true, status: 200, json: async () => { throw new Error("Bad JSON"); } }]) {
    const client = authenticatedClient(async () => result);
    await assert.rejects(client.approve(action, true), ApiError);
    await assert.rejects(client.approve(action, true), /cannot be approved/);
    assert.equal(client.isLocked("req-one"), true);
  }
});

test("unconfigured services stay explicit and local upload still works", async () => {
  const health = configuration({ mode: "unconfigured", gemini_configured: false, github_configured: false, allow_github_writes: false });
  assert.equal(health.ready, false);
  assert.match(health.message, /Gemini and GitHub MCP/);
  assert.match(health.message, /upload/);
  const client = new GuardianClient({ transport: async () => response({ assignment, token }) });
  assert.equal((await client.upload(new File(["brief"], "brief.txt"), repository.url)).id, assignment.id);
  assert.equal(configuration({ gemini_configured: true, github_configured: true, allow_github_writes: false }).writes, false);
  assert.equal(configuration(null).ready, false);
});

test("503 configuration errors preserve the parsed assignment session", async () => {
  const client = authenticatedClient(async () => response({ detail: "Configure Gemini and GitHub MCP." }, 503));
  await assert.rejects(client.analyze(), error => error.status === 503 && error.message === "Configure Gemini and GitHub MCP.");
  assert.equal(client.session.id, assignment.id);
  assert.equal(client.session.token, token);
});

test("missing or unknown findings remain unverified in checklist totals", () => {
  const partial = { requirements: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }], findings: [{ requirement_id: "a", status: "satisfied" }, { requirement_id: "b", status: "not_satisfied" }, { requirement_id: "d", status: "invented" }] };
  assert.deepEqual(summarize(partial), { total: 4, satisfied: 1, not_satisfied: 1, unverified: 2 });
  assert.equal(statusFor({ id: "c" }, partial).status, "unverified");
  assert.deepEqual(summarize(null), { total: 0, satisfied: 0, not_satisfied: 0, unverified: 0 });
});

test("corrupt session data is ignored and unavailable session storage does not lose active access", async () => {
  const invalid = memoryStorage();
  invalid.setItem(SESSION_KEY, '{"id":"../unsafe","token":"x"}');
  const corrupt = new GuardianClient({ storage: invalid });
  assert.equal(corrupt.session, null);
  await assert.rejects(corrupt.request("/api/assignments/anything"), /Upload/);
  const blockedStorage = { getItem: () => null, setItem: () => { throw new Error("Storage disabled"); } };
  const client = new GuardianClient({ storage: blockedStorage, transport: async () => response({ assignment, token }) });
  await client.upload(new File(["brief"], "brief.txt"), repository.url);
  assert.equal(client.storageAvailable, false);
  assert.equal(client.session.token, token);
});
