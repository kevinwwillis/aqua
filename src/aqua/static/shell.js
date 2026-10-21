// App shell for the iPad layout;
// differences are marked "live:". The question sits at the end of the context; Answer ↓ / ↑ Top jumps between them.
const store = { get(k, d) { try { const v = localStorage.getItem("aqua." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("aqua." + k, JSON.stringify(v)); } catch {} } };
const root = document.documentElement;
let zoom = store.get("zoom", 1);
const applyVars = () => root.style.setProperty("--z", zoom);
applyVars();

const COLORS = ["#7dd3fc", "#fbbf24", "#34d399", "#c4b5fd", "#fb7185", "#f472b6", "#a3e635"];
// a folder keeps its color as questions come and go
const projColor = (p) => { let h = 0; for (const ch of p) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return COLORS[h % COLORS.length]; };
const qn = (it) => `${it.questions.length} question${it.questions.length > 1 ? "s" : ""}`;
const projLeaf = (p) => { const parts = p.split(/[\\/]/).filter((x) => x && !/^[0-9a-f-]{36}$/.test(x) && x !== "scratchpad"); return parts.pop() || p; };

// Tables sit in a scroll box
function cmd(text) {
  const div = document.createElement("div");
  div.innerHTML = md(text, false);
  div.querySelectorAll("table").forEach((t) => { const w = document.createElement("div"); w.className = "tablewrap"; t.replaceWith(w); w.append(t); });
  return div.innerHTML;
}
const ctxHtml = (it) => it.context ? cmd(it.context) : `<p class="none">The agent sent no context before this question.</p>`;

// The list button sits at the right end, directly left of Answer ↓ / ↑ Top.
document.body.insertAdjacentHTML("afterbegin", `<div class="app">
  <div class="top"><div class="who"><b id="tt"></b><span id="ts"></span></div>
    <button class="tb" id="asb">Set aside</button>
    <button class="tb" id="listbtn" aria-label="Questions"></button>
    <button class="tb pri" id="jump">Answer ↓</button></div>
  <div class="scroll" id="scroll"><div class="ctx"><div class="cmd" id="ctx"></div></div>
    <section class="ans" id="ans"><div class="lbl">Your answer</div><div id="slot"></div></section></div></div>
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" aria-label="Questions">
  <div class="dh"><h2>aqua<span class="n" id="n"></span></h2>
    <button class="tb" id="gear" aria-label="Settings" aria-expanded="false">⚙</button>
    <button class="tb" id="close" aria-label="Close list">✕</button></div>
  <div class="tabs"><button data-tab="waiting">Waiting</button><button data-tab="aside">Set aside</button></div>
  <div class="list" id="list"></div>
</aside>`);

let tab = "waiting";
const $ = (id) => document.getElementById(id);
function countBtn() {
  const w = visible().length, a = asideList().length;
  $("listbtn").innerHTML = `<span>☰</span><span class="cnt">${w}</span>${a ? `<span class="cnt aside" title="set aside">${a}</span>` : ""}`;
}
function drawList() {
  const id = selectedId(), list = tab === "waiting" ? visible() : asideList();
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.classList.toggle("on", b.dataset.tab === tab);
    b.textContent = b.dataset.tab === "waiting" ? `Waiting (${visible().length})` : `Set aside (${asideList().length})`;
  });
  // live: questions that left the inbox while holding unsent text stay listed
  const gone = tab === "waiting" ? orphans().map((o) => `<div class="cell orphan${id === "gone:" + o.id ? " sel" : ""}" data-id="gone:${esc(o.id)}">
    <div class="t">${esc(o.title || "Question")}</div><div class="age"></div><div class="s">${esc(o.sessionName || "")}</div>
    <div class="c">unsent text</div><div class="p">Left the inbox</div></div>`).join("") : "";
  $("list").innerHTML = gone + (list.length ? list.map((it) => `
    <div class="cell${it.id === id ? " sel" : ""}" data-id="${it.id}" style="--pc:${projColor(it.project)}">
      <div class="t">${esc(it.title)}</div><div class="age">${ago(it.ts)}</div>
      <div class="s">${esc(it.sessionName)}</div><div class="c">${qn(it)}</div>
      ${tab === "aside" ? `<button class="back" data-back="${it.id}">Move back to Waiting</button>` : ""}</div>`).join("")
    : gone ? "" : `<div class="empty">${tab === "waiting" ? "Nothing waiting." : "Nothing set aside."}</div>`);
}
const openList = (on) => { root.classList.toggle("open-list", on); if (on) drawList(); };
$("listbtn").onclick = () => openList(!root.classList.contains("open-list"));
$("scrim").onclick = () => openList(false);
$("close").onclick = () => openList(false);
$("drawer").addEventListener("click", (e) => {
  const t = e.target;
  if (t.dataset.tab) { tab = t.dataset.tab; return drawList(); }
  if (t.dataset.back) { e.stopPropagation(); openList(false); return setAside(t.dataset.back, false); }
  const c = t.closest(".cell"); if (c) { openList(false); go(c.dataset.id); }
});
function go(id) { if (decodeURIComponent(location.hash.slice(1)) === (id || "")) render(); else select(id); }

