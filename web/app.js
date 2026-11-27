// Research with Receipts: single-page UI (vanilla JS, no build step).
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = { health: null, runId: null, source: null, run: null, tab: "all", phase: null, lastQuestion: "" };
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* storage may be blocked */ } },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function short(s, n = 120) { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function host(u) { try { return new URL(u).host.replace(/^www\./, ""); } catch { return u; } }
function icon(name, cls = "ic") { return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`; }
function pad2(n) { return String(n).padStart(2, "0"); }

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const code = accessCode();
  if (code) headers["X-Access-Code"] = code;
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* not JSON */ }
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// ------------------------------------------------------------ theme

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function syncThemeIcon() {
  const dark = currentTheme() === "dark";
  $("#btn-theme").innerHTML = icon(dark ? "sun" : "moon");
  $("#btn-theme").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
$("#btn-theme").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("rwr-theme", next);
  syncThemeIcon();
});

// ------------------------------------------------------------ health, examples, recordings

const EXAMPLES = (base) => [
  { k: "Browser + receipts", i: "camera", q: "How does gVisor isolate containers, and what performance cost does it have compared to runc?" },
  { k: "Browser + Python", i: "code", q: "What are the three most populous countries today, and what share of the world population do they hold together?" },
  { k: "Hostile page", i: "shield", q: `Summarize the product page at ${base}/demo/hostile and list its price and colours.` },
];

function setChip(el, cls, text, title) {
  el.className = "chip " + cls;
  el.innerHTML = `<i class="led"></i><span>${esc(text)}</span>`;
  if (title !== undefined) el.title = title;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.health = h;
    $("#offline").hidden = true;
    setChip($("#pill-sandbox"), h.sandbox_isolated ? "ok" : "warn",
      h.sandbox_isolated ? `Docker sandbox · ${h.sandbox_runtime}` : "Mock sandbox (dev only)", "Where browsing and code run");
    const st = h.llm_status || {};
    if (st.provider === "vultr") {
      setChip($("#pill-llm"), st.ok ? "ok" : "bad", `Vultr · ${h.llm_model}${st.ok ? "" : " (unreachable)"}`, st.detail || "");
    } else {
      setChip($("#pill-llm"), "warn", "Mock policy", st.detail || "");
    }
    syncAccess();
    renderExamples(h.public_base_url);
    syncReplayToggle();
    return true;
  } catch {
    $("#offline").hidden = false;
    setChip($("#pill-sandbox"), "bad", "Server offline");
    return false;
  }
}

// Must mirror app/cassettes.py normalize_question().
function normalizeQ(q) {
  return String(q || "").trim().toLowerCase()
    .replace(/https?:\/\/[^/\s]+(?=\/demo\/)/g, "{base}")
    .replace(/[^\w{}/:.]+/g, " ").replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "");
}

function recordingFor(q) {
  const n = normalizeQ(q);
  return ((state.health && state.health.recordings) || []).find((r) => normalizeQ(r.question) === n) || null;
}

// Mirrors the server rule in app/main.py: in "auto" mode a recorded question
// is replayed whenever the live model is not reachable.
function replayForced() {
  const h = state.health || {};
  const st = h.llm_status || {};
  return h.demo_mode === "replay" || (h.demo_mode === "auto" && !(st.live && st.ok));
}

function syncReplayToggle() {
  const rec = recordingFor($("#q").value);
  const box = $("#replay");
  $("#replay-row").hidden = !rec;
  const forced = !!rec && replayForced();
  box.disabled = forced;
  $("#replay-row").title = forced ? "No live model is configured, so recorded questions are replayed." : "";
  if (!rec) box.checked = false;
  else if (forced) box.checked = true;
}

function renderExamples(base) {
  const ex = EXAMPLES(base);
  $("#examples").innerHTML = ex.map((e, i) =>
    `<button type="button" class="example" data-i="${i}">
      <span class="k">${icon(e.i)}${esc(e.k)}${recordingFor(e.q) ? '<span class="rec">recorded</span>' : ""}</span>
      <span class="q">${esc(e.q)}</span>
    </button>`).join("");
  $$(".example").forEach((b) => b.addEventListener("click", () => {
    const q = $("#q");
    q.value = ex[+b.dataset.i].q;
    autosize(); syncReplayToggle(); q.focus();
  }));
}

function autosize() {
  const q = $("#q");
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 220) + "px";
}

// ------------------------------------------------------------ history

async function loadHistory() {
  try {
    const runs = await api("/api/runs");
    const ul = $("#history");
    if (!runs.length) { ul.innerHTML = '<li class="empty">No runs yet. Your research history will appear here.</li>'; return; }
    ul.innerHTML = runs.slice(0, 40).map((r) =>
      `<li><a href="#run=${r.id}" class="${r.id === state.runId ? "active" : ""}">
        <div class="hq">${esc(short(r.question, 110))}</div>
        <div class="hs"><span class="dot ${esc(r.status)}"></span>${esc(r.status)}${r.mode === "replay" ? " · replay" : ""} · <span class="num">${r.n_receipts}</span> receipts</div>
      </a></li>`).join("");
  } catch { /* history is optional */ }
}

// ------------------------------------------------------------ view mode

function setMode(mode) {
  document.body.classList.toggle("is-run", mode === "run");
  document.body.classList.toggle("is-home", mode !== "run");
  $("#run").hidden = mode !== "run";
}

// ------------------------------------------------------------ access code

const ACCESS_HELP = "This deployment is invite-only. Enter your code to run research.";

function accessRequired() { return !!(state.health && state.health.access_code_required); }
function accessCode() { return $("#access").value.trim() || store.get("rwr-code"); }

// Show the access row, optionally as an error ("missing" or "wrong"), and put
// the cursor in it so the next keystroke goes to the right place.
function showAccess(problem = "") {
  const row = $("#access-row"), input = $("#access");
  row.hidden = false;
  row.classList.toggle("invalid", !!problem);
  $("#access-msg").textContent = problem === "wrong" ? "That access code wasn't accepted. Check it and press Research again."
    : problem === "missing" ? "Enter the access code first. Research only runs on this deployment with a valid code."
    : ACCESS_HELP;
  if (!problem) { input.removeAttribute("aria-invalid"); return; }
  input.setAttribute("aria-invalid", "true");
  input.focus({ preventScroll: true });
  if (problem === "wrong") input.select();
  row.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
  if (!reduceMotion) { row.classList.remove("nudge"); void row.offsetWidth; row.classList.add("nudge"); }
}

// The row is visible whenever the server needs a code and none is saved yet.
function syncAccess() {
  const row = $("#access-row");
  if (!accessRequired()) { row.hidden = true; return; }
  if (!store.get("rwr-code")) { if (!row.classList.contains("invalid")) showAccess(); }
  else if (!row.classList.contains("invalid")) row.hidden = true;
}

// A request was accepted: remember a typed code and fold the row away.
function acceptAccess() {
  const typed = $("#access").value.trim();
  if (typed) store.set("rwr-code", typed);
  $("#access-row").classList.remove("invalid");
  syncAccess();
}

function rejectAccess() {
  const typed = $("#access").value.trim();
  store.del("rwr-code");
  showAccess(typed ? "wrong" : "missing");
}

// ------------------------------------------------------------ run lifecycle

function setBusy(busy, label = "Research") {
  const btn = $("#go");
  btn.disabled = busy;
  btn.innerHTML = busy ? `<span class="spinner" aria-hidden="true"></span><span>${esc(label)}</span>`
    : `<span>Research</span>${icon("arrow")}`;
}

async function startRun(question, mode) {
  state.lastQuestion = question;
  if (accessRequired() && !accessCode()) { showAccess("missing"); return; }
  setBusy(true, mode === "replay" ? "Starting replay" : "Starting sandbox");
  try {
    mode = mode || ($("#replay").checked ? "replay" : "auto");
    const { id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ question, mode }) });
    acceptAccess();
    location.hash = `run=${id}`;
  } catch (e) {
    setBusy(false);
    // A missing or wrong code is fixed in place, next to the question.
    if (e.status === 401) { rejectAccess(); return; }
    const msg = e.status === 422 ? "Please ask a question of at least 5 characters." : e.message;
    setMode("run");
    renderError({ question, error: `Could not start the run: ${msg}` });
  }
}

async function openRun(id) {
  if (state.source) { state.source.close(); state.source = null; }
  setMode("run");
  state.runId = id;
  state.phase = null;
  state.tab = "all";
  $("#log").innerHTML = "";
  $("#receipts-card").hidden = true;
  renderLoading({ question: "Loading run…" });
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  let run;
  try { run = await api(`/api/runs/${id}`); } catch {
    state.lastQuestion = "";
    renderError({ question: "", error: "This run doesn't exist any more. It may have been created on another server." });
    return;
  }
  state.run = run;
  state.lastQuestion = run.question;
  const q = $("#q"); q.value = run.question; autosize(); syncReplayToggle();
  renderSandbox(run.sandbox, run.status === "done" || run.status === "failed" ? "destroyed" : "running");
  loadHistory();
  if (run.status === "done" || run.status === "failed") {
    run.events.forEach((ev, i) => addEvent(ev, false, i));
    renderRun(run);
    return;
  }
  renderLoading(run);
  $("#live").hidden = false;
  setBusy(true, run.mode === "replay" ? "Replaying" : "Researching");
  const es = new EventSource(`/api/runs/${id}/events`);
  state.source = es;
  es.onmessage = async (m) => {
    const ev = JSON.parse(m.data);
    addEvent(ev, true);
    trackPhase(ev);
    if (ev.type === "run_started" && ev.mode === "replay") { state.run.mode = "replay"; state.run.replay_of = ev.replay_of; renderLoading(state.run); }
    if (ev.type === "sandbox_created") { state.run.sandbox = ev; renderSandbox(ev, "running"); }
    if (ev.type === "sandbox_destroyed") renderSandbox(state.run.sandbox, "destroyed");
    if (ev.type === "tool_result" && ev.receipt_id) refreshReceipts();
    if (ev.type === "run_finished") {
      es.close(); state.source = null; $("#live").hidden = true;
      $$(".tl.current").forEach((el) => el.classList.remove("current"));
      setBusy(false);
      const full = await api(`/api/runs/${id}`);
      state.run = full;
      renderSandbox(full.sandbox, "destroyed");
      renderRun(full);
      loadHistory();
    }
  };
  es.onerror = () => { /* EventSource reconnects by itself; events are replayed from the start */ };
}

let refreshTimer = null;
function refreshReceipts() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/runs/${state.runId}`);
      state.run = r; renderReceipts(r);
      const n = $("#progress-count"); if (n) n.textContent = `${r.receipts.length} receipt${r.receipts.length === 1 ? "" : "s"} captured so far`;
    } catch { /* transient */ }
  }, 300);
}

