// Research with Receipts: single-page UI (vanilla JS, no build step).
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = { health: null, runId: null, source: null, run: null, tab: "all", phase: null, lastQuestion: "" };

const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function short(s, n = 120) { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function host(u) { try { return new URL(u).host; } catch { return u; } }

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const code = $("#access").value || store.get("rwr-code");
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

// ------------------------------------------------------------ health, examples, recordings

const EXAMPLES = (base) => [
  { k: "Browser + receipts", q: "How does gVisor isolate containers, and what performance cost does it have compared to runc?" },
  { k: "Browser + Python", q: "What are the three most populous countries today, and what share of the world population do they hold together?" },
  { k: "Containment (hostile page)", q: `Summarize the product page at ${base}/demo/hostile and list its price and colours.` },
];

async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.health = h;
    $("#offline").hidden = true;
    const ps = $("#pill-sandbox");
    ps.textContent = h.sandbox_isolated ? `sandbox: docker (${h.sandbox_runtime})` : "sandbox: mock (dev only)";
    ps.className = "pill " + (h.sandbox_isolated ? "ok" : "warn");
    const pl = $("#pill-llm");
    const st = h.llm_status || {};
    if (st.provider === "vultr") {
      pl.textContent = `llm: vultr ${h.llm_model}` + (st.ok ? "" : " (unreachable)");
      pl.className = "pill " + (st.ok ? "ok" : "bad");
      pl.title = st.detail || "";
    } else {
      pl.textContent = "llm: mock policy";
      pl.className = "pill warn";
      pl.title = st.detail || "";
    }
    $("#code-row").hidden = !h.access_code_required;
    renderExamples(h.public_base_url);
    syncReplayToggle();
    return true;
  } catch {
    $("#offline").hidden = false;
    $("#pill-sandbox").textContent = "backend offline";
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

function syncReplayToggle() {
  const rec = recordingFor($("#q").value);
  $("#replay-row").hidden = !rec;
  if (!rec) $("#replay").checked = false;
}

function renderExamples(base) {
  const ex = EXAMPLES(base);
  $("#examples").innerHTML = `<span class="label">Try one</span>` + ex.map((e, i) =>
    `<button type="button" class="example" data-i="${i}"><span class="k">${esc(e.k)}${recordingFor(e.q) ? ' <span class="rec">recorded</span>' : ""}</span>${esc(e.q)}</button>`).join("");
  $$(".example").forEach((b) =>
    b.addEventListener("click", () => { $("#q").value = ex[+b.dataset.i].q; syncReplayToggle(); $("#q").focus(); }));
}

// ------------------------------------------------------------ history

async function loadHistory() {
  try {
    const runs = await api("/api/runs");
    const ul = $("#history");
    if (!runs.length) { ul.innerHTML = '<li class="muted small">No runs yet. Your research history will appear here.</li>'; return; }
    ul.innerHTML = runs.slice(0, 30).map((r) =>
      `<li><a href="#run=${r.id}" class="${r.id === state.runId ? "active" : ""}">${esc(short(r.question, 70))}
        <span class="st"><span class="dot ${esc(r.status)}"></span>${esc(r.status)}${r.mode === "replay" ? " · replay" : ""} · ${r.n_receipts} receipts</span></a></li>`).join("");
  } catch { /* history is optional */ }
}

// ------------------------------------------------------------ run lifecycle

function setBusy(busy, label = "Research it") {
  const btn = $("#go");
  btn.disabled = busy;
  btn.innerHTML = busy ? `<span class="spinner" aria-hidden="true"></span> ${esc(label)}` : "Research it";
}

async function startRun(question, mode) {
  state.lastQuestion = question;
  setBusy(true, mode === "replay" ? "Starting replay…" : "Starting sandbox…");
  try {
    const code = $("#access").value; if (code) store.set("rwr-code", code);
    mode = mode || ($("#replay").checked ? "replay" : "auto");
    const { id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ question, mode }) });
    location.hash = `run=${id}`;
  } catch (e) {
    setBusy(false);
    const msg = e.status === 401 ? "This deployment needs an access code. Enter it above and try again."
      : e.status === 422 ? "Please ask a question of at least 5 characters." : e.message;
    renderError({ question, error: `Could not start the run: ${msg}` });
  }
}