// set aside: the question stays waiting for the agent; it only leaves the main list
async function setAside(id, aside) {
  const next = aside ? nextAfter(id) : id;
  const r = await fetch(`api/questions/${id}/aside`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ aside }) }).catch(() => null);
  if (!r || !r.ok) { toast(`Not changed: ${r ? (r.status === 409 ? "the question already left the inbox" : `server said ${r.status}`) : "the server could not be reached"}.`); return; }
  const q = await r.json(), it = ITEMS.find((x) => x.id === id);
  if (it) it.set_aside_at = q.set_aside_at;
  toast(aside ? "Set aside. It is in the Set aside list." : "Back in Waiting.");
  shownId = null; go(next);
}
$("asb").onclick = () => { const it = ITEMS.find((x) => x.id === shownId); if (it) setAside(it.id, !it.set_aside_at); };

function who(it) {
  $("tt").textContent = it ? it.title : "Nothing waiting";
  $("ts").textContent = it ? `${it.sessionName} · ${projLeaf(it.project)} · asked ${ago(it.ts)} ago${it.set_aside_at ? " · set aside" : ""}` : "";
  $("asb").hidden = !it; $("asb").textContent = it && it.set_aside_at ? "Move back to Waiting" : "Set aside";
}
let shownId = null;
function drawMain() {
  const sid = selectedId();
  $("scroll").scrollTop = 0;
  const slot = $("slot"); slot.innerHTML = "";
  if (sid && sid.startsWith("gone:")) { shownId = sid; return drawGone(sid.slice(5)); }
  const it = ITEMS.find((i) => i.id === sid);
  shownId = it ? it.id : null;
  who(it);
  $("ctx").innerHTML = it ? ctxHtml(it) : `<p class="none">Nothing waiting.</p>`;
  $("ans").hidden = !it; $("jump").hidden = !it;
  if (it) slot.append(questionCard(it, { mode: "step", big: true, onSent: (next) => { shownId = null; go(next); } }));
  jumpLabel();
}
// live: redraw the page only when a different question is selected, so polling never wipes an answer in progress
// live: if the question on screen leaves the inbox while the user has unsent text in it, keep it on screen as a
// "left the inbox" notice at its own address (#gone:<id>), so later refreshes cannot dismiss it
function render() {
  if (shownId && !shownId.startsWith("gone:") && !visible().concat(asideList()).some((i) => i.id === shownId) && draftText(shownId)) {
    history.replaceState(null, "", "#gone:" + shownId);
  }
  countBtn();
  $("n").textContent = online ? "" : "offline — retrying";
  if (root.classList.contains("open-list")) drawList();
  if (selectedId() !== shownId) drawMain();
  else { const it = ITEMS.find((i) => i.id === shownId); if (it) who(it); }
}
async function drawGone(id) {
  const d = loadDraft(id) || {}, meta = d.meta || {};
  $("tt").textContent = meta.title || "This question"; $("ts").textContent = "left the inbox"; $("asb").hidden = true;
  $("ctx").innerHTML = `<p class="none">Loading…</p>`; $("ans").hidden = false; $("jump").hidden = false;
  $("slot").innerHTML = `<div class="gone"><b>${esc(meta.title || "This question")} left the inbox</b>
    <span class="why">Nothing was sent. Your unsent text is kept here until you discard it.</span>
    <textarea readonly rows="5">${esc(draftText(id))}</textarea>
    <button class="next">Discard this text</button></div>`;
  $("slot").querySelector(".next").onclick = () => { clearDraft(id); history.replaceState(null, "", "#"); shownId = null; render(); };
  const r = await fetch(`api/questions/${id}`).catch(() => null);
  const q = r && r.ok ? await r.json() : null;
  if (shownId !== "gone:" + id) return;
  $("ctx").innerHTML = q ? ctxHtml(q) : `<p class="none">The question could not be loaded.</p>`;
  if (q) $("slot").querySelector(".why").textContent = (q.status === "withdrawn" ? "Its agent withdrew it." : "It was answered somewhere else.") + " Nothing was sent from here. Your unsent text is kept here until you discard it.";
}
addEventListener("hashchange", render);