// ------------------------------------------------------------ answer states

const PHASES = [
  { id: "plan", label: "Plan" }, { id: "browse", label: "Browse" }, { id: "compute", label: "Compute" },
  { id: "evidence", label: "Evidence" }, { id: "verify", label: "Verify" },
];

function trackPhase(ev) {
  let p = null;
  if (ev.type === "run_started" || ev.type === "sandbox_created") p = "plan";
  if (ev.type === "step") p = ev.action === "python" ? "compute" : ev.action === "final" ? "evidence" : "browse";
  if (ev.type === "evidence" || ev.type === "drafted" || ev.type === "requote") p = "evidence";
  if (ev.type === "verification") p = "verify";
  if (!p) return;
  const order = PHASES.map((x) => x.id);
  // Compute can follow browse and vice versa; never move backwards past evidence.
  if (state.phase && order.indexOf(state.phase) >= 3 && order.indexOf(p) < 3) return;
  state.phase = p;
  const el = $("#phases");
  if (el) el.innerHTML = phasesHtml();
}

function phasesHtml(allDone = false) {
  const order = PHASES.map((x) => x.id);
  const cur = allDone ? PHASES.length : order.indexOf(state.phase || "plan");
  return PHASES.map((ph, i) => `<li class="${i < cur ? "done" : i === cur ? "now" : ""}">${ph.label}</li>`).join("");
}

