/* Collateral Studio — front-end logic (vanilla, no build step).
   Talks to the FastAPI backend on the same origin. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = { pair: "nimbus__vanguard", templates: [], lastResult: null };

document.addEventListener("DOMContentLoaded", () => {
  $("#pair").addEventListener("input", (e) => { state.pair = e.target.value.trim(); });
  checkHealth();
  loadTemplates();
  wireDrops();
  $("#generate").addEventListener("click", generate);
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
});

/* ───────────── health + templates ───────────── */
async function checkHealth() {
  const dot = $("#health-dot"), text = $("#health-text"), models = $("#health-models");
  try {
    const r = await fetch("/health");
    const h = await r.json();
    if (h.llm_configured) {
      dot.className = "dot ok"; text.textContent = "service ready";
    } else {
      dot.className = "dot warn"; text.textContent = "service up · no API key";
    }
    if (h.models) models.textContent = `${h.models.writer} · ${h.models.parser}`;
  } catch {
    dot.className = "dot err"; text.textContent = "service unavailable";
  }
}

async function loadTemplates() {
  const sel = $("#template");
  try {
    const r = await fetch("/templates");
    state.templates = await r.json();
    sel.innerHTML = "";
    state.templates.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.id; o.textContent = `${t.name} (${t.blocks.length} blocks)`;
      sel.appendChild(o);
    });
  } catch {
    sel.innerHTML = '<option value="one_pager_v1">One-pager bridge article</option>';
  }
}

/* ───────────── uploads (multipart) + job polling ───────────── */
function wireDrops() {
  $$(".drop").forEach((drop) => {
    const input = drop.querySelector("input[type=file]");
    const role = input.dataset.role;
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("dragover"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault(); drop.classList.remove("dragover");
      if (e.dataTransfer.files.length) uploadRole(role, e.dataTransfer.files, drop);
    });
    input.addEventListener("change", () => {
      if (input.files.length) uploadRole(role, input.files, drop);
    });
  });
}

async function uploadRole(role, files, drop) {
  const stateEl = drop.querySelector(".state");
  const dz = drop.querySelector(".dz");
  const names = Array.from(files).map((f) => f.name).join(", ");
  dz.textContent = names.length > 28 ? names.slice(0, 28) + "…" : names;
  drop.classList.add("has-file");
  setState(stateEl, "work", "uploading…");

  const fd = new FormData();
  fd.append("role", role);
  Array.from(files).forEach((f) => fd.append("files", f));

  try {
    const r = await fetch(`/companies/${encodeURIComponent(state.pair)}/documents`, { method: "POST", body: fd });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    const j = await r.json();
    setState(stateEl, "work", "parsing…");
    pollJob(j.job_id, stateEl);
  } catch (err) {
    setState(stateEl, "err", String(err.message || err).slice(0, 40));
  }
}

async function pollJob(jobId, stateEl, tries = 0) {
  if (tries > 40) { setState(stateEl, "err", "parse timed out"); return; }
  try {
    const r = await fetch(`/jobs/${encodeURIComponent(state.pair)}/${jobId}`);
    const j = await r.json();
    if (j.status === "done") { setState(stateEl, "ok", "brief ready ✓"); return; }
    if (j.status === "error") { setState(stateEl, "err", (j.message || "parse error").slice(0, 40)); return; }
    setTimeout(() => pollJob(jobId, stateEl, tries + 1), 2500);
  } catch {
    setTimeout(() => pollJob(jobId, stateEl, tries + 1), 2500);
  }
}

function setState(el, kind, text) { el.className = "state " + kind; el.textContent = text; }