async function openRun(id) {
  if (state.source) { state.source.close(); state.source = null; }
  state.runId = id;
  state.phase = null;
  state.tab = "all";
  $("#log").innerHTML = "";
  renderLoading({ question: "Loading run…" });
  $("#receipts-card").hidden = true;
  let run;
  try { run = await api(`/api/runs/${id}`); } catch {
    state.lastQuestion = "";
    renderError({ question: "", error: "This run doesn't exist anymore. It may have been created on another server." });
    return;
  }
  state.run = run;
  state.lastQuestion = run.question;
  renderSandbox(run.sandbox, run.status === "done" || run.status === "failed" ? "destroyed" : "running");
  loadHistory();
  if (run.status === "done" || run.status === "failed") {
    run.events.forEach((ev) => addEvent(ev, false));
    renderRun(run);
    return;
  }
  renderLoading(run);
  $("#live").hidden = false;
  setBusy(true, run.mode === "replay" ? "Replaying…" : "Researching…");
  const es = new EventSource(`/api/runs/${id}/events`);
  state.source = es;
  es.onmessage = async (m) => {
    const ev = JSON.parse(m.data);
    addEvent(ev, true);
    trackPhase(ev);
    if (ev.type === "run_started" && ev.mode === "replay") { state.run.mode = "replay"; state.run.replay_of = ev.replay_of; }
    if (ev.type === "sandbox_created") { state.run.sandbox = ev; renderSandbox(ev, "running"); }
    if (ev.type === "sandbox_destroyed") renderSandbox(state.run.sandbox, "destroyed");
    if (ev.type === "tool_result" && ev.receipt_id) refreshReceipts();
    if (ev.type === "run_finished") {
      es.close(); state.source = null; $("#live").hidden = true;
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
      const n = $("#progress-count"); if (n) n.textContent = `${r.receipts.length} receipts captured so far`;
    } catch { /* transient */ }
  }, 300);
}

// ------------------------------------------------------------ answer card states

const PHASES = [
  { id: "plan", label: "Plan" }, { id: "browse", label: "Browse" }, { id: "compute", label: "Compute" },
  { id: "evidence", label: "Evidence" }, { id: "verify", label: "Verify" },
];

function trackPhase(ev) {
  let p = null;
  if (ev.type === "run_started" || ev.type === "sandbox_created") p = "plan";
  if (ev.type === "step") p = ev.action === "python" ? "compute" : ev.action === "final" ? "evidence" : "browse";
  if (ev.type === "evidence" || ev.type === "drafted") p = "evidence";
  if (ev.type === "verification") p = "verify";
  if (!p) return;
  const order = PHASES.map((x) => x.id);
  // Compute can follow browse and vice versa; never move backwards past evidence.
  if (state.phase && order.indexOf(state.phase) >= 3 && order.indexOf(p) < 3) return;
  state.phase = p;
  const el = $("#stepper");
  if (el) el.innerHTML = stepperHtml();
}

function stepperHtml() {
  const order = PHASES.map((x) => x.id);
  const cur = order.indexOf(state.phase || "plan");
  return PHASES.map((ph, i) => `<li class="${i < cur ? "done" : i === cur ? "now" : ""}">${ph.label}</li>`).join("");
}