function questionHeader(run) {
  const replay = run.mode === "replay" ? `<div class="replay-note">${icon("retry")}<div><b>Replay of a recorded run</b> from
    ${esc((run.replay_of && run.replay_of.recorded_at) || "earlier")}. Nothing was fetched or executed live; receipts, hashes
    and verdicts were recomputed from the recording.${run.replay_of && run.replay_of.note ? `<small>${esc(run.replay_of.note)}</small>` : ""}</div></div>` : "";
  return `${replay}<p class="q-label">Question</p><h2 class="question">${esc(run.question)}</h2>`;
}

function renderLoading(run) {
  $("#answer").innerHTML = `${run.id ? questionHeader(run) : `<p class="q-label">${esc(run.question)}</p>`}
    <ol class="phases" id="phases" aria-label="Progress">${phasesHtml()}</ol>
    <p class="progress-note" id="progress-count">${run.mode === "replay" ? "Replaying the recording…" : "Starting a fresh sandbox…"}</p>
    <div class="skeleton"><span></span><span></span><span class="short"></span></div>
    <div class="skeleton card"><span class="short"></span><span></span><span></span></div>`;
}

function renderError(run) {
  const rec = recordingFor(run.question || state.lastQuestion);
  const actions = [];
  if (run.question || state.lastQuestion) actions.push(`<button type="button" class="btn" id="err-retry">${icon("retry")}Try again live</button>`);
  if (rec) actions.push(`<button type="button" class="btn accent" id="err-replay">Replay the recorded run</button>`);
  $("#answer").innerHTML = `${run.id ? questionHeader(run) : ""}
    <div class="state-card error">
      <h2>That run didn't finish.</h2>
      <p>${esc(run.error || "Unknown error")}</p>
      ${actions.length ? `<div class="actions">${actions.join("")}</div>` : ""}
      ${rec ? `<small>A recording of this question exists, so the demo can continue offline. Replays are labelled as such.</small>` : ""}
    </div>`;
  const q = run.question || state.lastQuestion;
  const r1 = $("#err-retry"); if (r1) r1.onclick = () => startRun(q, "live");
  const r2 = $("#err-replay"); if (r2) r2.onclick = () => startRun(q, "replay");
}

function citeChips(text) {
  return esc(text).replace(/\[(R\d+)\]/g, (_, id) => `<button type="button" class="cite" data-r="${id}" data-cite="${id}">${id}</button>`);
}

function claimReceiptIds(cl) {
  return [...(cl.receipt_ids || []), ...(cl.evidence_id ? [cl.evidence_id] : [])].map(String);
}

const STATUS_ICON = { verified: "check", partial: "alert", unverified: "x" };
function statusHelp(s) {
  return {
    verified: "Quote found in the receipt and highlighted on the captured page (or in code output)",
    partial: "Quote roughly matches the receipt text, but was not confirmed on the screenshot",
    unverified: "Quote not found in the cited receipts",
  }[s] || "";
}

