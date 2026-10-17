import { GuardianClient, STATUS_LABELS, configuration, safeGitHubUrl, statusFor, summarize } from "./client.mjs?v=20261017-1";

const $ = id => document.getElementById(id);
let storage = null;
try { storage = window.sessionStorage; } catch { /* The current tab can still work in memory. */ }
const client = new GuardianClient({ storage });
const state = { assignment: null, health: null, events: [], action: null, filter: "all", busy: null };

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined && value !== null) element.textContent = String(value);
  return element;
}
function link(value, label) {
  const url = safeGitHubUrl(value);
  if (!url) return node("span", "muted", label || "Source link unavailable");
  const anchor = node("a", "", label || url);
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}
function showMessage(message = "", isError = false) {
  const target = $(isError ? "error" : "notice");
  target.textContent = message;
  target.hidden = !message;
  if (message) $(isError ? "notice" : "error").hidden = true;
}
function clearMessages() { $("notice").hidden = true; $("error").hidden = true; }
function formattedTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function setBusy(kind) {
  state.busy = kind;
  $("upload-button").disabled = !!kind;
  $("assignment-file").disabled = !!kind;
  $("repository-url").disabled = !!kind;
  $("refresh-health").disabled = !!kind;
  $("refresh-events").disabled = !!kind || !state.assignment;
  $("upload-button").firstChild.textContent = kind === "upload" ? "Reading assignment… " : "Add assignment ";
  $("analyze-button").firstChild.textContent = kind === "analyze" ? "Analyzing… " : state.assignment?.analysis ? "Analyze again " : "Analyze repository ";
  $("analyze-button").disabled = !!kind || !state.assignment || !configuration(state.health).ready;
  $("analysis-progress").hidden = kind !== "analyze";
  $("checklist-panel").setAttribute("aria-busy", String(kind === "analyze"));
  document.querySelectorAll("[data-propose]").forEach(button => { button.disabled = !!kind || client.isLocked(button.dataset.propose); });
  updateApproval();
}
function updateApproval() {
  const action = state.action;
  const canApprove = !!action && action.status === "pending" && !client.isLocked(action.requirement_id);
  const writes = configuration(state.health).writes;
  $("approval-label").hidden = !canApprove;
  $("approve-checkbox").disabled = !!state.busy || !writes;
  $("approve-button").hidden = !!action && !canApprove;
  $("approve-button").disabled = !canApprove || !!state.busy || !writes || !$("approve-checkbox").checked;
  $("approve-button").firstChild.textContent = state.busy === "approve" ? "Creating & checking… " : "Approve & create Issue ";
  $("write-help").hidden = !!action && !canApprove;
  $("write-help").textContent = writes ? "This sends one write to GitHub, followed by a readback check." : "GitHub writes are disabled on the server. You can still review this preview.";
}
function renderHealth() {
  const config = configuration(state.health);
  $("service-status").classList.toggle("ready", config.ready);
  const readyLabel = state.health?.analysis_backend === "vertex" ? "Vertex AI configured" : "Services configured";
  $("service-label").textContent = state.health ? config.ready ? readyLabel : "Setup needed" : "Status unavailable";
  $("config-notice").hidden = config.ready && config.writes;
  $("config-title").textContent = config.title;
  $("config-message").textContent = config.message;
  setBusy(state.busy);
}
async function refreshHealth() {
  try { state.health = await client.health(); }
  catch { state.health = null; }
  renderHealth();
}
function renderJourney() {
  const current = state.assignment?.analysis ? "review" : state.assignment ? "analyze" : "upload";
  const order = ["upload", "analyze", "review"];
  for (const [index, name] of order.entries()) {
    const item = $(`stage-${name}`);
    item.classList.toggle("current", name === current);
    item.classList.toggle("complete", index < order.indexOf(current));
    if (name === current) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
}
function renderSource() {
  const assignment = state.assignment;
  $("source-panel").hidden = !assignment;
  $("guide").hidden = !!assignment;
  if (!assignment) return;
  const pages = assignment.document.pages;
  $("source-meta").replaceChildren(
    node("p", "source-filename", assignment.document.filename),
    node("p", "source-meta-line", `${pages.length} source ${pages.length === 1 ? "section" : "sections"} · parsed locally`),
  );
  $("source-pages").className = "source-pages";
  $("source-pages").replaceChildren(...pages.map(page => {
    const section = node("section", "source-page");
    section.append(node("h3", "", `Page / paragraph ${page.number}`), node("p", "", page.text));
    return section;
  }));
}
function renderCoverage(analysis) {
  const counts = summarize(analysis);
  const assessed = counts.satisfied + counts.not_satisfied;
  $("coverage").hidden = !analysis || !counts.total;
  $("coverage-label").textContent = `${assessed} of ${counts.total} assessed`;
  $("coverage-detail").textContent = `${counts.unverified} still unverified`;
  $("coverage-bar").setAttribute("aria-label", `${counts.satisfied} satisfied, ${counts.not_satisfied} not satisfied, ${counts.unverified} unverified`);
  for (const status of Object.keys(STATUS_LABELS)) $("coverage-" + status.replace("_", "-")).style.width = `${counts.total ? counts[status] / counts.total * 100 : 0}%`;
  $("filters").hidden = !analysis || !counts.total;
  $("filters").replaceChildren(...["all", ...Object.keys(STATUS_LABELS)].map(status => {
    const button = node("button", "filter-button", `${status === "all" ? "All" : STATUS_LABELS[status]} ${status === "all" ? counts.total : counts[status]}`);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.filter === status));
    button.addEventListener("click", () => {
      state.filter = status;
      renderRequirements();
      // Keep focus on the selected filter after its DOM is refreshed.
      $("filters").querySelector('[aria-pressed="true"]')?.focus();
    });
    return button;
  }));
}
function renderEvidence(finding, analysis) {
  const list = node("ul", "evidence-list");
  const evidence = new Map(analysis.evidence.map(item => [item.id, item]));
  for (const id of finding?.evidence_ids || []) {
    const item = node("li");
    const source = evidence.get(id);
    if (!source) item.append(node("p", "", "The referenced evidence is unavailable."));
    else item.append(link(source.source_url, source.tool ? `${source.tool} · open source ↗` : "Open source ↗"), node("p", "", source.summary), node("small", "", `Retrieved ${formattedTime(source.retrieved_at)}`));
    list.append(item);
  }
  if (!list.children.length) list.append(node("li", "muted", "No repository evidence was returned for this requirement."));
  return list;
}
function renderRequirement(requirement, index, analysis) {
  const { finding, status } = statusFor(requirement, analysis);
  const article = node("article", "requirement");
  const heading = node("div", "requirement-head");
  const headingText = node("div");
  headingText.append(node("h3", "", requirement.title), node("span", `badge ${status}`, STATUS_LABELS[status]));
  heading.append(node("span", "requirement-number", String(index + 1).padStart(2, "0")), headingText);
  article.append(heading, node("p", "requirement-description", requirement.description));
  const details = node("details", "requirement-details");
  details.open = status !== "satisfied";
  details.append(node("summary", "", "Source, evidence & next step"));
  details.append(node("p", "detail-label", `Assignment · page / paragraph ${requirement.source.page}`), node("blockquote", "", requirement.source.quote));
  if (requirement.ambiguity) details.append(node("p", "ambiguity", `Needs clarification: ${requirement.ambiguity}`));
  details.append(node("p", "detail-label", "Finding"), node("p", "finding-explanation", finding?.explanation || "This requirement has no finding yet and remains unverified."));
  details.append(node("p", "detail-label", "Repository evidence"), renderEvidence(finding, analysis));
  if (finding?.recommendation) {
    details.append(node("p", "detail-label", "Recommended next step"), node("p", "recommendation", finding.recommendation));
    const button = node("button", "button secondary", client.isLocked(requirement.id) ? "Approval already attempted" : "Preview Issue ↗");
    button.type = "button";
    button.dataset.propose = requirement.id;
    button.disabled = !!state.busy || client.isLocked(requirement.id);
    button.addEventListener("click", () => propose(requirement.id));
    details.append(button);
    if (client.isLocked(requirement.id)) details.append(node("p", "field-help", "Review Activity and GitHub for the existing action. It will not be submitted again here."));
  }
  article.append(details);
  return article;
}
function renderRequirements() {
  const assignment = state.assignment;
  const analysis = assignment?.analysis;
  const requirements = analysis?.requirements || [];
  $("requirement-count").textContent = analysis ? requirements.length : "—";
  $("analysis-controls").hidden = !assignment;
  $("assignment-context").hidden = !assignment;
  if (assignment) $("assignment-context").replaceChildren(node("span", "", assignment.document.filename), link(assignment.repository.url, `${assignment.repository.owner}/${assignment.repository.name} ↗`));
  $("analysis-help").textContent = analysis ? "Run again to check the current repository. Existing Issue actions remain in Activity." : "Extract requirements and check real repository evidence.";
  renderCoverage(analysis);
  const visible = requirements.map((item, index) => ({ item, index })).filter(({ item }) => state.filter === "all" || statusFor(item, analysis).status === state.filter);
  $("requirements").replaceChildren(...visible.map(({ item, index }) => renderRequirement(item, index, analysis)));
  $("checklist-empty").hidden = !!requirements.length;
  if (assignment && !analysis) {
    $("checklist-empty").querySelector("h3").textContent = "Your brief is ready to check.";
    $("checklist-empty").querySelector("p").textContent = "Read the extracted source, then analyze to build your requirement checklist and gather repository evidence.";
  } else if (analysis && !requirements.length) {
    $("checklist-empty").querySelector("h3").textContent = "No checkable requirements returned.";
    $("checklist-empty").querySelector("p").textContent = "Review your source and the analysis warnings before running again.";
  }
  if (requirements.length && !visible.length) $("requirements").append(node("p", "muted", "No requirements match this filter."));
  const warnings = [...(analysis?.warnings || [])];
  if (analysis?.live === false) warnings.unshift("Offline test analysis — these results are not live service evidence.");
  $("analysis-warnings").hidden = !warnings.length;
  $("analysis-warnings").replaceChildren(...warnings.map(message => node("p", "", message)));
  renderJourney();
  setBusy(state.busy);
}
function renderAction() {
  const action = state.action;
  $("action-empty").hidden = !!action;
  $("action-preview").hidden = !action;
  $("approve-checkbox").checked = false;
  if (!action) { updateApproval(); return; }
  $("action-title").textContent = "Review your Issue.";
  const labels = { pending: "Awaiting approval", executing: "Write in progress", succeeded: "Created", failed: "Failed", unknown: "Outcome unconfirmed" };
  $("action-status").className = `badge ${Object.hasOwn(labels, action.status) ? action.status : "unknown"}`;
  $("action-status").textContent = labels[action.status] || "Outcome unconfirmed";
  $("action-repository").replaceChildren(link(action.repository.url, `${action.repository.owner}/${action.repository.name} ↗`));
  $("issue-title").textContent = action.title;
  $("issue-body").textContent = action.body;
  $("proposal-fingerprint").textContent = `Proposal ${action.id} · ${action.proposal_hash.slice(0, 12)}`;
  $("action-message").textContent = action.error || (action.status === "succeeded" && action.receipt?.verified ? "GitHub created this Issue and its title and body were read back and verified." : action.status === "pending" ? "This is a saved proposal. Review all of the text before approving." : "Review the activity history before taking further action.");
  $("issue-receipt").replaceChildren();
  if (action.receipt) $("issue-receipt").append(link(action.receipt.url, `Open Issue #${action.receipt.number} ↗`));
  updateApproval();
}
function renderEvents() {
  const events = [...state.events].reverse();
  $("activity-empty").hidden = !!events.length;
  $("events").replaceChildren(...events.map(event => {
    const item = node("li");
    item.append(node("p", "", event.message), node("small", "", `${event.kind.replaceAll("_", " ")} · ${formattedTime(event.timestamp)}`));
    return item;
  }));
  const steps = state.assignment?.analysis?.steps || [];
  $("tool-activity").hidden = !steps.length;
  $("steps").replaceChildren(...steps.map(step => {
    const item = node("li");
    item.append(node("p", "", step.message), node("small", "", `${step.tool || step.kind} · ${formattedTime(step.timestamp)}`));
    return item;
  }));
}
async function refreshEvents({ quiet = false } = {}) {
  if (!state.assignment) return false;
  try { state.events = await client.events(); renderEvents(); return true; }
  catch (error) { if (!quiet) showMessage(`Activity could not be refreshed. ${error.message}`, true); return false; }
}
function renderAssignment() { renderSource(); renderRequirements(); renderEvents(); }
async function propose(requirementId) {
  if (state.busy) return;
  clearMessages();
  setBusy("propose");
  try {
    state.action = await client.propose(requirementId);
    renderAction();
    await refreshEvents({ quiet: true });
    $("action-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    $("issue-title").tabIndex = -1;
    $("issue-title").focus({ preventScroll: true });
  } catch (error) { showMessage(error.message, true); }
  finally { setBusy(null); }
}

$("assignment-file").addEventListener("change", event => {
  const file = event.target.files[0];
  $("file-label").textContent = file ? file.name : "Choose an assignment";
  $("file-help").textContent = file ? `${(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KB · ready to upload` : "PDF, DOCX, TXT or Markdown · text-based files";
});
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.busy) return;
  const file = $("assignment-file").files[0];
  if (!file) { showMessage("Choose an assignment file first.", true); return; }
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) { showMessage("Choose a PDF, DOCX, TXT or Markdown assignment.", true); return; }
  clearMessages();
  setBusy("upload");
  try {
    state.assignment = await client.upload(file, $("repository-url").value.trim());
    state.action = null;
    state.events = [];
    state.filter = "all";
    $("upload-panel").open = false;
    renderAssignment();
    renderAction();
    showMessage(client.storageAvailable && storage ? "Assignment added. Read the source, then analyze the repository when services are configured." : "Assignment added. Session storage is unavailable; keep this page open to retain access.");
    await refreshEvents({ quiet: true });
    $("checklist-title").tabIndex = -1;
    $("checklist-title").focus({ preventScroll: true });
  } catch (error) { showMessage(error.message, true); }
  finally { setBusy(null); }
});
$("analyze-button").addEventListener("click", async () => {
  if (state.busy || !state.assignment) return;
  clearMessages();
  setBusy("analyze");
  try {
    state.assignment = await client.analyze();
    state.filter = "all";
    renderAssignment();
    showMessage("Analysis saved. Review each finding and its evidence before choosing a next step.");
  } catch (error) {
    showMessage(error.message, true);
    if (error.status === 503) await refreshHealth();
  } finally {
    await refreshEvents({ quiet: true });
    setBusy(null);
  }
});
$("approve-checkbox").addEventListener("change", updateApproval);
$("approve-button").addEventListener("click", async () => {
  if (state.busy || !state.action || !$("approve-checkbox").checked) return;
  clearMessages();
  const action = state.action;
  setBusy("approve");
  try {
    state.action = await client.approve(action, true);
    renderAction();
    showMessage(state.action.status === "succeeded" && state.action.receipt?.verified ? "Issue created and verified by a GitHub readback." : "The action returned. Review its recorded status before taking any further action.");
  } catch (error) {
    state.action = { ...action, status: "unknown", error: "The outcome is not confirmed in this browser. This approval is locked to prevent another write. Inspect GitHub and Activity before creating any new proposal." };
    renderAction();
    showMessage(`${error.message} This approval will not be retried.`, true);
  } finally {
    const refreshed = await refreshEvents({ quiet: true });
    if (!refreshed) $("action-message").textContent += " Activity is currently unavailable; use Refresh when the server is reachable.";
    renderRequirements();
    setBusy(null);
  }
});
$("refresh-health").addEventListener("click", async () => { if (!state.busy) { setBusy("health"); await refreshHealth(); setBusy(null); } });
$("refresh-events").addEventListener("click", async () => { if (!state.busy) { setBusy("events"); await refreshEvents(); setBusy(null); } });

async function restore() {
  if (!client.session) return;
  setBusy("restore");
  try {
    state.assignment = await client.assignment();
    $("repository-url").value = state.assignment.repository.url;
    $("upload-panel").open = false;
    renderAssignment();
    await refreshEvents({ quiet: true });
    if (client.session.locked.length) showMessage("Assignment restored. Earlier Issue approvals remain locked in this tab; review their outcomes in Activity and GitHub.");
  } catch (error) {
    if (error.status === 404) client.clearSession();
    showMessage(`The previous assignment could not be restored. ${error.message}`, true);
  } finally { setBusy(null); }
}
await Promise.all([refreshHealth(), restore()]);