function questionHeader(run) {
  const replay = run.mode === "replay" ? `<div class="replay-banner"><b>Replay.</b> This run replays a recording from
    ${esc((run.replay_of && run.replay_of.recorded_at) || "earlier")}. Nothing was fetched or executed live; receipts,
    hashes and verification were recomputed from the recording.${run.replay_of && run.replay_of.note ? `<br><span class="muted">${esc(run.replay_of.note)}</span>` : ""}</div>` : "";
  return `${replay}<div class="q">Question</div><h2 class="question">${esc(run.question)}</h2>`;
}

function renderEmpty() {
  $("#answer").innerHTML = `<div class="empty">
    <h2>Ask something worth checking.</h2>
    <p>The agent plans its research, drives a real browser and runs Python, all inside disposable containers on Vultr.
       You get an answer where every claim links to a receipt you can inspect and verify.</p>
    <ol class="how">
      <li><b>Plan</b><span>A model on Vultr Serverless Inference decides the next step.</span></li>
      <li><b>Execute</b><span>Search, browse and compute run in an isolated sandbox with no secrets.</span></li>
      <li><b>Receipt</b><span>Each source is captured: URL, exact quote, highlighted screenshot, SHA-256.</span></li>
      <li><b>Verify</b><span>Every claim is checked against its receipts and labelled.</span></li>
    </ol>
  </div>`;
}

function renderLoading(run) {
  $("#answer").innerHTML = `${run.id ? questionHeader(run) : `<div class="q">${esc(run.question)}</div>`}
    <ol class="stepper" id="stepper">${stepperHtml()}</ol>
    <p class="muted small" id="progress-count">${run.mode === "replay" ? "Replaying the recording…" : "Starting a fresh sandbox…"}</p>
    <div class="skeleton"><span></span><span></span><span class="short"></span></div>
    <div class="skeleton claim-sk"><span></span><span class="short"></span></div>`;
}

function renderError(run) {
  const rec = recordingFor(run.question || state.lastQuestion);
  const actions = [];
  if (run.question || state.lastQuestion) actions.push(`<button type="button" class="btn" id="err-retry">Try again live</button>`);
  if (rec) actions.push(`<button type="button" class="btn primary-inline" id="err-replay">Replay the recorded run</button>`);
  $("#answer").innerHTML = `${run.id ? questionHeader(run) : ""}
    <div class="error-state">
      <h2>That run didn't finish.</h2>
      <p>${esc(run.error || "Unknown error")}</p>
      ${actions.length ? `<div class="actions">${actions.join("")}</div>` : ""}
      ${rec ? `<p class="muted small">A recording of this question exists, so the demo can continue offline. Replays are labelled as such.</p>` : ""}
    </div>`;
  const q = run.question || state.lastQuestion;
  const r1 = $("#err-retry"); if (r1) r1.onclick = () => startRun(q, "live");
  const r2 = $("#err-replay"); if (r2) r2.onclick = () => startRun(q, "replay");
}

function citeChips(text) {
  return esc(text).replace(/\[(R\d+)\]/g, (_, id) => `<button type="button" class="cite" data-r="${id}" data-rids="${id}">${id}</button>`);
}