// Answer ↓ jumps to the question; at the question it reads ↑ Top
// live: decided by where the question starts, not how much of it shows, so a card taller than the screen still flips
let atAns = false;
function jumpLabel() {
  const sc = $("scroll"), top = $("ans").getBoundingClientRect().top - sc.getBoundingClientRect().top;
  atAns = !$("ans").hidden && top < sc.clientHeight * 0.6;
  $("jump").textContent = atAns ? "↑ Top" : "Answer ↓";
}
$("scroll").addEventListener("scroll", jumpLabel, { passive: true });
addEventListener("resize", jumpLabel);
$("jump").onclick = () => atAns ? $("scroll").scrollTo({ top: 0, behavior: "smooth" }) : $("ans").scrollIntoView({ behavior: "smooth", block: "start" });

// image viewer: PhotoSwipe; tap outside the image, the ✕, or a swipe right from the left edge closes it
let pswp = null;
document.addEventListener("click", async (e) => {
  // live: images in the question and its options open too, not only those in the context
  const img = e.target.closest(".cmd img, .qc .md img"); if (!img || !img.naturalWidth) return;
  e.preventDefault();
  const { default: PhotoSwipe } = await import("./vendor/photoswipe.esm.min.js");
  pswp = new PhotoSwipe({ dataSource: [{ src: img.src, width: img.naturalWidth, height: img.naturalHeight, alt: img.alt }],
    bgOpacity: 0.96, showHideAnimationType: "zoom", wheelToZoom: true, initialZoomLevel: "fit", secondaryZoomLevel: 2, maxZoomLevel: 6,
    tapAction: (pt, ev) => { if (!ev.target.closest(".pswp__img")) pswp.close(); }, bgClickAction: "close", imageClickAction: "zoom-or-close" });
  pswp.on("uiRegister", () => pswp.ui.registerElement({ name: "edgehint", appendTo: "root", onInit: () => {} }));
  pswp.on("destroy", () => (pswp = null));
  pswp.init();
});
// live: the left-edge swipe only closes an open image (the list now opens from the right)
let edge = null;
const edgeStart = (x, y) => { if (pswp && x < 28) edge = { x, y }; };
const edgeEnd = (x, y) => { if (!edge) return; const dx = x - edge.x, dy = Math.abs(y - edge.y); edge = null; if (dx > 70 && dy < 60 && pswp) pswp.close(); };
addEventListener("touchstart", (e) => { const t = e.touches[0]; if (t) edgeStart(t.clientX, t.clientY); }, { capture: true, passive: true });
addEventListener("touchend", (e) => { const t = e.changedTouches[0]; if (t) edgeEnd(t.clientX, t.clientY); }, { capture: true, passive: true });
addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") edgeStart(e.clientX, e.clientY); }, true);
addEventListener("pointerup", (e) => edgeEnd(e.clientX, e.clientY), true);

