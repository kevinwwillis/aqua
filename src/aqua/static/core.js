// Shared app logic: data, markdown, list state, the question card.
// "live:" marks behavior added for the live server.
let ITEMS = []; // live: loaded from the server by shell.js
const state = { answered: new Map(), filter: "waiting", q: "" };

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
// breaks: a single newline is a line break (question and option text). The context passes false, as common Markdown renderers do:
// agents wrap their markdown files at ~120 characters, and forced breaks kept paragraphs in a narrow column.
function md(text, breaks = true) {
  const html = DOMPurify.sanitize(marked.parse(text || "", { gfm: true, breaks }));
  const div = document.createElement("div");
  div.innerHTML = html;
  div.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  return div.innerHTML;
}
// live: escaped, because labels come from agents
function plain(text) { return esc((text || "").replace(/[*_`#>\[\]]/g, "").replace(/\(https?:[^)]*\)/g, "").replace(/\s+/g, " ").trim()); }
function ago(ts) {
  const m = Math.max(1, Math.round((Date.now() - Date.parse(ts)) / 60000));
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 48) return h + "h " + (m % 60) + "m";
  return Math.floor(h / 24) + "d";
}
function leaf(p) { return p.split(/[\\/]/).pop(); }
// iPad layout: the Waiting list leaves out questions the user set aside; they have their own list
function visible() {
  return ITEMS.filter((it) => !state.answered.has(it.id) && !it.set_aside_at).sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
}
function asideList() {
  return ITEMS.filter((it) => !state.answered.has(it.id) && it.set_aside_at).sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
}
// The question below this one in its list; the one above when it was the last (answering must not
// jump back to the top, past the questions kept for later).
function nextAfter(id) {
  const it = ITEMS.find((x) => x.id === id), list = it && it.set_aside_at ? asideList() : visible();
  const i = list.findIndex((x) => x.id === id);
  if (i < 0) return (visible()[0] || {}).id;
  return (list[i + 1] || list[i - 1] || visible().find((x) => x.id !== id) || {}).id;
}
// live: "#gone:<id>" shows a question that left the inbox while it held unsent text (see orphans())
function selectedId() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("gone:") && draftText(h.slice(5))) return h;
  return [...visible(), ...asideList()].some((it) => it.id === h) ? h : (visible()[0] || {}).id;
}
function select(id) { location.hash = id || ""; }
function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.append(t); }
  t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200);
}
// live: unsent answers survive switching questions and reloads
function loadDraft(id) { try { return JSON.parse(localStorage.getItem("aqua.draft." + id) || "null"); } catch { return null; } }
function saveDraft(id, d) { try { localStorage.setItem("aqua.draft." + id, JSON.stringify(d)); } catch {} }
function clearDraft(id) { try { localStorage.removeItem("aqua.draft." + id); } catch {} }
function draftText(id) {
  const d = loadDraft(id); if (!d) return "";
  const parts = Object.values(d.sel || {}).flatMap((x) => [...(x.labels || []), ...(x.other && x.otherText ? [x.otherText] : [])]);
  if (d.note) parts.push("Note: " + d.note);
  return parts.join("\n").trim();
}
// Questions that left the inbox (withdrawn, or answered elsewhere) while the user had unsent text in them. Their drafts
// are kept until the user sends or discards them; they stay in the list so the text is never lost.
function orphans() {
  return draftKeys().filter((id) => !ITEMS.some((i) => i.id === id) && draftText(id))
    .map((id) => ({ id, ...((loadDraft(id) || {}).meta || {}) }));
}
function draftKeys() { const out = []; try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith("aqua.draft.")) out.push(k.slice(11)); } } catch {} return out; }
// live: posts the answer; on failure the card keeps everything the user entered and says why.
async function sendAnswer(it, result, onDone) {
  const next = nextAfter(it.id);
  const r = await fetch(`api/questions/${it.id}/answer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(result) })
    .catch(() => null);
  if (!r || !r.ok) {
    const why = r ? (r.status === 409 ? "it was already answered or the agent withdrew it" : `server said ${r.status}`) : "the server could not be reached";
    toast(`Not sent: ${why}.`);
    if (r && r.status === 409) { state.answered.set(it.id, true); onDone(draftText(it.id) ? "gone:" + it.id : next); }
    return false;
  }
  state.answered.set(it.id, result);
  clearDraft(it.id);
  toast(`Sent to ${it.sessionName}` + (it.blocking ? " — the agent resumes now" : " — the agent picks it up when it checks"));
  onDone(next);
  return true;
}
// j/k between rows
addEventListener("keydown", (e) => {
  if (e.target.closest("input,textarea,select,[contenteditable]") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key !== "j" && e.key !== "k") return;
  // within the list the shown question is in (Waiting or Set aside)
  const sid = selectedId(), cur = ITEMS.find((it) => it.id === sid);
  const v = cur && cur.set_aside_at ? asideList() : visible(), i = v.findIndex((it) => it.id === sid);
  const n = v[i + (e.key === "j" ? 1 : -1)];
  if (n) select(n.id);
});

// ---- question card ----
// mode "step": one question at a time with Back/Skip/Next/Send.
// mode "all": every question shown at once, one Send at the end.
function questionCard(it, { mode = "step", big = false, onSent }) {
  const root = document.createElement("div");
  root.className = "qc" + (big ? " big" : "");
  const qs = it.questions;
  qs.forEach((q, i) => (q._id = q._id || "q" + i));
  // live: start from the saved draft, if any
  const d0 = loadDraft(it.id) || {};
  const sel = d0.sel || {}; // id -> {labels:[], other:false, otherText:""}
  let idx = Math.min(Math.max(d0.idx || 0, 0), qs.length - 1), note = d0.note || "", collapsed = !!d0.collapsed;
  // the note is folded away behind the header's ✎; a draft that has one starts with it open
  let noteOpen = !!note.trim();
  const save = () => saveDraft(it.id, { sel, idx, note, collapsed, meta: { title: it.title, sessionName: it.sessionName, project: it.project } });
  const answerOf = (q) => { const s = sel[q._id]; if (!s) return []; return [...s.labels, ...(s.other && s.otherText.trim() ? [s.otherText.trim()] : [])]; };
  const choose = (q, label) => {
    const s = (sel[q._id] = sel[q._id] || { labels: [], other: false, otherText: "" });
    if (q.multiSelect) s.labels = s.labels.includes(label) ? s.labels.filter((l) => l !== label) : [...s.labels, label];
    // the typed Other text is kept (not sent) when a listed option is picked, so reopening Other brings it back
    else { s.labels = [label]; s.other = false; if (mode === "step" && idx < qs.length - 1) idx++; }
    draw();
  };
  const other = (q) => { const s = (sel[q._id] = sel[q._id] || { labels: [], other: false, otherText: "" }); s.other = true; if (!q.multiSelect) s.labels = []; draw(); requestAnimationFrame(() => root.querySelector(`[data-other="${q._id}"]`)?.focus()); };
  const send = (cancelled) => {
    // live: one entry per question, in order (null = skipped)
    const answers = cancelled ? [] : qs.map((q) => { const a = answerOf(q); return a.length ? (q.multiSelect ? a : a[0]) : null; });
    // live: one send at a time; the buttons stay disabled until the server replies
    if (root.dataset.busy) return;
    root.dataset.busy = "1"; root.querySelectorAll(".go, .x, .skip, .back, .tog, .notet").forEach((b) => (b.disabled = true));
    // live: no typing while the answer is on its way, so nothing typed after Send can be dropped (review 2026-10-20)
    root.querySelectorAll("textarea").forEach((t) => (t.readOnly = true));
    sendAnswer(it, { answers, note: note.trim(), cancelled: !!cancelled }, onSent).then((ok) => { if (ok) root.dataset.sent = "1"; }).finally(() => {
      delete root.dataset.busy; if (root.isConnected) draw();
    });
  };
  const optionsHtml = (q) => {
    const s = sel[q._id];
    return `<div class="opts">${(q.options || []).map((o, i) => `
      <div class="opt${s && s.labels.includes(o.label) ? " on" : ""}" data-q="${q._id}" data-l="${i}">
        <span class="ol"><button type="button" class="lab">${plain(o.label)}</button>${o.description ? `<span class="od md">${md(o.description)}</span>` : ""}</span>
        <span class="kc">${i + 1}</span></div>`).join("")}
      <div class="opt other${s && s.other ? " on" : ""}" data-q="${q._id}" data-other-row>
        <button type="button" class="lab otherbtn">Other <span class="kc">${(q.options || []).length + 1}</span></button>
        <textarea rows="1" data-other="${q._id}" placeholder="Type your own answer here">${s ? esc(s.otherText || "") : ""}</textarea>
      </div></div>`;
  };
  function draw() {
    if (!root.dataset.sent) save();
    const q = qs[idx];
    const header = `<div class="qh">${qs.length > 1 && mode === "step" ? `<span class="badge">${idx + 1}/${qs.length}</span>` : ""}
      <span class="ql">${collapsed ? plain(q.question) : mode === "step" ? plain(q.header || "Question") : qs.length + (qs.length > 1 ? " questions" : " question")}</span>
      ${mode === "step" && q.multiSelect ? `<span class="mut">Select one or more</span>` : ""}
      <span class="sp"></span>${collapsed ? "" : `<button class="ic notet${noteOpen || note.trim() ? " on" : ""}" title="${noteOpen ? "Hide note" : "Add a note"}">✎</button>`}<button class="ic tog" title="View question options">${collapsed ? "›" : "⌄"}</button><button class="ic x" title="Dismiss">✕</button></div>`;
    const body = collapsed ? "" : mode === "step"
      ? `<div class="qb"><div class="qq md">${md(q.question)}</div>${optionsHtml(q)}`
      : `<div class="qb">${qs.map((q, i) => `<section class="qsec"><div class="qhd">${qs.length > 1 ? `<span class="badge">${i + 1}</span>` : ""}<b>${plain(q.header || "")}</b>${q.multiSelect ? `<span class="mut">Select one or more</span>` : ""}</div><div class="qq md">${md(q.question)}</div>${optionsHtml(q)}</section>`).join("")}`;
    const last = mode !== "step" || idx === qs.length - 1;
    const ready = mode === "step" ? answerOf(q).length > 0 : qs.some((x) => answerOf(x).length);
    if (collapsed) { root.innerHTML = header; return; }
    const noteRow = noteOpen ? `<div class="noter"><textarea rows="1" class="note" placeholder="Note — sent with your answers, even if you skip">${esc(note)}</textarea><button class="ic mic" title="Dictate">🎙</button></div>` : "";
    const acts = `<div class="acts">${mode === "step" && idx > 0 ? `<button class="b back">Back</button>` : ""}<span class="sp"></span>
        ${mode === "step" ? `<button class="b skip">Skip</button>` : ""}</div>`;
    // Send / Next is a strip down the card's whole right edge, so a thumb finds it without reaching the bottom
    const strip = `<button class="go strip" ${ready ? "" : "disabled"}><span class="sk"><span class="sl">${last ? "Send" : "Next"} <i>${last ? "↵" : "→"}</i></span></span></button>`;
    root.innerHTML = `<div class="cg"><div class="cl">${header}${body}</div>${noteRow}${acts}</div>${strip}</div>`;
  }
  root.addEventListener("click", (e) => {
    // while an answer is on its way nothing in the card redraws, so nothing typed now can miss the send (review 2026-10-21)
    if (root.dataset.busy) return;
    const t = e.target;
    if (t.closest(".tog")) { collapsed = !collapsed; return draw(); }
    if (t.closest(".notet")) { noteOpen = !noteOpen; draw(); if (noteOpen) requestAnimationFrame(() => root.querySelector(".note")?.focus()); return; }
    if (t.closest(".x")) return send(true);
    if (t.closest(".back")) { idx--; return draw(); }
    if (t.closest(".skip")) { if (qs[idx]) delete sel[qs[idx]._id]; if (idx < qs.length - 1) { idx++; return draw(); } return send(false); }
    if (t.closest(".go")) { if (mode === "step" && idx < qs.length - 1) { idx++; return draw(); } return send(false); }
    if (t.closest("a") || t.closest("textarea") || t.closest(".mic")) return;
    const row = t.closest(".opt");
    if (!row) return;
    const q = qs.find((x) => x._id === row.dataset.q);
    if (row.hasAttribute("data-other-row")) return other(q);
    choose(q, q.options[+row.dataset.l].label);
  });
  root.addEventListener("input", (e) => {
    if (e.target.matches(".note")) { note = e.target.value; save(); return; }
    const id = e.target.dataset.other; if (!id) return;
    const q = qs.find((x) => x._id === id);
    const s = (sel[id] = sel[id] || { labels: [], other: true, otherText: "" });
    s.other = true; s.otherText = e.target.value; if (!q.multiSelect) s.labels = [];
    save();
    root.querySelector(".go")?.toggleAttribute("disabled", !(mode === "step" ? answerOf(qs[idx]).length : qs.some((x) => answerOf(x).length)));
  });
  // Digits pick options while focus is in the card (outside it, digits do nothing).
  root.tabIndex = -1;
  root.addEventListener("keydown", (e) => {
    if (root.dataset.busy) return;
    if (e.target.closest("textarea")) { if (e.key === "Enter" && !e.shiftKey && e.target.matches(".note")) { e.preventDefault(); root.querySelector(".go:not([disabled])")?.click(); } return; }
    const q = qs[idx];
    if (mode === "step" && /^\d$/.test(e.key)) {
      e.stopPropagation(); const n = +e.key - 1;
      if (n < q.options.length) choose(q, q.options[n].label); else if (n === q.options.length) other(q);
    }
    if (e.key === "Enter") { e.preventDefault(); root.querySelector(".go:not([disabled])")?.click(); }
  }, true);
  draw();
  return root;
}