function claimReceiptIds(cl) {
  return [...(cl.receipt_ids || []), ...(cl.evidence_id ? [cl.evidence_id] : [])].map(String);
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
      ? `<button type="button" class="thumb-btn" data-r="${ev.id}" aria-label="Open receipt ${ev.id}"><img class="thumb" loading="lazy" alt="" src="/api/runs/${run.id}/receipts/${ev.id}/image"></button>`
      : `<div class="thumb placeholder">${esc(ids.join(" ") || "no receipt")}</div>`;
    const vision = v.vision ? ` · vision ${v.vision.supports === true ? "agrees" : v.vision.supports === false ? "disagrees" : "skipped"}` : "";
    const chips = ids.map((r) => `<button type="button" class="cite" data-r="${esc(r)}">${esc(r)}</button>`).join("");
    return `<li class="claim" data-claim="${i}" data-rids="${esc(ids.join(" "))}">
      <div>
        <span class="badge ${v.status}" title="${statusHelp(v.status)}">${v.status}</span>
        <div class="text">${i + 1}. ${esc(cl.text)} ${chips}</div>
        ${cl.quote ? `<blockquote><mark>${esc(cl.quote)}</mark></blockquote>` : ""}
        ${cl.quote_snapped ? `<div class="snapped">The model's quote wasn't verbatim${cl.quote_original ? `: “${esc(short(cl.quote_original, 160))}”` : ""}. Showing the closest real sentence from the source instead, so this claim can be at most partial.</div>` : ""}
        <div class="why">quote match ${(v.quote_score * 100).toFixed(0)}%${vision}${cl.evidence_id ? " · highlighted screenshot " + esc(cl.evidence_id) : ""}</div>
      </div>
      ${thumb}
    </li>`;
  }).join("");
  $("#answer").innerHTML = `${questionHeader(run)}
    <p class="summary">${citeChips(a.summary)}</p>
    <div class="counts">
      <span class="badge verified">${c.verified || 0} verified</span>
      <span class="badge partial">${c.partial || 0} partial</span>
      <span class="badge unverified">${c.unverified || 0} unverified</span>
      <span class="badge neutral">${(run.receipts || []).length} receipts</span>
      <span class="badge neutral">${a.retries || 0} self-heal retries</span>
      <span class="badge neutral" title="Model that planned this run">${esc(run.llm || "")}</span>
      <a class="btn small-btn" href="/api/runs/${run.id}/export" download>Download receipts (.zip)</a>
    </div>
    <ol class="claims">${claims || '<li class="muted">The model returned no claims for this question.</li>'}</ol>`;
  renderReceipts(run);
}

function statusHelp(s) {
  return {
    verified: "Quote found in the receipt and highlighted on the captured page (or in code output)",
    partial: "Quote roughly matches the receipt text, but was not confirmed on the screenshot",
    unverified: "Quote not found in the cited receipts",
  }[s] || "";
}

// ------------------------------------------------------------ receipts panel

function receiptKind(r) { return r.kind === "code" ? "code" : r.tool === "evidence" ? "evidence" : "sources"; }

function citedBy(run, rid) {
  const out = [];
  ((run.answer && run.answer.claims) || []).forEach((cl, i) => { if (claimReceiptIds(cl).includes(rid)) out.push(i); });
  return out;
}

function renderReceipts(run) {
  const list = run.receipts || [];
  $("#receipts-card").hidden = !list.length;
  $("#receipts-count").textContent = list.length ? `(${list.length})` : "";
  const groups = { all: list.length, sources: 0, evidence: 0, code: 0 };
  list.forEach((r) => { groups[receiptKind(r)] += 1; });
  const labels = { all: "All", sources: "Sources", evidence: "Highlighted", code: "Code" };
  $("#receipt-tabs").innerHTML = Object.keys(labels).filter((k) => k === "all" || groups[k])
    .map((k) => `<button type="button" role="tab" class="tab ${state.tab === k ? "on" : ""}" data-tab="${k}" aria-selected="${state.tab === k}">${labels[k]} <span>${groups[k]}</span></button>`).join("");
  const shown = list.filter((r) => state.tab === "all" || receiptKind(r) === state.tab);
  $("#receipts").innerHTML = shown.map((r) => {
    const img = r.has_image
      ? `<img loading="lazy" alt="" src="/api/runs/${run.id}/receipts/${r.id}/image">`
      : `<div class="noimg">${esc(short(r.kind === "code" ? (r.stdout || r.stderr || r.code) : r.text, 180))}</div>`;
    const kind = receiptKind(r);
    const t = r.kind === "code" ? `python · exit ${r.exit_code}` : host(r.url);
    const cites = citedBy(run, r.id);
    return `<button type="button" class="rcard" data-r="${r.id}" data-rid="${r.id}">${img}
      <div class="meta">
        <div class="line1"><b>${r.id}</b><span class="kind ${kind}">${kind === "evidence" ? "highlighted" : kind === "code" ? "code" : r.tool}</span>${r.highlight_found ? '<span class="ok-mark" title="Quote found and highlighted on the page">●</span>' : ""}</div>
        <span class="t">${esc(t)}</span>
        <span class="t muted">${esc(short(r.title || (r.code || "").split("\n")[0] || "", 60))}</span>
        <span class="t mono">${esc(r.sha256.slice(0, 12))}…${cites.length ? ` · claim ${cites.map((i) => i + 1).join(", ")}` : ""}</span>
      </div></button>`;
  }).join("") || '<p class="muted small">Nothing in this group.</p>';
}