// settings: a fixed layer outside the scaled .app, so it stays still while the page changes size
const gear = $("gear");
const closeSettings = () => { $("settings")?.remove(); gear.setAttribute("aria-expanded", "false"); };
gear.addEventListener("click", () => {
  if ($("settings")) return closeSettings();
  gear.setAttribute("aria-expanded", "true");
  document.body.insertAdjacentHTML("beforeend", `<div class="settings" id="settings" role="dialog" aria-label="Settings">
    <div class="top2"><h3>Text size</h3><span class="val"></span></div>
    <div class="fs"><button class="small" data-d="-0.05" aria-label="Smaller">A</button>
      <input type="range" min="0.7" max="1.6" step="0.05" aria-label="Text size">
      <button class="large" data-d="0.05" aria-label="Larger">A</button></div>
    <div class="ticks"><span>70%</span><span>100%</span><span>160%</span></div>
    <div class="row2"><button class="reset">Reset to 100%</button><button class="done">Done</button></div></div>`);
  const p = $("settings"), r = p.querySelector("input");
  const show = () => { r.value = zoom; r.style.setProperty("--pct", ((zoom - 0.7) / 0.9) * 100 + "%"); p.querySelector(".val").textContent = Math.round(zoom * 100) + "%"; };
  const set = (z) => { zoom = Math.round(Math.min(1.6, Math.max(0.7, z)) * 100) / 100; store.set("zoom", zoom); applyVars(); show(); };
  show();
  r.oninput = () => set(+r.value);
  p.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => set(zoom + +b.dataset.d)));
  p.querySelector(".reset").onclick = () => set(1);
  p.querySelector(".done").onclick = closeSettings;
});
document.addEventListener("pointerdown", (e) => { if (!e.target.closest("#settings, #gear")) closeSettings(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") { closeSettings(); openList(false); } });

// live: load waiting questions, then long-poll the server for changes
let version = -1, online = true;
// live: a new release reloads the page by itself, once the user is not typing; drafts are kept in localStorage anyway
let seenBuild = null, newBuild = false;
// the first build seen is the one this page was loaded with: load() reports it before any poll
function noteBuild(b) {
  if (b && seenBuild && b !== seenBuild) newBuild = true;
  seenBuild = seenBuild || b;
  if (newBuild) reloadWhenIdle();
}
function reloadWhenIdle() { if (!document.activeElement?.closest("textarea, input")) location.reload(); }
addEventListener("focusout", () => { if (newBuild) setTimeout(reloadWhenIdle, 1500); });
const fromServer = (q) => ({ ...q, ts: new Date(q.created_at * 1000).toISOString(), sessionName: q.session_name });
async function load() {
  const r = await fetch("api/questions?status=waiting");
  if (!r.ok) throw new Error("server said " + r.status);
  const d = await r.json();
  noteBuild(d.build);
  ITEMS = d.items.map(fromServer); version = d.version;
  for (const id of [...state.answered.keys()]) if (!ITEMS.some((i) => i.id === id)) state.answered.delete(id);
  render();
}
async function watch() {
  for (;;) {
    try {
      if (version < 0 || !online) { await load(); online = true; }
      const r = await fetch(`api/changes?since=${version}&wait=25`);
      if (!r.ok) throw new Error("server said " + r.status);
      const c = await r.json();
      noteBuild(c.build);
      if (c.version !== version) await load();
    } catch (e) {
      online = false;
      $("n").textContent = "offline — retrying";
      await new Promise((ok) => setTimeout(ok, 5000));
    }
  }
}
setInterval(() => { countBtn(); if (root.classList.contains("open-list")) drawList(); }, 60000); // keep "time since asked" current
// Servers with an access token: open the page once as <address>/#token=<token>. The fragment never reaches the server
// or its logs; the page trades it for a cookie and drops it from the address.
async function tokenLogin() {
  if (!location.hash.startsWith("#token=")) return;
  const token = decodeURIComponent(location.hash.slice(7));
  history.replaceState(null, "", location.pathname + location.search);
  await fetch("api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
}
tokenLogin().finally(watch);