function renderRun(run) {
  if (!run.answer) { renderError(run); renderReceipts(run); return; }
  const a = run.answer;
  const c = a.counts || {};
  const recs = Object.fromEntries((run.receipts || []).map((r) => [r.id, r]));
  const claims = (a.claims || []).map((cl, i) => {
    const v = cl.verification || { status: "unverified", quote_score: 0 };
    const ids = claimReceiptIds(cl);
    const ev = recs[cl.evidence_id] || ids.map((r) => recs[r]).find((r) => r && r.has_image);
    const thumb = ev && ev.has_image
      ? `<button type="button" class="thumb-btn" data-r="${ev.id}" aria-label="Open receipt ${ev.id}"><img loading="lazy" alt="Screenshot receipt ${ev.id}" src="/api/runs/${run.id}/receipts/${ev.id}/image"></button>`
      : `<div class="thumb-empty">${icon("page")}<span>${ids.length ? "Text receipt" : "No receipt"}</span><span class="mono">${esc(ids.join(" · "))}</span></div>`;
    const vision = !v.vision ? ""
      : v.vision.text_only ? `<span title="${esc(v.vision.reason || "")}">text-only evidence: hidden on the page</span>`
      : `<span title="${esc(v.vision.reason || "")}">vision ${v.vision.supports === true ? "agrees" : v.vision.supports === false ? "disagrees" : "skipped"}</span>`;
    const chips = ids.map((r) => `<button type="button" class="cite" data-r="${esc(r)}">${esc(r)}</button>`).join("");
    return `<li class="claim in" style="animation-delay:${Math.min(i, 8) * 70}ms" data-claim="${i}" data-rids="${esc(ids.join(" "))}">
      <div>
        <div class="claim-top"><span class="claim-n">${pad2(i + 1)}</span>
          <span class="badge ${v.status}" title="${statusHelp(v.status)}">${icon(STATUS_ICON[v.status] || "dot")}${v.status}</span></div>
        <p class="claim-text">${esc(cl.text)}</p>
        ${cl.quote ? `<blockquote><mark>${esc(cl.quote)}</mark></blockquote>` : ""}
        ${cl.quote_snapped ? `<div class="snapped">The model's quote wasn't verbatim${cl.quote_original ? `: “${esc(short(cl.quote_original, 160))}”` : ""}. Showing the closest real sentence from the source, so this claim is capped at partial.</div>` : ""}
        <div class="claim-meta"><span class="cites">${chips}</span><span>quote match <span class="num">${(v.quote_score * 100).toFixed(0)}%</span></span>${vision}${cl.quote_repaired ? "<span>quote re-picked verbatim from the source</span>" : ""}</div>
      </div>
      ${thumb}
    </li>`;
  }).join("");
  $("#answer").innerHTML = `${questionHeader(run)}
    <ol class="phases" aria-label="Progress">${phasesHtml(true)}</ol>
    <p class="summary">${citeChips(a.summary)}</p>
    <div class="stats">
      <span class="stat verified"><b>${c.verified || 0}</b>verified</span>
      <span class="stat partial"><b>${c.partial || 0}</b>partial</span>
      <span class="stat unverified"><b>${c.unverified || 0}</b>unverified</span>
      <span class="stat-sep"></span>
      <span class="stat"><b>${(run.receipts || []).length}</b>receipts</span>
      <span class="stat"><b>${a.retries || 0}</b>self-heal retries</span>
      <span class="spacer"></span>
      <span class="model" title="Model that planned this run">${esc(short(run.llm || "", 60))}</span>
      <a class="btn sm" href="/api/runs/${run.id}/export" download>${icon("download")}Receipts .zip</a>
    </div>
    <ol class="claims" id="claims">${claims || '<li class="state-card"><p>The model returned no claims for this question.</p></li>'}</ol>`;
  renderReceipts(run, true);
}

// ------------------------------------------------------------ receipts gallery

function receiptKind(r) { return r.kind === "code" ? "code" : r.tool === "evidence" ? "evidence" : "sources"; }

function citedBy(run, rid) {
  const out = [];
  ((run.answer && run.answer.claims) || []).forEach((cl, i) => { if (claimReceiptIds(cl).includes(rid)) out.push(i); });
  return out;
}

function renderReceipts(run, animate = false) {
  const list = run.receipts || [];
  const before = new Set($$(".rcard").map((el) => el.dataset.rid));
  $("#receipts-card").hidden = !list.length;
  $("#receipts-count").textContent = list.length ? String(list.length) : "";
  const groups = { all: list.length, sources: 0, evidence: 0, code: 0 };
  list.forEach((r) => { groups[receiptKind(r)] += 1; });
  const labels = { all: "All", sources: "Sources", evidence: "Highlighted", code: "Code" };
  $("#receipt-tabs").innerHTML = Object.keys(labels).filter((k) => k === "all" || groups[k])
    .map((k) => `<button type="button" role="tab" class="${state.tab === k ? "on" : ""}" data-tab="${k}" aria-selected="${state.tab === k}">${labels[k]}<span>${groups[k]}</span></button>`).join("");
  const shown = list.filter((r) => state.tab === "all" || receiptKind(r) === state.tab);
  let n = 0;
  $("#receipts").innerHTML = shown.map((r) => {
    const img = r.has_image
      ? `<div class="shot"><img loading="lazy" alt="" src="/api/runs/${run.id}/receipts/${r.id}/image"></div>`
      : `<div class="shot text">${esc(short(r.kind === "code" ? (r.stdout || r.stderr || r.code) : r.text, 320))}</div>`;
    const kind = receiptKind(r);
    const cites = citedBy(run, r.id);
    const fresh = animate || !before.has(r.id);
    const delay = fresh ? `style="animation-delay:${Math.min(n++, 10) * 40}ms"` : "";
    return `<button type="button" class="rcard${fresh ? " in" : ""}" ${delay} data-r="${r.id}" data-rid="${r.id}">${img}
      <div class="rmeta">
        <div class="rline"><span class="rid">${r.id}</span><span class="kind ${kind}">${kind === "evidence" ? "highlighted" : kind === "code" ? "python" : r.tool}</span>${r.highlight_found ? `<span class="found" title="Quote found and highlighted on the page">${icon("check")}</span>` : ""}</div>
        <span class="rhost">${esc(r.kind === "code" ? `exit ${r.exit_code}` : host(r.url))}</span>
        <span class="rtitle">${esc(short(r.title || (r.code || "").split("\n")[0] || "", 70))}</span>
        <span class="rhash">${esc(r.sha256.slice(0, 16))}…${cites.length ? ` · claim ${cites.map((i) => i + 1).join(", ")}` : ""}</span>
      </div></button>`;
  }).join("") || '<p class="sec-help">Nothing in this group.</p>';
}