// Cross-highlighting between claims and receipts.
function setLinked(rids, claimIdx, on) {
  rids.forEach((id) => $$(`.rcard[data-rid="${CSS.escape(id)}"]`).forEach((el) => el.classList.toggle("linked", on)));
  if (claimIdx !== null) claimIdx.forEach((i) => $$(`.claim[data-claim="${i}"]`).forEach((el) => el.classList.toggle("linked", on)));
}

document.addEventListener("mouseover", (e) => {
  const claim = e.target.closest(".claim");
  if (claim) { setLinked(claim.dataset.rids.split(" ").filter(Boolean), null, true); claim.classList.add("linked"); return; }
  const card = e.target.closest(".rcard");
  if (card && state.run) { setLinked([card.dataset.rid], citedBy(state.run, card.dataset.rid), true); return; }
  const chip = e.target.closest(".summary .cite");
  if (chip && state.run) setLinked([chip.dataset.r], citedBy(state.run, chip.dataset.r), true);
});
document.addEventListener("mouseout", (e) => {
  if (e.target.closest(".claim, .rcard, .summary .cite")) $$(".linked").forEach((el) => el.classList.remove("linked"));
});

// ------------------------------------------------------------ sandbox panel

function renderSandbox(sb, phase) {
  if (!sb || !sb.backend) return;
  const cls = phase === "destroyed" ? "off" : (sb.isolated ? "on" : "mock");
  const label = sb.backend === "replay" ? (phase === "destroyed" ? "replay finished" : "replaying recording")
    : phase === "destroyed" ? "destroyed after run" : (sb.isolated ? "running, isolated" : "mock, not isolated");
  const lim = sb.limits || {};
  $("#sandbox-info").innerHTML = `<span class="life ${cls}">${label}</span>
    <dl class="kv">
      <dt>backend</dt><dd>${esc(sb.backend)} / ${esc(sb.runtime || "")}</dd>
      <dt>container</dt><dd>${esc(sb.container_id)}</dd>
      <dt>browser</dt><dd>${esc(lim.browser ? `${lim.browser.memory}, ${lim.browser.cpus} cpu, ${lim.browser.pids} pids` : "n/a")}</dd>
      <dt>code</dt><dd>${esc(lim.code ? `${lim.code.memory}, ${lim.code.cpus} cpu, ${lim.code.timeout_s}s, no network` : "n/a")}</dd>
      <dt>secrets</dt><dd>none inside</dd>
    </dl>
    <p class="muted small" style="margin:8px 0 0">${esc(sb.notes || "")}</p>`;
}

// ------------------------------------------------------------ live log

function iconFor(ev) {
  return { run_started: "▶", sandbox_created: "⬢", step: "◆", tool_result: ev.ok ? "✓" : "✗", retry: "↻",
    containment: "⛨", guard: "⚠", snap: "≈", evidence: "◎", verification: "✔", drafted: "✎", final: "★", fallback: "⚠",
    sandbox_destroyed: "⬡", error: "✗", run_finished: "■" }[ev.type] || "•";
}

