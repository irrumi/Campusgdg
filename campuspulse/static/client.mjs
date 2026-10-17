export const SESSION_KEY = "campuspulse.assignment.v1";
export const STATUS_LABELS = Object.freeze({ satisfied: "Satisfied", not_satisfied: "Not satisfied", unverified: "Unverified" });

export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = "ApiError"; this.status = status; }
}

export function safeGitHubUrl(value) {
  if (typeof value !== "string" || /[\s\\\x00-\x1f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.username || url.password || url.port) return null;
    return url.href;
  } catch { return null; }
}

export function validRepositoryUrl(value) {
  const safe = safeGitHubUrl(value);
  if (!safe || /[%?#]/.test(value)) return false;
  if (!/^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_.-]+\/?$/i.test(value)) return false;
  const url = new URL(safe);
  return /^\/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\/[A-Za-z0-9_.-]+\/?$/.test(url.pathname)
    && !url.pathname.split("/").some(part => part === "." || part === "..");
}

export function statusFor(requirement, analysis) {
  const finding = analysis?.findings?.find(item => item.requirement_id === requirement.id);
  const status = Object.hasOwn(STATUS_LABELS, finding?.status) ? finding.status : "unverified";
  return { finding, status };
}

export function summarize(analysis) {
  const counts = { total: 0, satisfied: 0, not_satisfied: 0, unverified: 0 };
  for (const requirement of analysis?.requirements || []) {
    counts.total += 1;
    counts[statusFor(requirement, analysis).status] += 1;
  }
  return counts;
}

export function configuration(health) {
  if (!health) return { ready: false, writes: false, title: "Service status unavailable", message: "Check the local server connection. Upload and analysis availability could not be confirmed." };
  if (health.analysis_backend === "vertex") {
    const ready = health.analysis_configured === true;
    return { ready, writes: health.allow_github_writes === true && health.github_configured === true,
      title: ready ? "Cloud analysis configured" : "Cloud analysis needs configuration",
      message: ready ? "Analysis runs in Vertex AI. Access is checked when you analyze; uploads are parsed on this server."
        : "Configure the cloud agent on the server to analyze. You can still upload and read your assignment." };
  }
  const missing = [];
  if (health.gemini_configured !== true) missing.push("Gemini");
  if (health.github_configured !== true) missing.push("GitHub MCP");
  return { ready: !missing.length, writes: health.allow_github_writes === true,
    title: missing.length ? "Analysis needs configuration" : "GitHub writes are disabled",
    message: missing.length ? `Configure ${missing.join(" and ")} on the server to analyze. You can still upload and read your assignment locally.` : "You can analyze and preview Issues. Creating an Issue requires server-side write access." };
}

function loadSession(storage) {
  try {
    const value = JSON.parse(storage?.getItem(SESSION_KEY) || "null");
    if (!value || typeof value.id !== "string" || !/^[a-zA-Z0-9_-]+$/.test(value.id) || typeof value.token !== "string" || !value.token || value.token.length > 512) return null;
    return { id: value.id, token: value.token, locked: Array.isArray(value.locked) ? value.locked.filter(item => typeof item === "string") : [] };
  } catch { return null; }
}

export class GuardianClient {
  constructor({ transport = globalThis.fetch?.bind(globalThis), storage = null } = {}) {
    this.transport = transport;
    this.storage = storage;
    this.session = loadSession(storage);
    this.storageAvailable = true;
    this.activeApproval = false;
  }
  persist() {
    try { this.storage?.setItem(SESSION_KEY, JSON.stringify(this.session)); }
    catch { this.storageAvailable = false; }
  }
  clearSession() {
    this.session = null;
    try { this.storage?.removeItem(SESSION_KEY); } catch { this.storageAvailable = false; }
  }
  async request(path, { method = "GET", body, authenticated = true } = {}) {
    const headers = { Accept: "application/json" };
    if (authenticated) {
      if (!this.session) throw new ApiError("Upload an assignment first.");
      headers["X-Assignment-Token"] = this.session.token;
    }
    if (body !== undefined && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    let response;
    try { response = await this.transport(path, { method, headers, body, credentials: "same-origin", cache: "no-store", redirect: "error" }); }
    catch { throw new ApiError("The server connection was interrupted. Check the connection and activity history."); }
    let value;
    try { value = await response.json(); }
    catch { throw new ApiError("The server returned an unreadable response.", response.status); }
    if (!response.ok) throw new ApiError(typeof value?.detail === "string" ? value.detail : "The request could not be completed.", response.status);
    return value;
  }
  assignmentPath(suffix = "") {
    if (!this.session) throw new ApiError("Upload an assignment first.");
    return `/api/assignments/${encodeURIComponent(this.session.id)}${suffix}`;
  }
  health() { return this.request("/api/health", { authenticated: false }); }
  async upload(file, repository) {
    if (!validRepositoryUrl(repository)) throw new ApiError("Enter an HTTPS github.com owner/repository URL without a query or fragment.");
    const body = new FormData();
    body.append("file", file);
    body.append("repository_url", repository);
    const result = await this.request("/api/assignments", { method: "POST", body, authenticated: false });
    if (!result?.assignment?.id || typeof result.token !== "string" || !result.token) throw new ApiError("The server did not return assignment access. Please upload again.");
    this.session = { id: result.assignment.id, token: result.token, locked: [] };
    this.persist();
    return result.assignment;
  }
  assignment() { return this.request(this.assignmentPath()); }
  analyze() { return this.request(this.assignmentPath("/analyze"), { method: "POST" }); }
  events() { return this.request(this.assignmentPath("/events")); }
  isLocked(requirementId) { return !!this.session?.locked.includes(requirementId); }
  propose(requirementId) {
    if (this.isLocked(requirementId)) throw new ApiError("An Issue approval was already attempted for this requirement. Inspect GitHub and the activity history before taking further action.");
    return this.request(this.assignmentPath("/actions"), { method: "POST", body: { requirement_id: requirementId } });
  }
  async approve(action, explicitlyApproved) {
    if (explicitlyApproved !== true) throw new ApiError("Explicit approval of the exact Issue is required.");
    if (!this.session || action.assignment_id !== this.session.id || action.status !== "pending" || !action.proposal_hash || this.isLocked(action.requirement_id) || this.activeApproval) throw new ApiError("This proposal cannot be approved again. Review the activity history.");
    // Persist BEFORE the request. A dropped response must not enable a second write after reload.
    this.session.locked.push(action.requirement_id);
    this.persist();
    this.activeApproval = true;
    try {
      return await this.request(this.assignmentPath(`/actions/${encodeURIComponent(action.id)}/approve`), {
        method: "POST", body: { proposal_hash: action.proposal_hash, approved: true },
      });
    } finally { this.activeApproval = false; }
  }
}
