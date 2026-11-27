// Research with Receipts: single-page UI (vanilla JS, no build step).
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { health: null, runId: null, source: null, run: null };

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
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

// ------------------------------------------------------------ health + examples

async function loadHealth() {
  try {
    const h = await api("/api/health");
    state.health = h;
    const ps = $("#pill-sandbox");
    ps.textContent = h.sandbox_isolated ? `sandbox: docker (${h.sandbox_runtime})` : "sandbox: mock (dev only)";
    ps.className = "pill " + (h.sandbox_isolated ? "ok" : "warn");
    const pl = $("#pill-llm");
    pl.textContent = h.llm === "mock" ? "llm: mock policy" : `llm: vultr ${h.llm_model}`;
    pl.className = "pill " + (h.llm === "mock" ? "warn" : "ok");
    $("#code-row").hidden = !h.access_code_required;
    renderExamples(h.public_base_url);
  } catch (e) {
    $("#pill-sandbox").textContent = "backend offline";
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
  const ex = [
    { k: "Browser + receipts", q: "How does gVisor isolate containers, and what performance cost does it have compared to runc?" },
    { k: "Browser + Python", q: "What are the three most populous countries today, and what share of the world population do they hold together?" },
    { k: "Containment (hostile page)", q: `Summarize the product page at ${base}/demo/hostile and list its price and colours.` },
  ];
  $("#examples").innerHTML = ex.map((e, i) =>
    `<button type="button" class="example" data-i="${i}"><span class="k">${esc(e.k)}${recordingFor(e.q) ? ' <span class="rec">recorded</span>' : ""}</span>${esc(e.q)}</button>`).join("");
  $("#examples").querySelectorAll(".example").forEach((b) =>
    b.addEventListener("click", () => { $("#q").value = ex[+b.dataset.i].q; syncReplayToggle(); $("#q").focus(); }));
}

// ------------------------------------------------------------ history

async function loadHistory() {
  try {
    const runs = await api("/api/runs");
    const ul = $("#history");
    if (!runs.length) { ul.innerHTML = '<li class="muted">No runs yet.</li>'; return; }
    ul.innerHTML = runs.slice(0, 30).map((r) =>
      `<li><a href="#run=${r.id}" class="${r.id === state.runId ? "active" : ""}">${esc(short(r.question, 70))}
        <span class="st">${esc(r.status)} · ${r.n_receipts} receipts</span></a></li>`).join("");
  } catch { /* ignore */ }
}

// ------------------------------------------------------------ run lifecycle

async function startRun(question) {
  const btn = $("#go");
  btn.disabled = true; btn.textContent = "Starting sandbox…";
  try {
    const code = $("#access").value; if (code) store.set("rwr-code", code);
    const mode = $("#replay").checked ? "replay" : "auto";
    const { id } = await api("/api/runs", { method: "POST", body: JSON.stringify({ question, mode }) });
    location.hash = `run=${id}`;
  } catch (e) {
    alertBox(`Could not start run: ${e.message}`);
    btn.disabled = false; btn.textContent = "Research it";
  }
}

function alertBox(msg) {
  $("#answer").innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(msg)}</p></div>`;
}

async function openRun(id) {
  if (state.source) { state.source.close(); state.source = null; }
  state.runId = id;
  $("#log").innerHTML = "";
  $("#answer").innerHTML = `<div class="q">Question</div><div class="summary muted">Loading…</div>`;
  $("#receipts-card").hidden = true;
  let run;
  try { run = await api(`/api/runs/${id}`); } catch { alertBox("Run not found."); return; }
  state.run = run;
  renderQuestion(run);
  renderSandbox(run.sandbox, run.status === "done" || run.status === "failed" ? "destroyed" : "running");
  loadHistory();
  if (run.status === "done" || run.status === "failed") {
    run.events.forEach(addEvent);
    renderAnswer(run);
    return;
  }
  $("#live").hidden = false;
  const btn = $("#go"); btn.disabled = true; btn.textContent = "Researching…";
  const es = new EventSource(`/api/runs/${id}/events`);
  state.source = es;
  es.onmessage = async (m) => {
    const ev = JSON.parse(m.data);
    addEvent(ev);
    if (ev.type === "sandbox_created") renderSandbox(ev, "running");
    if (ev.type === "sandbox_destroyed") renderSandbox({ ...(state.run.sandbox || {}), ...ev }, "destroyed");
    if (ev.type === "tool_result" && ev.receipt_id) refreshReceipts();
    if (ev.type === "run_finished") {
      es.close(); state.source = null; $("#live").hidden = true;
      btn.disabled = false; btn.textContent = "Research it";
      const full = await api(`/api/runs/${id}`);
      state.run = full;
      renderSandbox(full.sandbox, "destroyed");
      renderAnswer(full);
      loadHistory();
    }
  };
  es.onerror = () => { /* EventSource retries on its own */ };
}

let refreshTimer = null;
function refreshReceipts() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try { const r = await api(`/api/runs/${state.runId}`); state.run = r; renderReceipts(r); } catch { /* ignore */ }
  }, 300);
}

// ------------------------------------------------------------ rendering

function renderQuestion(run) {
  $("#answer").innerHTML = `<div class="q">Question</div><h2 style="font-size:18px">${esc(run.question)}</h2>
    <div class="summary muted">${run.status === "failed" ? "Run failed: " + esc(run.error) : "Working… follow the live log."}</div>`;
}

function renderSandbox(sb, phase) {
  if (!sb || !sb.backend) { return; }
  const cls = phase === "destroyed" ? "off" : (sb.isolated ? "on" : "mock");
  const label = phase === "destroyed" ? "destroyed after run" : (sb.isolated ? "running, isolated" : "mock, not isolated");
  const lim = sb.limits || {};
  $("#sandbox-info").innerHTML = `<span class="life ${cls}">${label}</span>
    <dl class="kv">
      <dt>backend</dt><dd>${esc(sb.backend)} / ${esc(sb.runtime || "")}</dd>
      <dt>container</dt><dd>${esc(sb.container_id)}</dd>
      <dt>browser</dt><dd>${esc(lim.browser ? `${lim.browser.memory}, ${lim.browser.cpus} cpu, ${lim.browser.pids} pids` : "")}</dd>
      <dt>code</dt><dd>${esc(lim.code ? `${lim.code.memory}, ${lim.code.cpus} cpu, ${lim.code.timeout_s}s, net none` : "")}</dd>
      <dt>secrets</dt><dd>none inside</dd>
    </dl>
    <p class="muted" style="font-size:12px;margin:8px 0 0">${esc(sb.notes || "")}</p>`;
}

function citeChips(text) {
  return esc(text).replace(/\[(R\d+)\]/g, (_, id) => `<button type="button" class="cite" data-r="${id}">${id}</button>`);
}

function markQuote(quote, span) {
  const q = esc(quote);
  return `<mark>${q}</mark>`;
}

function renderAnswer(run) {
  const a = run.answer;
  const el = $("#answer");
  if (!a) {
    el.innerHTML = `<div class="q">Question</div><h2 style="font-size:18px">${esc(run.question)}</h2>
      <p class="summary" style="color:var(--bad)">Run failed: ${esc(run.error || "unknown error")}</p>`;
    renderReceipts(run);
    return;
  }
  const c = a.counts || {};
  const recs = Object.fromEntries((run.receipts || []).map((r) => [r.id, r]));
  const claims = (a.claims || []).map((cl, i) => {
    const v = cl.verification || { status: "unverified", quote_score: 0 };
    const ev = recs[cl.evidence_id] || (cl.receipt_ids || []).map((r) => recs[r]).find((r) => r && r.has_image);
    const thumb = ev && ev.has_image
      ? `<img class="thumb" loading="lazy" alt="Screenshot receipt ${ev.id}" data-r="${ev.id}" src="/api/runs/${run.id}/receipts/${ev.id}/image">`
      : `<div class="thumb" style="display:flex;align-items:center;justify-content:center;font:12px var(--mono)">${esc((cl.receipt_ids || []).join(" "))}</div>`;
    const vision = v.vision ? ` · vision ${v.vision.supports === true ? "agrees" : v.vision.supports === false ? "disagrees" : "n/a"}` : "";
    const chips = [...(cl.receipt_ids || []), ...(cl.evidence_id ? [cl.evidence_id] : [])]
      .map((r) => `<button type="button" class="cite" data-r="${esc(r)}">${esc(r)}</button>`).join("");
    return `<li class="claim">
      <div>
        <span class="badge ${v.status}">${v.status}</span>
        <div class="text">${i + 1}. ${esc(cl.text)} ${chips}</div>
        ${cl.quote ? `<blockquote>${markQuote(cl.quote)}</blockquote>` : ""}
        <div class="why">quote match ${(v.quote_score * 100).toFixed(0)}%${vision}${cl.evidence_id ? " · highlighted screenshot " + esc(cl.evidence_id) : ""}</div>
      </div>
      ${thumb}
    </li>`;
  }).join("");
  el.innerHTML = `<div class="q">Question</div><h2 style="font-size:18px">${esc(run.question)}</h2>
    <p class="summary">${citeChips(a.summary)}</p>
    <div class="counts">
      <span class="badge verified">${c.verified || 0} verified</span>
      <span class="badge partial">${c.partial || 0} partial</span>
      <span class="badge unverified">${c.unverified || 0} unverified</span>
      <span class="badge neutral">${(run.receipts || []).length} receipts</span>
      <span class="badge neutral">${a.retries || 0} self-heal retries</span>
      <span class="badge neutral">${esc(run.llm || "")}</span>
    </div>
    <ol class="claims">${claims || '<li class="muted">The model returned no claims.</li>'}</ol>`;
  renderReceipts(run);
}

function renderReceipts(run) {
  const list = run.receipts || [];
  $("#receipts-card").hidden = !list.length;
  $("#receipts-count").textContent = list.length ? `(${list.length})` : "";
  $("#receipts").innerHTML = list.map((r) => {
    const img = r.has_image
      ? `<img loading="lazy" alt="" src="/api/runs/${run.id}/receipts/${r.id}/image">`
      : `<div class="noimg">${esc(short(r.kind === "code" ? (r.stdout || r.stderr || r.code) : r.text, 160))}</div>`;
    const t = r.kind === "code" ? `python · exit ${r.exit_code}` : `${r.tool} · ${host(r.url)}`;
    return `<button type="button" class="rcard" data-r="${r.id}">${img}
      <div class="meta"><b>${r.id}</b>${esc(t)}<span class="t">${esc(r.title || r.code.split("\n")[0] || "")}</span></div></button>`;
  }).join("");
}

function iconFor(ev) {
  return { run_started: "▶", sandbox_created: "⬢", step: "◆", tool_result: ev.ok ? "✓" : "✗", retry: "↻",
    containment: "⛨", guard: "⚠", evidence: "◎", verification: "✔", drafted: "✎", final: "★",
    sandbox_destroyed: "⬡", error: "✗", run_finished: "■" }[ev.type] || "•";
}

function addEvent(ev) {
  const log = $("#log");
  const li = document.createElement("li");
  li.className = "ev";
  let title = ev.type.replace(/_/g, " "), body = "", extra = "";
  switch (ev.type) {
    case "run_started":
      body = esc(ev.llm);
      if (ev.mode === "replay") { li.classList.add("guard"); title = "replay started"; body = `Replaying a recording from ${esc(ev.replay_of && ev.replay_of.recorded_at)}. Nothing is executed live.`; }
      break;
    case "sandbox_created":
      li.classList.add("sandbox"); title = "sandbox created";
      body = `${esc(ev.backend)} ${esc(ev.container_id)} · ${ev.isolated ? "isolated" : "NOT isolated (mock)"}`; break;
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
      break;
    case "retry": li.classList.add("guard"); title = "self-heal retry"; body = esc(ev.message); break;
    case "containment": li.classList.add("contain"); title = "containment"; body = esc(ev.message); break;
    case "guard": li.classList.add("guard"); title = "prompt injection detected"; body = esc(ev.message); break;
    case "evidence": body = esc(ev.message); break;
    case "verification":
      li.classList.add(ev.status === "verified" ? "ok" : ev.status === "partial" ? "guard" : "err");
      title = `claim ${ev.claim + 1}: ${ev.status}`;
      body = `quote match ${(ev.score * 100).toFixed(0)}%` + (ev.vision ? ` · vision: ${esc(ev.vision.reason || "")}` : ""); break;
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

// ------------------------------------------------------------ receipt modal

async function showReceipt(rid) {
  if (!state.runId) return;
  let r;
  try { r = await api(`/api/runs/${state.runId}/receipts/${rid}`); } catch { return; }
  const img = r.has_image ? `<img class="shot" alt="Screenshot receipt ${r.id}" src="/api/runs/${state.runId}/receipts/${r.id}/image">` : "";
  const main = r.kind === "code"
    ? `${img}<h4>Code (ran in a fresh container, no network)</h4><pre>${esc(r.code)}</pre>
       <h4>stdout</h4><pre>${esc(r.stdout || "(empty)")}</pre>${r.stderr ? `<h4>stderr</h4><pre>${esc(r.stderr)}</pre>` : ""}`
    : `${img || '<p class="muted">Text-only receipt (fetched without a browser). The highlighted screenshot, if any, is a separate evidence receipt.</p>'}
       <h4>Captured text</h4><pre>${esc(short(r.text, 6000))}</pre>`;
  $("#modal-title").textContent = `${r.id} · ${r.kind === "code" ? "code output" : r.title || r.url}`;
  $("#modal-body").innerHTML = `<div class="meta-grid"><div>${main}</div>
    <dl class="kv">
      ${r.url ? `<dt>source</dt><dd><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(short(r.url, 80))}</a></dd>` : ""}
      <dt>tool</dt><dd>${esc(r.tool)}</dd>
      <dt>captured</dt><dd>${esc(r.captured_at)}</dd>
      <dt>sandbox</dt><dd>${esc(r.sandbox)}</dd>
      <dt>sha256</dt><dd>${esc(r.sha256)}</dd>
      ${r.highlight ? `<dt>highlight</dt><dd>${r.highlight_found ? "found on page" : "not found"}</dd><dt>quote</dt><dd>${esc(short(r.highlight, 300))}</dd>` : ""}
      ${r.exit_code !== null && r.exit_code !== undefined ? `<dt>exit code</dt><dd>${r.exit_code}</dd>` : ""}
      ${r.blocked_requests && r.blocked_requests.length ? `<dt>blocked</dt><dd>${esc(r.blocked_requests.join(", "))}</dd>` : ""}
    </dl></div>`;
  $("#modal").showModal();
}

// ------------------------------------------------------------ containment test

function runContainment() {
  const dlg = $("#contain");
  $("#contain-list").innerHTML = '<li class="pending">Starting a fresh sandbox…</li>';
  $("#contain-done").innerHTML = "";
  $("#contain-sandbox").textContent = "";
  dlg.showModal();
  const code = $("#access").value || store.get("rwr-code");
  const es = new EventSource(`/api/containment-test${code ? `?code=${encodeURIComponent(code)}` : ""}`);
  let first = true;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "sandbox") {
      $("#contain-sandbox").textContent = `Sandbox ${ev.backend} ${ev.container_id} (${ev.isolated ? "isolated" : "mock, not isolated"})`;
      return;
    }
    if (first) { $("#contain-list").innerHTML = ""; first = false; }
    if (ev.type === "attack") {
      const li = document.createElement("li");
      li.innerHTML = `<div class="h"><span>${esc(ev.title)}</span>
        <span class="badge ${ev.contained ? "verified" : "unverified"}">${ev.contained ? "contained" : "NOT contained"}</span></div>
        <div class="muted" style="font-size:13px">${esc(ev.note)} · ${ev.duration_s}s${ev.sandbox_said ? " · " + esc(short(ev.sandbox_said, 140)) : ""}</div>
        <details><summary>attack</summary><pre>${esc(ev.code)}</pre>${ev.stdout ? `<pre>${esc(ev.stdout)}</pre>` : ""}${ev.stderr ? `<pre>${esc(ev.stderr)}</pre>` : ""}</details>`;
      $("#contain-list").appendChild(li);
    } else if (ev.type === "done") {
      $("#contain-done").innerHTML = `<div class="done-banner">${esc(ev.message)} Host check: ${ev.host_ok ? "OK" : "FAILED"}.</div>`;
      es.close();
    } else if (ev.type === "error") {
      $("#contain-done").innerHTML = `<p style="color:var(--bad)">${esc(ev.message)}</p>`;
      es.close();
    }
  };
  es.onerror = () => es.close();
}

// ------------------------------------------------------------ wiring

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-r]");
  if (t) { e.preventDefault(); showReceipt(t.dataset.r); return; }
  const c = e.target.closest("[data-close]");
  if (c) c.closest("dialog").close();
});
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) startRun(q); });
$("#q").addEventListener("input", syncReplayToggle);
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#ask").requestSubmit(); });
$("#btn-contain").addEventListener("click", runContainment);
window.addEventListener("hashchange", route);

function route() {
  const m = location.hash.match(/run=([\w-]+)/);
  if (m && m[1] !== state.runId) openRun(m[1]);
}

$("#access").value = store.get("rwr-code");
loadHealth().then(() => { loadHistory(); route(); });