function addEvent(ev, live) {
  const log = $("#log");
  const li = document.createElement("li");
  li.className = "ev" + (live ? " fresh" : "");
  let title = ev.type.replace(/_/g, " "), body = "", extra = "";
  switch (ev.type) {
    case "run_started":
      body = esc(ev.llm);
      if (ev.mode === "replay") { li.classList.add("guard"); title = "replay started"; body = `Replaying a recording from ${esc(ev.replay_of && ev.replay_of.recorded_at)}. Nothing is executed live.`; }
      break;
    case "sandbox_created":
      li.classList.add("sandbox"); title = "sandbox created";
      body = `${esc(ev.backend)} ${esc(ev.container_id)} · ${ev.isolated ? "isolated" : ev.backend === "replay" ? "replay" : "NOT isolated (mock)"}`; break;
    case "step":
      li.classList.add("step"); title = `step ${ev.step}: ${ev.action}`;
      body = esc(ev.thought);
      if (ev.args && Object.keys(ev.args).length) {
        const a = ev.args.code ? `<pre>${esc(ev.args.code)}</pre>` : `<code>${esc(short(JSON.stringify(ev.args), 300))}</code>`;
        extra = `<details><summary>args</summary>${a}</details>`;
      }
      break;
    case "tool_result":
      li.classList.add(ev.ok ? "ok" : "err");
      title = `${ev.tool} ${ev.ok ? "ok" : "failed"}${ev.receipt_id ? " → " + ev.receipt_id : ""}`;
      body = esc(ev.ok ? (ev.summary || "") : ev.error) + (ev.url ? ` <span class="muted">${esc(host(ev.url))}</span>` : "");
      if (ev.stderr && ev.stderr.trim()) extra += `<details><summary>stderr</summary><pre>${esc(ev.stderr)}</pre></details>`;
      if (ev.stdout && ev.stdout.trim()) extra += `<details><summary>stdout</summary><pre>${esc(ev.stdout)}</pre></details>`;
      if (ev.blocked_requests && ev.blocked_requests.length)
        extra += `<details><summary>${ev.blocked_requests.length} blocked requests</summary><pre>${esc(ev.blocked_requests.join("\n"))}</pre></details>`;
      if (ev.injection_suspected) li.classList.add("guard");
      if (ev.receipt_id) li.dataset.r = ev.receipt_id;
      break;
    case "retry": li.classList.add("guard"); title = "self-heal retry"; body = esc(ev.message); break;
    case "fallback": li.classList.add("guard"); title = "model fallback"; body = esc(ev.message); break;
    case "snap": li.classList.add("guard"); title = "quote snapped to source"; body = esc(ev.message); break;
    case "containment": li.classList.add("contain"); title = "containment"; body = esc(ev.message); break;
    case "guard": li.classList.add("guard"); title = "prompt injection detected"; body = esc(ev.message); break;
    case "evidence": body = esc(ev.message); break;
    case "verification":
      li.classList.add(ev.status === "verified" ? "ok" : ev.status === "partial" ? "guard" : "err");
      title = `claim ${ev.claim + 1}: ${ev.status}`;
      body = `quote match ${(ev.score * 100).toFixed(0)}%` + (ev.vision ? ` · vision: ${esc(ev.vision.reason || "")}` : ""); break;
    case "drafted": title = "answer drafted"; body = `${ev.n_claims} claims to verify`; break;
    case "final": li.classList.add("final"); title = "answer ready";
      body = `${ev.counts.verified} verified, ${ev.counts.partial} partial, ${ev.counts.unverified} unverified`; break;
    case "sandbox_destroyed": li.classList.add("sandbox"); title = "sandbox destroyed"; body = esc(ev.message); break;
    case "error": li.classList.add("err"); body = esc(ev.message); break;
    case "run_finished": body = esc(ev.status); break;
    default: body = esc(ev.message || "");
  }
  li.innerHTML = `<div class="h"><span>${iconFor(ev)} ${esc(title)}</span><span class="t">+${ev.t}s</span></div>
    <div class="b">${body}</div>${extra}`;
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
}