// Claim <-> receipt linking: hovering one lights up the other and dims the rest.
function clearLinks() {
  $$(".linked").forEach((el) => el.classList.remove("linked"));
  $$(".focus").forEach((el) => el.classList.remove("focus"));
}
function link(rids, claimIdx) {
  clearLinks();
  rids.forEach((id) => $$(`.rcard[data-rid="${CSS.escape(id)}"], .cite[data-r="${CSS.escape(id)}"]`).forEach((el) => el.classList.add("linked")));
  claimIdx.forEach((i) => $$(`.claim[data-claim="${i}"]`).forEach((el) => el.classList.add("linked")));
  if (rids.length) $("#receipts").classList.add("focus");
  if (claimIdx.length) { const c = $("#claims"); if (c) c.classList.add("focus"); }
}
document.addEventListener("mouseover", (e) => {
  const claim = e.target.closest(".claim");
  if (claim) { link(claim.dataset.rids.split(" ").filter(Boolean), [+claim.dataset.claim]); return; }
  const card = e.target.closest(".rcard");
  if (card && state.run) { link([card.dataset.rid], citedBy(state.run, card.dataset.rid)); return; }
  const chip = e.target.closest(".summary .cite");
  if (chip && state.run) link([chip.dataset.r], citedBy(state.run, chip.dataset.r));
});
document.addEventListener("mouseout", (e) => {
  const from = e.target.closest(".claim, .rcard, .summary .cite");
  if (from && !from.contains(e.relatedTarget)) clearLinks();
});

// ------------------------------------------------------------ sandbox panel

function renderSandbox(sb, phase) {
  if (!sb || !sb.backend) return;
  const life = $("#sandbox-life");
  const replay = sb.backend === "replay";
  life.className = "life " + (phase === "destroyed" ? "" : sb.isolated ? "on" : replay ? "" : "mock");
  life.textContent = replay ? (phase === "destroyed" ? "replay done" : "replaying")
    : phase === "destroyed" ? "destroyed" : sb.isolated ? "running" : "mock";
  const lim = sb.limits || {};
  const steps = ["created", "running", "destroyed"];
  const at = phase === "destroyed" ? 2 : 1;
  $("#sandbox-info").innerHTML = `
    <div class="sb-lifecycle">${steps.map((s, i) => `<span class="${i <= at ? "on" : ""}">${s}</span>`).join("<i></i>")}</div>
    <dl class="kv">
      <dt>backend</dt><dd>${esc(sb.backend)}${sb.runtime ? " / " + esc(sb.runtime) : ""}</dd>
      <dt>container</dt><dd>${esc(sb.container_id)}</dd>
      <dt>browser</dt><dd>${esc(lim.browser ? `${lim.browser.memory} · ${lim.browser.cpus} cpu · ${lim.browser.pids} pids` : "n/a")}</dd>
      <dt>python</dt><dd>${esc(lim.code ? `${lim.code.memory} · ${lim.code.timeout_s}s · no network` : "n/a")}</dd>
      <dt>secrets</dt><dd>none inside</dd>
    </dl>
    ${sb.notes ? `<p class="sb-note">${esc(sb.notes)}</p>` : ""}`;
}

// ------------------------------------------------------------ agent timeline

const TOOL_LABEL = { search: "Search", fetch: "Read page", screenshot: "Open in browser", python: "Run Python", evidence: "Highlight" };
const STEP_ICON = { search: "search", fetch: "page", screenshot: "camera", python: "code", final: "pen" };