/* ───────────── generate ───────────── */
async function generate() {
  const btn = $("#generate"), err = $("#error");
  err.hidden = true;
  btn.disabled = true; const label = btn.textContent; btn.textContent = "generating…";
  try {
    const body = { pair_id: state.pair, prompt: $("#prompt").value.trim(), template_id: $("#template").value };
    // async: POST returns a job_id (202); poll until the article is ready.
    const r = await fetch("/generate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    const job = await r.json();                 // { job_id, status, poll }
    render(await pollGenerate(job.job_id));
  } catch (e) {
    err.hidden = false; err.textContent = String(e.message || e);
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

function pollGenerate(jobId) {
  return new Promise((resolve, reject) => {
    const tick = async (n) => {
      if (n > 60) return reject(new Error("generation timed out"));
      try {
        const r = await fetch(`/generate/${encodeURIComponent(state.pair)}/${jobId}`);
        const j = await r.json();
        if (j.status === "done") return resolve(j.result);
        if (j.status === "error") return reject(new Error(j.message || "generation error"));
      } catch { /* transient — keep polling */ }
      setTimeout(() => tick(n + 1), 1500);
    };
    tick(0);
  });
}

/* ───────────── render ───────────── */
function render(result) {
  state.lastResult = result;
  $("#placeholder").hidden = true;
  $("#tabs").hidden = false;
  if (result.template_id === "executive_mono_v1") renderExecutive(result);
  else renderBrochure(result);
  renderInspect(result);
  $("#json").innerHTML = highlightJSON(result);
  switchTab("preview");
}

function pct(x) { return Math.round((x ?? 0) * 100) + "%"; }

function esc(s) { return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function renderBrochure(r) {
  const wrap = $("#brochure");
  const blocks = r.blocks || [];
  const bodies = blocks.filter((b) => b.type === "body");
  let html = "";
  let bodyRendered = false;

  for (const b of blocks) {
    if (b.type === "heading") {
      const col = b.color ? `#${b.color}` : "var(--paper-ink)";
      html += `<h1 class="b-head" style="color:${col}">${esc(b.text)}</h1>`;
    } else if (b.type === "subheading") {
      html += `<p class="b-sub">${esc(b.text)}</p>`;
    } else if (b.type === "caption" && b.image_ref) {
      const src = `/assets/${encodeURIComponent(r.pair_id)}/${encodeURIComponent(b.image_ref)}`;
      html += `<div class="b-hero">
        <img src="${src}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';" />
        <div class="ph" style="display:none"><span class="ic">◳</span><span class="id">${esc(b.image_ref)}</span></div>
        <span class="cap">${esc(b.text)}</span></div>`;
    } else if (b.type === "caption") {
      html += `<blockquote class="b-quote">${esc(b.text)}</blockquote>`;
    } else if (b.type === "body") {
      if (bodies.length === 2) {
        if (!bodyRendered) {
          html += `<div class="b-cols">${bodies.map((x) => `<p class="b-body">${esc(x.text)}</p>`).join("")}</div>`;
          bodyRendered = true;
        }
      } else {
        html += `<p class="b-body">${esc(b.text)}</p>`;
      }
    } else if (b.type === "cta") {
      html += `<div><span class="b-cta">${esc(b.text)}</span></div>`;
    }
  }
  wrap.innerHTML = html;
}

/* ───────────── executive (monochrome 2-page) render ───────────── */
function exStat(s) {
  const t = (s || "").trim();
  const m = t.match(/^([+~]?[\d.,]+\s*(?:%|pts?|k|x|hrs?|wks?|m|bn|\+)?)\s+(.+)$/i);
  const n = m ? m[1].trim() : (t.split(" ")[0] || "");
  const l = m ? m[2] : t.split(" ").slice(1).join(" ");
  return `<div class="ex-stat"><div class="ex-n">${esc(n)}</div><div class="ex-l">${esc(l)}</div></div>`;
}

async function renderExecutive(r) {
  const wrap = $("#brochure");
  wrap.className = "brochure exec";
  const byId = Object.fromEntries((r.blocks || []).map((b) => [b.id, b]));
  const pid = r.pair_id;
  const asset = (ref) => `/assets/${encodeURIComponent(pid)}/${encodeURIComponent(ref)}`;

  // the brief supplies masthead names + stats + logo; degrade gracefully if it's missing
  let sender = null, receiver = null;
  try {
    const br = await fetch(`/companies/${encodeURIComponent(pid)}`).then((x) => x.json());
    sender = br.sender; receiver = br.receiver;
  } catch (e) { /* no brief — render without names/stats */ }
  const senderName = (sender && sender.name) || "Collateral";
  const receiverName = (receiver && receiver.name) || "the recipient";
  const logoRef = sender && sender.logo_asset;
  const stats = ((sender && sender.key_stats) || []).slice(0, 3);

  const photo = (b, cls) => (b && b.image_ref)
    ? `<figure class="ex-photo ${cls || ""}"><img src="${asset(b.image_ref)}" alt="" onerror="this.closest('.ex-photo').classList.add('empty')"/>${b.text ? `<figcaption class="ex-tag">${esc(b.text)}</figcaption>` : ""}</figure>`
    : "";
  const shots = ["shot_a", "shot_b", "shot_c"].map((id) => byId[id]).filter((b) => b && b.image_ref);

  let h = `<article class="exec">`;
  h += `<header class="ex-mast"><span class="ex-kick">Prepared for — ${esc(receiverName)}</span>` +
    `<span class="ex-wm">${logoRef ? `<img class="ex-logo" src="${asset(logoRef)}" alt="" onerror="this.style.display='none'"/>` : `<span class="ex-mk"></span>`}<span class="ex-nm">${esc(senderName)}</span></span></header><div class="ex-rule"></div>`;
  h += photo(byId.hero, "ex-hero");
  if (byId.headline) h += `<h1 class="ex-h1">${esc(byId.headline.text)}</h1>`;
  if (byId.standfirst) h += `<p class="ex-stand">${esc(byId.standfirst.text)}</p>`;
  if (stats.length) h += `<div class="ex-stats">${stats.map(exStat).join("")}</div>`;
  if (byId.intro) h += `<p class="ex-lede">${esc(byId.intro.text)}</p>`;
  h += `<div class="ex-break"></div>`;
  if (byId.section) h += `<h2 class="ex-h2">${esc(byId.section.text)}</h2>`;
  if (byId.body) h += `<div class="ex-body">${esc(byId.body.text)}</div>`;
  if (shots.length) {
    const grid = shots.map((b, i) => photo(b, (shots.length % 2 === 1 && i === shots.length - 1) ? "wide" : "")).join("");
    h += `<div class="ex-grid">${grid}</div>`;
  }
  if (byId.quote && byId.quote.text) h += `<blockquote class="ex-quote">${esc(byId.quote.text)}</blockquote>`;
  if (byId.cta) h += `<div class="ex-cta"><span class="ex-cta-t">${esc(byId.cta.text)}</span></div>`;
  h += `</article>`;
  wrap.innerHTML = h;
}

function renderInspect(r) {
  const tb = $("#inspect tbody");
  tb.innerHTML = "";
  for (const b of r.blocks || []) {
    const within = (b.min_words == null || b.words >= b.min_words) && (b.max_words == null || b.words <= b.max_words);
    const over = b.max_words != null && b.words > b.max_words;
    const repaired = (r.constraints?.repaired_blocks || []).includes(b.id);
    const truncated = (r.constraints?.truncated_blocks || []).includes(b.id);
    let status = `<span class="s-ok">✓ within</span>`;
    if (!within) status = `<span class="s-bad">✗ ${over ? "over" : "under"}</span>`;
    else if (truncated) status = `<span class="s-bad">truncated</span>`;
    else if (repaired) status = `<span class="s-rep">✓ repaired</span>`;

    const pctFill = b.max_words ? Math.min(100, Math.round((b.words / b.max_words) * 100)) : 0;
    const budget = b.min_words != null ? `${b.min_words}–${b.max_words}<span class="bar ${over ? "over" : ""}"><i style="width:${pctFill}%"></i></span>` : "—";
    const cites = (b.citations || []).length
      ? `<span class="s-cite">${(b.citations || []).join(", ")}</span>` : `<span class="s-cite">—</span>`;

    tb.insertAdjacentHTML("beforeend",
      `<tr><td class="bid">${esc(b.id)}</td><td>${b.type}</td><td>${b.words}</td><td>${budget}</td><td>${status}</td><td>${cites}</td></tr>`);
  }

  const f = r.faithfulness || {};
  let detail = `<h4>faithfulness</h4>${f.supported ?? 0} / ${f.total_claims ?? 0} factual blocks supported by the briefs (score ${pct(f.score)}).`;
  if ((f.unsupported_claims || []).length) {
    detail += "<br>flagged: " + f.unsupported_claims.map((u) => `<code>${esc(u)}</code>`).join(", ");
  } else {
    detail += " No unsupported claims.";
  }
  detail += "<br><br>Citations reference brief fact-ids (e.g. <code>recv.fact.3</code>) — internal verification metadata, not footnotes in the brochure.";
  $("#faith-detail").innerHTML = detail;
}

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  ["preview", "inspect", "json"].forEach((p) => { $("#pane-" + p).hidden = p !== name; });
}

/* tiny JSON syntax highlighter */
function highlightJSON(obj) {
  const json = JSON.stringify(obj, null, 2);
  return json
    .replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))
    .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="s">"$1"</span>')
    .replace(/: (true|false|null)/g, ': <span class="b">$1</span>')
    .replace(/: (-?\d+\.?\d*)/g, ': <span class="n">$1</span>');
}