// ------------------------------------------------------------ receipt modal + hash verification

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

async function verifyReceipt(r, out) {
  out.className = "verify-out pending";
  out.textContent = "Recomputing SHA-256…";
  try {
    let res;
    if (window.crypto && crypto.subtle) res = await verifyInBrowser(r);
    else {
      const s = await api(`/api/runs/${state.runId}/receipts/${r.id}/verify`);
      res = { recomputed: s.recomputed, image_bytes: s.image_bytes, where: "on the server (this page is not on HTTPS, so the browser can't hash)" };
    }
    const ok = res.recomputed === r.sha256;
    out.className = "verify-out " + (ok ? "good" : "bad");
    out.innerHTML = `${ok ? "✓ Hash matches." : "✗ Hash mismatch: this receipt was altered."} Recomputed ${esc(res.where)}
      from ${r.text.length.toLocaleString()} chars of captured text${res.image_bytes ? ` and ${res.image_bytes.toLocaleString()} screenshot bytes` : ""}.
      <code>${esc(res.recomputed)}</code>`;
  } catch (e) {
    out.className = "verify-out bad";
    out.textContent = `Could not verify: ${e.message}`;
  }
}

async function showReceipt(rid) {
  if (!state.runId) return;
  const body = $("#modal-body");
  $("#modal-title").textContent = `${rid} · loading…`;
  body.innerHTML = `<div class="skeleton"><span></span><span></span><span class="short"></span></div>`;
  if (!$("#modal").open) $("#modal").showModal();
  let r;
  try { r = await api(`/api/runs/${state.runId}/receipts/${rid}`); } catch (e) {
    body.innerHTML = `<p class="error-text">Couldn't load receipt ${esc(rid)}: ${esc(e.message)}</p>`; return;
  }
  const img = r.has_image ? `<img class="shot" alt="Screenshot receipt ${r.id}" src="/api/runs/${state.runId}/receipts/${r.id}/image">` : "";
  const main = r.kind === "code"
    ? `${img}<h4>Code <span class="muted small">ran in a fresh container with no network</span></h4><pre>${esc(r.code)}</pre>
       <h4>stdout</h4><pre>${esc(r.stdout || "(empty)")}</pre>${r.stderr ? `<h4>stderr</h4><pre>${esc(r.stderr)}</pre>` : ""}`
    : `${img || '<p class="muted">Text-only receipt (fetched without a browser). The highlighted screenshot, if any, is a separate receipt.</p>'}
       <h4>Captured text</h4><pre>${esc(short(r.text, 6000))}</pre>`;
  const cites = state.run ? citedBy(state.run, r.id) : [];
  $("#modal-title").textContent = `${r.id} · ${r.kind === "code" ? "code output" : r.title || r.url}`;
  body.innerHTML = `<div class="meta-grid"><div>${main}</div>
    <aside>
      <button type="button" class="btn verify-btn" id="verify-btn">Verify SHA-256</button>
      <div class="verify-out" id="verify-out">Recompute this receipt's hash from its captured content and compare.</div>
      <dl class="kv">
        ${r.url ? `<dt>source</dt><dd><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(short(r.url, 80))}</a></dd>` : ""}
        <dt>tool</dt><dd>${esc(r.tool)}</dd>
        <dt>captured</dt><dd>${esc(r.captured_at)}</dd>
        <dt>sandbox</dt><dd>${esc(r.sandbox)}</dd>
        <dt>sha256</dt><dd>${esc(r.sha256)}</dd>
        ${cites.length ? `<dt>supports</dt><dd>claim ${cites.map((i) => i + 1).join(", ")}</dd>` : ""}
        ${r.highlight ? `<dt>highlight</dt><dd>${r.highlight_found ? "found on page" : "not found"}</dd><dt>quote</dt><dd>${esc(short(r.highlight, 300))}</dd>` : ""}
        ${r.exit_code !== null && r.exit_code !== undefined ? `<dt>exit code</dt><dd>${r.exit_code}</dd>` : ""}
        ${r.blocked_requests && r.blocked_requests.length ? `<dt>blocked</dt><dd>${esc(r.blocked_requests.join(", "))}</dd>` : ""}
      </dl>
    </aside></div>`;
  $("#verify-btn").onclick = () => verifyReceipt(r, $("#verify-out"));
}