function describe(ev) {
  let kind = "", ic = "dot", title = ev.type.replace(/_/g, " "), body = "", extra = "";
  switch (ev.type) {
    case "run_started":
      ic = "dot"; title = ev.mode === "replay" ? "Replay started" : "Run started";
      body = ev.mode === "replay" ? `Replaying a recording from ${esc(ev.replay_of && ev.replay_of.recorded_at)}. Nothing runs live.` : esc(ev.llm);
      if (ev.mode === "replay") kind = "guard";
      break;
    case "sandbox_created":
      ic = "box"; title = "Sandbox created";
      body = `<span class="host">${esc(ev.backend)} ${esc(ev.container_id)}</span> · ${ev.isolated ? "isolated" : ev.backend === "replay" ? "replay" : "not isolated (mock)"}`; break;
    case "step": {
      kind = "step"; ic = STEP_ICON[ev.action] || "dot";
      const a = ev.args || {};
      title = ev.action === "search" ? `Search “${esc(short(a.query || "", 60))}”`
        : ev.action === "fetch" || ev.action === "screenshot" ? `${TOOL_LABEL[ev.action]} · ${esc(host(a.url || ""))}`
        : ev.action === "python" ? "Run Python" : ev.action === "final" ? "Draft the answer" : esc(ev.action);
      body = esc(ev.thought);
      if (a.code) extra = `<details><summary>code</summary><pre>${esc(a.code)}</pre></details>`;
      return { kind, ic, title, body, extra, raw: true };
    }
    case "tool_result":
      kind = ev.ok ? "ok" : "err"; ic = ev.ok ? "check" : "x";
      title = `${TOOL_LABEL[ev.tool] || ev.tool} ${ev.ok ? "done" : "failed"}${ev.receipt_id ? ` → ${ev.receipt_id}` : ""}`;
      body = esc(ev.ok ? (ev.summary || "") : ev.error) + (ev.url ? ` <span class="host">${esc(host(ev.url))}</span>` : "");
      if (ev.stderr && ev.stderr.trim()) extra += `<details><summary>stderr</summary><pre>${esc(ev.stderr)}</pre></details>`;
      if (ev.stdout && ev.stdout.trim()) extra += `<details><summary>stdout</summary><pre>${esc(ev.stdout)}</pre></details>`;
      if (ev.blocked_requests && ev.blocked_requests.length)
        extra += `<details><summary>${ev.blocked_requests.length} blocked request${ev.blocked_requests.length > 1 ? "s" : ""}</summary><pre>${esc(ev.blocked_requests.join("\n"))}</pre></details>`;
      if (ev.injection_suspected) kind = "guard";
      break;
    case "retry": kind = "guard"; ic = "retry"; title = "Self-heal retry"; body = esc(ev.message); break;
    case "fallback": kind = "guard"; ic = "alert"; title = "Model fallback"; body = esc(ev.message); break;
    case "snap": kind = "guard"; ic = "alert"; title = "Quote snapped to source"; body = esc(ev.message); break;
    case "requote": kind = "ok"; ic = "retry"; title = "Quote re-checked"; body = esc(ev.message); break;
    case "containment": kind = "ok"; ic = "shield"; title = "Contained by the sandbox"; body = esc(ev.message); break;
    case "guard": kind = "guard"; ic = "alert"; title = "Prompt injection flagged"; body = esc(ev.message); break;
    case "evidence": ic = "camera"; title = `Highlight claim ${ev.claim + 1}`; body = esc(ev.message); break;
    case "verification":
      kind = ev.status === "verified" ? "ok" : ev.status === "partial" ? "guard" : "err";
      ic = STATUS_ICON[ev.status] || "dot"; title = `Claim ${ev.claim + 1} ${ev.status}`;
      body = `quote match ${(ev.score * 100).toFixed(0)}%` + (ev.vision ? ` · vision: ${esc(ev.vision.reason || "")}` : ""); break;
    case "drafted": ic = "pen"; title = "Answer drafted"; body = `${ev.n_claims} claims to verify`; break;
    case "final": kind = "final"; ic = "flag"; title = "Answer ready";
      body = `${ev.counts.verified} verified · ${ev.counts.partial} partial · ${ev.counts.unverified} unverified`; break;
    case "sandbox_destroyed": ic = "trash"; title = "Sandbox destroyed"; body = esc(ev.message); break;
    case "error": kind = "err"; ic = "x"; title = "Error"; body = esc(ev.message); break;
    case "run_finished": ic = "dot"; title = "Run finished"; body = esc(ev.status); break;
    default: body = esc(ev.message || "");
  }
  return { kind, ic, title: esc(title), body, extra };
}

function addEvent(ev, live, index = 0) {
  const log = $("#log");
  const d = describe(ev);
  const li = document.createElement("li");
  li.className = `tl ${d.kind}`;
  if (live || index < 40) {
    li.classList.add("enter");
    if (!live) li.style.animationDelay = `${index * 22}ms`;
  }
  if (ev.type === "tool_result" && ev.receipt_id) { li.dataset.r = ev.receipt_id; li.classList.add("tl-receipt"); }
  li.innerHTML = `<span class="node">${icon(d.ic)}</span>
    <div><div class="tl-h"><span class="tl-title">${d.title}</span><span class="tl-t">+${ev.t}s</span></div>
    ${d.body ? `<div class="tl-b">${d.body}</div>` : ""}${d.extra}</div>`;
  if (live) {
    $$(".tl.current", log).forEach((el) => el.classList.remove("current"));
    if (ev.type !== "run_finished") li.classList.add("current");
  }
  log.appendChild(li);
  if (live) log.scrollTop = log.scrollHeight;
}

// ------------------------------------------------------------ receipt sheet + hash verification

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Recompute the receipt hash in the browser: sha256(utf8(url\ntext\ncode\nstdout) + image bytes).
async function verifyInBrowser(r) {
  const head = new TextEncoder().encode(`${r.url}\n${r.text}\n${r.code}\n${r.stdout}`);
  let img = new Uint8Array(0);
  if (r.has_image) {
    const res = await fetch(`/api/runs/${state.runId}/receipts/${r.id}/image`, { cache: "no-store" });
    img = new Uint8Array(await res.arrayBuffer());
  }
  const all = new Uint8Array(head.length + img.length);
  all.set(head, 0); all.set(img, head.length);
  return { recomputed: await sha256Hex(all), image_bytes: img.length, where: "in your browser" };
}