// ------------------------------------------------------------ containment test

function runContainment() {
  const dlg = $("#contain");
  $("#contain-list").innerHTML = '<li class="pending"><span class="spinner" aria-hidden="true"></span> Starting a fresh sandbox…</li>';
  $("#contain-done").innerHTML = "";
  $("#contain-sandbox").textContent = "";
  dlg.showModal();
  const code = $("#access").value || store.get("rwr-code");
  const es = new EventSource(`/api/containment-test${code ? `?code=${encodeURIComponent(code)}` : ""}`);
  let first = true, n = 0, held = 0;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "sandbox") {
      $("#contain-sandbox").textContent = `Sandbox ${ev.backend} ${ev.container_id} (${ev.isolated ? "isolated" : "mock, not isolated"})`;
      return;
    }
    if (first) { $("#contain-list").innerHTML = ""; first = false; }
    if (ev.type === "attack") {
      n += 1; if (ev.contained) held += 1;
      const li = document.createElement("li");
      li.innerHTML = `<div class="h"><span>${esc(ev.title)}</span>
        <span class="badge ${ev.contained ? "verified" : "unverified"}">${ev.contained ? "contained" : "NOT contained"}</span></div>
        <div class="muted small">${esc(ev.note)} · ${ev.duration_s}s</div>
        <details><summary>attack and sandbox output</summary><pre>${esc(ev.code)}</pre>${ev.stdout ? `<pre>${esc(ev.stdout)}</pre>` : ""}${ev.stderr ? `<pre>${esc(ev.stderr)}</pre>` : ""}</details>`;
      $("#contain-list").appendChild(li);
    } else if (ev.type === "done") {
      $("#contain-done").innerHTML = `<div class="done-banner ${held === n ? "" : "bad"}">${held}/${n} attacks contained. ${esc(ev.message)} Host check: ${ev.host_ok ? "OK" : "FAILED"}.</div>`;
      es.close();
    } else if (ev.type === "error") {
      $("#contain-done").innerHTML = `<p class="error-text">${esc(ev.message)}</p>`;
      es.close();
    }
  };
  es.onerror = () => {
    if (first) $("#contain-list").innerHTML = '<li class="error-text">Lost the connection to the backend.</li>';
    es.close();
  };
}

// ------------------------------------------------------------ wiring

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) { state.tab = tab.dataset.tab; if (state.run) renderReceipts(state.run); return; }
  const t = e.target.closest("[data-r]");
  if (t) { e.preventDefault(); showReceipt(t.dataset.r); return; }
  const c = e.target.closest("[data-close]");
  if (c) c.closest("dialog").close();
});
// Click on the backdrop closes dialogs.
$$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) startRun(q); });
$("#q").addEventListener("input", syncReplayToggle);
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#ask").requestSubmit(); });
$("#btn-contain").addEventListener("click", runContainment);
window.addEventListener("hashchange", route);

function route() {
  const m = location.hash.match(/run=([\w-]+)/);
  if (m && m[1] !== state.runId) openRun(m[1]);
  if (!m) { state.runId = null; renderEmpty(); }
}

async function boot() {
  $("#access").value = store.get("rwr-code");
  renderEmpty();
  const ok = await loadHealth();
  loadHistory();
  route();
  if (!ok) {
    // Keep retrying quietly until the backend is back.
    const timer = setInterval(async () => { if (await loadHealth()) { clearInterval(timer); loadHistory(); } }, 4000);
  }
}
boot();