// Rare, explicit action: let the digest resolve character by character.
function revealHash(el, hex) {
  if (reduceMotion) { el.textContent = hex; return; }
  const chars = "0123456789abcdef";
  const start = performance.now(), dur = 650;
  const tick = (now) => {
    const k = Math.min(1, (now - start) / dur);
    const fixed = Math.floor(hex.length * k);
    el.textContent = hex.slice(0, fixed) + Array.from({ length: hex.length - fixed }, () => chars[(Math.random() * 16) | 0]).join("");
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

async function verifyReceipt(r, out, btn) {
  out.className = "verify-out";
  out.innerHTML = `<span class="spinner" aria-hidden="true"></span> Recomputing SHA-256…`;
  btn.disabled = true;
  try {
    let res;
    if (window.crypto && crypto.subtle) res = await verifyInBrowser(r);
    else {
      const s = await api(`/api/runs/${state.runId}/receipts/${r.id}/verify`);
      res = { recomputed: s.recomputed, image_bytes: s.image_bytes, where: "on the server (this page is not on HTTPS, so the browser can't hash)" };
    }
    const ok = res.recomputed === r.sha256;
    out.className = "verify-out " + (ok ? "good" : "bad");
    out.innerHTML = `${ok ? "Hash matches the one recorded at capture time." : "Hash mismatch: this receipt was altered."}
      Recomputed ${esc(res.where)} from <span class="num">${r.text.length.toLocaleString()}</span> characters of captured text${res.image_bytes ? ` and <span class="num">${res.image_bytes.toLocaleString()}</span> screenshot bytes` : ""}.
      <span class="hash"></span>`;
    revealHash($(".hash", out), res.recomputed);
  } catch (e) {
    out.className = "verify-out bad";
    out.textContent = `Could not verify: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function openSheet(dlg, fromEl) {
  // Morph the clicked thumbnail into the sheet where the browser supports view transitions.
  if (fromEl && document.startViewTransition && !reduceMotion && !dlg.open) {
    fromEl.style.viewTransitionName = "receipt-shot";
    const t = document.startViewTransition(() => { fromEl.style.viewTransitionName = ""; dlg.showModal(); });
    t.finished.finally(() => { fromEl.style.viewTransitionName = ""; });
  } else if (!dlg.open) dlg.showModal();
}

// Captured text with the quoted sentence marked. Long pages are cut to a
// window around the quote so the evidence is visible without scrolling.
function findQuote(text, quote) {
  const words = String(quote || "").match(/[\p{L}\p{N}]+/gu) || [];
  const re = (ws) => new RegExp(ws.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[^\\p{L}\\p{N}]+"), "iu");
  for (const ws of [words, words.slice(0, 10)]) {
    if (ws.length < 3) continue;
    const m = re(ws).exec(text);
    if (m) return [m.index, m.index + m[0].length];
  }
  return null;
}

function capturedText(text, quote, max = 6000) {
  text = String(text || "");
  const hit = quote ? findQuote(text, quote) : null;
  if (!hit) return esc(short(text, max));
  const start = Math.max(0, hit[0] - Math.floor(max / 3));
  const end = Math.min(text.length, start + max);
  return (start > 0 ? "…" : "") + esc(text.slice(start, hit[0])) + `<mark id="quote-mark">${esc(text.slice(hit[0], hit[1]))}</mark>`
    + esc(text.slice(hit[1], end)) + (end < text.length ? "…" : "");
}

async function showReceipt(rid, fromEl) {
  if (!state.runId) return;
  const body = $("#modal-body");
  $("#modal-title").textContent = `${rid} · loading…`;
  body.innerHTML = `<div class="skeleton"><span></span><span></span><span class="short"></span></div>`;
  let r;
  try { r = await api(`/api/runs/${state.runId}/receipts/${rid}`); } catch (e) {
    body.innerHTML = `<p class="error-text">Couldn't load receipt ${esc(rid)}: ${esc(e.message)}</p>`;
    openSheet($("#modal")); return;
  }
  const img = r.has_image ? `<img class="shot-full" alt="Screenshot receipt ${r.id}" src="/api/runs/${state.runId}/receipts/${r.id}/image">` : "";
  const main = r.kind === "code"
    ? `${img ? `<h4>Output</h4>${img}` : ""}<h4>Code · fresh container, no network</h4><pre>${esc(r.code)}</pre>
       <h4>stdout</h4><pre>${esc(r.stdout || "(empty)")}</pre>${r.stderr ? `<h4>stderr</h4><pre>${esc(r.stderr)}</pre>` : ""}`
    : `${img || '<p class="sec-help">Text-only receipt, fetched without a browser. The highlighted screenshot, if any, is a separate receipt.</p>'}
       <h4>Captured text</h4><pre class="captured">${capturedText(r.text, r.highlight)}</pre>`;
  const cites = state.run ? citedBy(state.run, r.id) : [];
  $("#modal-title").textContent = `${r.id} · ${r.kind === "code" ? "Python output" : r.title || host(r.url)}`;
  body.innerHTML = `<div class="receipt-grid"><div>${main}</div>
    <aside class="receipt-side">
      <div class="verify-box">
        <button type="button" class="btn accent" id="verify-btn">${icon("hash")}Verify SHA-256</button>
        <div class="verify-out" id="verify-out">Recompute this receipt's hash from its captured content and compare it with the hash taken at capture time.</div>
      </div>
      <dl class="kv">
        ${r.url ? `<dt>source</dt><dd><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(short(r.url, 80))}</a></dd>` : ""}
        <dt>tool</dt><dd>${esc(r.tool)}</dd>
        <dt>captured</dt><dd>${esc(r.captured_at)}</dd>
        <dt>sandbox</dt><dd>${esc(r.sandbox)}</dd>
        <dt>sha256</dt><dd>${esc(r.sha256)}</dd>
        ${cites.length ? `<dt>supports</dt><dd>claim ${cites.map((i) => i + 1).join(", ")}</dd>` : ""}
        ${r.highlight ? `<dt>highlight</dt><dd>${r.highlight_hidden ? `in the page but hidden from view (${esc(r.highlight_hidden)})` : r.highlight_found ? "found on page" : "not found"}</dd><dt>quote</dt><dd>${esc(short(r.highlight, 300))}</dd>` : ""}
        ${r.exit_code !== null && r.exit_code !== undefined ? `<dt>exit code</dt><dd>${r.exit_code}</dd>` : ""}
        ${r.blocked_requests && r.blocked_requests.length ? `<dt>blocked</dt><dd>${esc(r.blocked_requests.join(", "))}</dd>` : ""}
      </dl>
    </aside></div>`;
  const shot = $(".shot-full", body);
  if (shot) shot.style.viewTransitionName = "receipt-shot";
  openSheet($("#modal"), fromEl);
  if (shot) setTimeout(() => { shot.style.viewTransitionName = ""; }, 400);
  const mark = $("#quote-mark", body);
  if (mark) {
    // The sheet may open inside a view transition, so wait until it is laid out.
    const pre = mark.closest("pre");
    const scroll = () => { pre.scrollTop = Math.max(0, mark.offsetTop - pre.offsetTop - 48); };
    [60, 320].forEach((ms) => setTimeout(scroll, ms));
  }
  $("#verify-btn").onclick = () => verifyReceipt(r, $("#verify-out"), $("#verify-btn"));
}

// ------------------------------------------------------------ containment test

async function runContainment() {
  if (accessRequired()) {
    if (!accessCode()) { showAccess("missing"); return; }
    try { await api("/api/access", { method: "POST" }); acceptAccess(); } catch (e) {
      if (e.status === 401) { rejectAccess(); return; }
    }
  }
  const dlg = $("#contain");
  $("#contain-list").innerHTML = '<li class="pending"><span class="spinner" aria-hidden="true"></span> Starting a fresh sandbox…</li>';
  $("#contain-done").innerHTML = "";
  $("#contain-sandbox").innerHTML = "";
  dlg.showModal();
  const code = accessCode();
  const es = new EventSource(`/api/containment-test${code ? `?code=${encodeURIComponent(code)}` : ""}`);
  const TOTAL = 8;
  let first = true, n = 0, held = 0;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "sandbox") {
      $("#contain-sandbox").innerHTML = `Sandbox ${esc(ev.backend)} ${esc(ev.container_id)} · ${ev.isolated ? "isolated" : "mock, not isolated"}
        <div class="contain-progress"><i id="contain-bar"></i></div>`;
      return;
    }
    if (first) { $("#contain-list").innerHTML = ""; first = false; }
    if (ev.type === "attack") {
      n += 1; if (ev.contained) held += 1;
      const bar = $("#contain-bar"); if (bar) bar.style.width = `${Math.min(100, (n / TOTAL) * 100)}%`;
      const li = document.createElement("li");
      li.innerHTML = `<div class="a-h"><b>${esc(ev.title)}</b>
        <span class="badge ${ev.contained ? "verified" : "unverified"}">${icon(ev.contained ? "shield" : "x")}${ev.contained ? "contained" : "not contained"}</span></div>
        <div class="a-n">${esc(ev.note)} · <span class="num">${ev.duration_s}s</span></div>
        <details><summary>attack and sandbox output</summary><pre>${esc(ev.code)}</pre>${ev.stdout ? `<pre>${esc(ev.stdout)}</pre>` : ""}${ev.stderr ? `<pre>${esc(ev.stderr)}</pre>` : ""}</details>`;
      $("#contain-list").appendChild(li);
      li.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
    } else if (ev.type === "done") {
      const bar = $("#contain-bar"); if (bar) bar.style.width = "100%";
      $("#contain-done").innerHTML = `<div class="done-banner ${held === n ? "" : "bad"}">${icon(held === n ? "shield" : "alert")}
        <b>${held}/${n}</b><span>attacks contained. ${esc(ev.message)} Host check: ${ev.host_ok ? "OK" : "FAILED"}.</span></div>`;
      $("#contain-done").scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
      es.close();
    } else if (ev.type === "error") {
      $("#contain-done").innerHTML = `<p class="error-text">${esc(ev.message)}</p>`;
      es.close();
    }
  };
  es.onerror = () => {
    if (first) $("#contain-list").innerHTML = '<li class="error-text">Lost the connection to the server.</li>';
    es.close();
  };
}

// ------------------------------------------------------------ wiring

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) { state.tab = tab.dataset.tab; if (state.run) renderReceipts(state.run, true); return; }
  const t = e.target.closest("[data-r]");
  if (t) { e.preventDefault(); showReceipt(t.dataset.r, t.querySelector("img")); return; }
  const c = e.target.closest("[data-close]");
  if (c) c.closest("dialog").close();
});
// A click on the backdrop closes dialogs.
$$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
$("#history").addEventListener("click", (e) => { if (e.target.closest("a")) $("#history-sheet").close(); });
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) startRun(q); });
$("#q").addEventListener("input", () => { autosize(); syncReplayToggle(); });
$("#access").addEventListener("input", () => {
  if ($("#access-row").classList.contains("invalid")) showAccess();
});
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#ask").requestSubmit(); });
$("#btn-contain").addEventListener("click", runContainment);
$("#btn-history").addEventListener("click", () => { loadHistory(); $("#history-sheet").showModal(); });
$(".brand").addEventListener("click", (e) => { e.preventDefault(); history.pushState(null, "", location.pathname); route(); });
window.addEventListener("hashchange", route);
window.addEventListener("popstate", route);

function route() {
  const m = location.hash.match(/run=([\w-]+)/);
  if (m && m[1] !== state.runId) { openRun(m[1]); return; }
  if (!m) {
    if (state.source) { state.source.close(); state.source = null; setBusy(false); }
    state.runId = null; state.run = null;
    setMode("home");
  }
}

async function boot() {
  $("#access").value = store.get("rwr-code");
  syncThemeIcon();
  setMode("home");
  const ok = await loadHealth();
  loadHistory();
  route();
  if (!ok) {
    // Keep retrying quietly until the server is back.
    const timer = setInterval(async () => { if (await loadHealth()) { clearInterval(timer); loadHistory(); } }, 4000);
  }
}
boot();
