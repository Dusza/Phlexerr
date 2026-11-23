"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DAY = 86400;
const PAGE = 500;

const S = {
  cfg: { dry_run: true }, items: [], byId: new Map(), view: [], sel: new Set(),
  sort: { key: "added_ts", dir: "desc" }, shown: PAGE, lastIdx: null, generated: null,
};

async function api(path, opts = {}) {
  const headers = { "X-Requested-With": "phlexerr", ...(opts.body ? { "Content-Type": "application/json" } : {}) };
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers });
  if (r.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

// ---------- formatting ----------
function fmtSize(b) {
  if (!b) return "0 GB";
  const gb = b / 1073741824;
  if (gb >= 1024) return (gb / 1024).toFixed(2) + " TB";
  if (gb >= 10) return gb.toFixed(0) + " GB";
  if (gb >= 0.1) return gb.toFixed(1) + " GB";
  return (b / 1048576).toFixed(0) + " MB";
}
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("en-CA") : "");
function ago(ts) {
  if (!ts) return "";
  const d = Math.floor((Date.now() / 1000 - ts) / DAY);
  if (d < 1) return "today";
  if (d < 60) return d + "d ago";
  if (d < 730) return Math.round(d / 30.4) + "mo ago";
  return (d / 365).toFixed(1) + "y ago";
}

// ---------- filtering / sorting ----------
const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };

const ANIM_ICON = "🎨";
const isAnim = (i) => (i.genres || []).some((g) => String(g).toLowerCase() === "animation");
function applyFilters() {
  const q = $("#fQ").value.trim().toLowerCase();
  const type = $("#fType").value, lib = $("#fLib").value, user = $("#fUser").value, coll = $("#fColl").value;
  const anim = $("#fAnim").value, stale = num("#fStale"), age = num("#fAge"), recent = num("#fNew"), plays = num("#fPlays"), pct = num("#fPct");
  const before = num("#fYear"), rating = num("#fRating"), size = num("#fSize"), unrated = $("#fUnrated").value, watched = $("#fWatched").value;
  const hideKeep = $("#fHideKeep").checked, leave = $("#fLeave").value;
  const now = Date.now() / 1000;

  S.view = S.items.filter((i) => {
    if (hideKeep && i.protected.length) return false;
    if (q && !(`${i.title} ${i.year ?? ""}`.toLowerCase().includes(q))) return false;
    if (type && i.type !== type) return false;
    if (anim && isAnim(i) !== (anim === "only")) return false;
    if (leave === "marked" && !i.leaving_due) return false;
    if (leave === "due" && !(i.leaving_due && i.leaving_due < now)) return false;
    if (leave === "not" && i.leaving_due) return false;
    if (lib && i.library !== lib) return false;
    if (coll === "__any") { if (!i.collections.length) return false; }
    else if (coll === "__none") { if (i.collections.length) return false; }
    else if (coll && !i.collections.includes(coll)) return false;
    if (user === "__none") { if (i.requested_by.length) return false; }
    else if (user && !i.requested_by.includes(user)) return false;
    if (stale !== null && i.last_viewed_ts && i.last_viewed_ts > now - stale * DAY) return false;
    if (age !== null && (!i.added_ts || i.added_ts > now - age * DAY)) return false;
    if (recent !== null && (!i.added_ts || i.added_ts < now - recent * DAY)) return false;   // added within the last N days
    if (plays !== null && i.plays_1y !== null && i.plays_1y > plays) return false;
    if (pct !== null && i.pct > pct) return false;
    // Unrated items (no IMDb rating) never match a numeric rating filter; use the "Unrated" filter to find or hide them.
    if (unrated === "only") { if (i.rating !== null) return false; }
    else {
      if (unrated === "hide" && i.rating === null) return false;
      if (rating !== null && (i.rating === null || i.rating > rating)) return false;
    }
    const never = !i.last_viewed_ts && !i.plays_total && !i.pct;   // nobody has ever played any of it
    if (watched === "never" && !never) return false;
    if (watched === "some" && never) return false;
    if (before !== null && (!i.year || i.year >= before)) return false;     // strictly before; items with no year never match
    if (size !== null && i.size < size * 1073741824) return false;
    return true;
  });
  sortView();
  S.shown = PAGE;
  render();
}

function sortVal(i, k) {
  if (k === "title") return (i.title || "").toLowerCase();
  if (k === "requested") return i.requested_by.join(", ").toLowerCase();
  if (k === "leaving_due") return i.leaving_due || null;
  if (k === "last_viewed_ts") return i.last_viewed_ts || 0; // never viewed = oldest
  return i[k];
}
function sortView() {
  const { key, dir } = S.sort, m = dir === "asc" ? 1 : -1;
  S.view.sort((a, b) => {
    const x = sortVal(a, key), y = sortVal(b, key);
    const xn = x === null || x === undefined || x === "", yn = y === null || y === undefined || y === "";
    if (xn && yn) return 0;
    if (xn) return 1;            // empty values always last
    if (yn) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * m;
  });
}

// ---------- Plex links ----------
function plexUrl(i) {
  if (!S.machineId) return null;
  const base = (S.cfg.plex_link_base || "https://app.plex.tv/desktop").replace(/\/+$/, "");
  return `${base}/#!/server/${encodeURIComponent(S.machineId)}/details?key=${encodeURIComponent("/library/metadata/" + i.plex_key)}`;
}
function plexLink(i, inner, cls) {
  const u = plexUrl(i);
  return u ? `<a class="${cls}" href="${esc(u)}" target="_blank" rel="noopener noreferrer" title="Open in Plex">${inner}</a>` : inner;
}


// On phones the tables turn into stacked cards (see .stackable in the CSS). The cards get their labels from the column headers.
function labelCells(tbl) {
  if (!tbl || !tbl.tHead || !tbl.tBodies[0]) return;
  const hs = [...tbl.tHead.rows[0].cells].map((th) => ({
    text: th.hasAttribute("data-nolabel") ? "" : th.textContent.replace(/[▲▼]/g, "").trim(),
    wide: th.hasAttribute("data-wide"), cb: th.hasAttribute("data-cbcol"),
  }));
  const hasCb = hs.some((h) => h.cb);
  tbl.tBodies[0].querySelectorAll(":scope > tr").forEach((tr) => {
    if (tr.cells.length !== hs.length) return;
    let first = true;
    [...tr.cells].forEach((td, i) => {
      const h = hs[i];
      if (h.text) td.dataset.l = h.text;
      td.classList.toggle("wide", h.wide); td.classList.toggle("cbcol", h.cb);
      td.classList.toggle("pr", hasCb && h.wide && first && !h.cb);
      if (h.wide && !h.cb) first = false;
    });
  });
}

// ---------- rendering ----------
// Rule-protected items can't be selected at all; manually "Kept" ones can (so they can be un-kept) but are never deleted.
const canSelect = (i) => !i.protected.length || i.keep;

function rowHtml(i) {
  const sel = S.sel.has(i.id);
  const imdb = i.rating !== null
    ? (i.imdb_id ? `<a class="imdb" target="_blank" rel="noopener noreferrer" href="https://www.imdb.com/title/${esc(i.imdb_id)}/" title="Source: ${esc(i.rating_src)}">${i.rating.toFixed(1)}</a>` : `<span title="Source: ${esc(i.rating_src)}">${i.rating.toFixed(1)}</span>`)
    : `<span class="dim">–</span>`;
  const badges = [
    `<span class="badge">${i.type === "movie" ? "Movie" : "Show"}</span>`,
    isAnim(i) ? `<span class="badge anim" title="Animation" aria-label="Animation">${ANIM_ICON}</span>` : "",
    i.leaving_due ? `<span class="badge leave${i.leaving_due < Date.now() / 1000 ? " due" : ""}" title="${i.leaving_due < Date.now() / 1000 ? "Past its date. Nothing is deleted automatically: delete it yourself when you're ready." : "Shown in the Leaving Soon row in Plex until then"}">⏳ ${i.leaving_due < Date.now() / 1000 ? "Past due" : "Leaving " + fmtDate(i.leaving_due)}</span>` : "",
    i.protected.length ? `<span class="badge keep" title="${esc("Protected from deletion:\n" + i.protected.join("\n"))}">🔒 ${i.keep && i.protected.length === 1 ? "Keep" : "Protected"}</span>` : "",
    !i.arr && S.cfg.services[i.type === "movie" ? "radarr" : "sonarr"] ? `<span class="badge warn" title="Not found in ${i.type === "movie" ? "Radarr" : "Sonarr"} - can only be deleted via Plex">no ${i.type === "movie" ? "Radarr" : "Sonarr"}</span>` : "",
  ].join("");
  const sub = [esc(i.library), i.episodes ? `${i.episodes} eps` : ""].filter(Boolean).join(" · ");
  const collBadges = i.collections.slice(0, 2).map((c) => `<span class="badge coll" title="Part of the collection “${esc(c)}”">${esc(c)}</span>`).join("")
    + (i.collections.length > 2 ? `<span class="badge coll" title="${esc(i.collections.slice(2).join(", "))}">+${i.collections.length - 2}</span>` : "");
  const req = i.requested_by.length ? esc(i.requested_by.join(", ")) : `<span class="dim">–</span>`;
  const last = i.last_viewed_ts
    ? `<span title="${esc(i.last_viewed_by ? "by " + i.last_viewed_by : "")}">${fmtDate(i.last_viewed_ts)}</span> <span class="dim">${ago(i.last_viewed_ts)}</span>`
    : `<span class="dim">Never</span>`;
  const plays1y = i.plays_1y === null ? `<span class="dim" title="Needs Tautulli">?</span>` : `<span title="${i.plays_total} total">${i.plays_1y}</span>`;
  return `<tr data-id="${esc(i.id)}" class="${sel ? "sel" : ""}${i.protected.length ? " kept" : ""}">
    <td class="cb"><input type="checkbox" class="rowchk" ${sel ? "checked" : ""} ${canSelect(i) ? "" : "disabled"}></td>
    <td class="title-cell"><div class="tc">${plexLink(i, `<img class="poster" loading="lazy" alt="" src="/api/poster/${esc(i.plex_key)}">`, "plink")}<div><div class="t">${plexLink(i, esc(i.title), "plink")} ${i.year ? `<span class="dim">(${i.year})</span>` : ""} <button type="button" class="info" aria-label="Show description" data-key="${esc(i.plex_key)}">i</button></div><div class="sub">${badges}<span>${sub}</span></div>${collBadges ? `<div class="sub">${collBadges}</div>` : ""}</div></div></td>
    <td class="num mono" data-l="IMDb">${imdb}</td>
    <td class="mono" data-l="Added">${fmtDate(i.added_ts)} <span class="dim">${ago(i.added_ts)}</span></td>
    <td class="mono" data-l="Last viewed">${last}</td>
    <td class="num" data-l="% viewed"><span class="pct"><span class="bar"><i style="width:${Math.min(100, i.pct)}%"></i></span><span class="mono">${Math.round(i.pct)}%</span></span></td>
    <td data-l="Requested by" class="needs-seerr">${req}</td>
    <td class="num mono" data-l="Plays (1y)">${plays1y}</td>
    <td class="num mono" data-l="Size">${fmtSize(i.size)}</td></tr>`;
}

const V = { mode: "cards" };
try { const m = localStorage.getItem("mp-view"); if (m === "table" || m === "cards") V.mode = m; } catch (e) {}
function applyViewMode() {
  const cards = V.mode === "cards";
  $("#tbl").classList.toggle("cardview", cards);
  $("#candWrap").classList.toggle("cardwrap", cards);
  document.body.classList.toggle("cardmode", cards);
  $("#vCards").setAttribute("aria-pressed", String(cards)); $("#vTable").setAttribute("aria-pressed", String(!cards));
  $("#vCards").classList.toggle("active", cards); $("#vTable").classList.toggle("active", !cards);
}
function setViewMode(m) {
  V.mode = m;
  try { localStorage.setItem("mp-view", m); } catch (e) {}
  applyViewMode();
}
function render() {
  const rows = S.view.slice(0, S.shown).map(rowHtml).join("");
  const more = S.view.length > S.shown
    ? `<tr><td colspan="9" class="more"><button class="btn" id="btnMore">Show ${Math.min(PAGE, S.view.length - S.shown)} more (${S.view.length - S.shown} hidden)</button></td></tr>` : "";
  $("#rows").innerHTML = rows + more;
  const empty = $("#empty");
  empty.hidden = S.view.length > 0;
  empty.textContent = S.items.length ? "Nothing matches these filters." : 'No data yet - click “Refresh data” to build the report.';
  $("#sCount").textContent = S.view.length.toLocaleString();
  $("#sSize").textContent = fmtSize(S.view.reduce((a, i) => a + i.size, 0));
  document.querySelectorAll("#tbl th.sortable").forEach((th) => {
    const isActive = th.dataset.sort === S.sort.key;
    th.querySelector(".arrow").textContent = isActive ? (S.sort.dir === "asc" ? "▲" : "▼") : "";
    th.setAttribute("aria-sort", isActive ? (S.sort.dir === "asc" ? "ascending" : "descending") : "none");
  });
  $("#mSort").value = `${S.sort.key}|${S.sort.dir}`;
  const allSel = S.view.length > 0 && S.view.filter(canSelect).every((i) => S.sel.has(i.id));
  $("#chkAll").checked = allSel; $("#chkAll2").checked = allSel;
  updateSelBar();
}

function updateSelBar() {
  // drop selections that no longer exist
  for (const id of [...S.sel]) if (!S.byId.has(id)) S.sel.delete(id);
  const n = S.sel.size;
  $("#selbar").hidden = n === 0;
  $("#selCount").textContent = n.toLocaleString();
  $("#selSize").textContent = fmtSize([...S.sel].reduce((a, id) => a + (S.byId.get(id)?.size || 0), 0));
  const marked = [...S.sel].filter((id) => S.byId.get(id)?.leaving_due).length;
  $("#btnUnleave").hidden = !marked;                   // only offered when something selected is already leaving soon
  $("#btnLeave").textContent = marked && marked < n ? "Leaving soon (rest)" : "Leaving soon";
}

// ---------- info popover (description) ----------
const pop = document.createElement("div");
pop.id = "infoPop"; pop.hidden = true;
document.documentElement.appendChild(pop);      // outside <body> so the text-size zoom doesn't skew positioning
const infoCache = new Map();
let popBtn = null, popPinned = false, showTimer = null, hideTimer = null;

async function getInfo(key) {
  if (!infoCache.has(key)) infoCache.set(key, await api("/api/info/" + encodeURIComponent(key)));
  return infoCache.get(key);
}
function placePop(btn) {
  const r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  let x = r.right + 10;
  if (x + w > innerWidth - 8) x = Math.max(8, r.left - w - 10);
  const y = Math.max(8, Math.min(r.top - 8, innerHeight - h - 8));
  pop.style.left = x + "px"; pop.style.top = y + "px";
}
function infoHtml(i, d) {
  const meta = [d.content_rating, d.runtime_min ? (d.runtime_min >= 60 ? `${Math.floor(d.runtime_min / 60)}h ${d.runtime_min % 60}m` : `${d.runtime_min}m`) : "",
    d.seasons ? `${d.seasons} season${d.seasons === 1 ? "" : "s"}` : "", d.episodes ? `${d.episodes} eps` : "", d.studio].filter(Boolean).map(esc).join(" · ");
  const u = plexUrl(i);
  const imdb = /^tt\d+$/.test(i.imdb_id || "") ? `https://www.imdb.com/title/${i.imdb_id}/`
    : `https://www.imdb.com/find/?s=tt&q=${encodeURIComponent(`${i.title} ${i.year || ""}`.trim())}`;
  return `<div class="ph"><b>${esc(d.title || i.title)}</b> ${d.year ? `<span class="dim">(${d.year})</span>` : ""}</div>
    ${meta ? `<div class="meta">${meta}</div>` : ""}
    ${d.genres.length ? `<div class="chips">${d.genres.map((g) => `<span class="badge">${esc(g)}</span>`).join("")}</div>` : ""}
    ${d.tagline ? `<div class="tag">${esc(d.tagline)}</div>` : ""}
    <p class="sum">${d.summary ? esc(d.summary) : `<span class="dim">No description available.</span>`}</p>
    ${d.directors.length ? `<div class="crew"><span class="dim">Director</span> ${esc(d.directors.join(", "))}</div>` : ""}
    ${d.cast.length ? `<div class="crew"><span class="dim">Cast</span> ${esc(d.cast.join(", "))}</div>` : ""}
    ${u ? `<a class="btn small" href="${esc(u)}" target="_blank" rel="noopener noreferrer">Open in Plex ↗</a>` : ""}
    <a class="btn small imdbBtn" href="${esc(imdb)}" target="_blank" rel="noopener noreferrer">IMDb ↗</a>`;
}
async function showInfo(btn, pin) {
  clearTimeout(hideTimer);
  const i = S.byId.get(btn.closest("tr").dataset.id);
  if (!i) return;
  popBtn = btn; popPinned = pin;
  pop.classList.toggle("pinned", pin);
  pop.hidden = false; pop.innerHTML = `<span class="dim">Loading…</span>`; placePop(btn);
  try {
    const d = await getInfo(i.plex_key);
    if (popBtn !== btn) return;
    pop.innerHTML = infoHtml(i, d); placePop(btn);
  } catch (e) { if (popBtn === btn) pop.innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
}
function hideInfo(force) {
  if (popPinned && !force) return;
  pop.hidden = true; popBtn = null; popPinned = false;
}
const rowsEl = $("#rows");
rowsEl.addEventListener("mouseover", (e) => {
  const b = e.target.closest(".info");
  if (!b || popPinned) return;
  clearTimeout(hideTimer); clearTimeout(showTimer);
  showTimer = setTimeout(() => showInfo(b, false), 220);
});
rowsEl.addEventListener("mouseout", (e) => {
  if (!e.target.closest(".info")) return;
  clearTimeout(showTimer);
  hideTimer = setTimeout(() => hideInfo(false), 250);
});
pop.addEventListener("mouseenter", () => clearTimeout(hideTimer));
pop.addEventListener("mouseleave", () => { hideTimer = setTimeout(() => hideInfo(false), 250); });
rowsEl.addEventListener("click", (e) => {
  const b = e.target.closest(".info");
  if (!b) return;
  e.preventDefault();
  clearTimeout(showTimer);
  if (popPinned && popBtn === b) hideInfo(true); else showInfo(b, true);   // click pins it open (also how touch screens use it)
});
document.addEventListener("click", (e) => { if (!pop.hidden && !e.target.closest("#infoPop") && !e.target.closest(".info")) hideInfo(true); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideInfo(true); });
window.addEventListener("scroll", () => hideInfo(false), { passive: true });

// ---------- selection ----------
$("#rows").addEventListener("click", (e) => {
  if (e.target.id === "btnMore") { S.shown += PAGE; render(); return; }
  if (e.target.closest("a") || e.target.closest(".info")) return;  // links open Plex; the (i) button has its own handler
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = tr.dataset.id;
  const idx = S.view.findIndex((i) => i.id === id);
  if (!canSelect(S.view[idx])) return;
  const turnOn = e.target.classList.contains("rowchk") ? e.target.checked : !S.sel.has(id);
  if (e.shiftKey && S.lastIdx !== null) {
    const [a, b] = [Math.min(S.lastIdx, idx), Math.max(S.lastIdx, idx)];
    for (let k = a; k <= b; k++) if (canSelect(S.view[k])) (turnOn ? S.sel.add(S.view[k].id) : S.sel.delete(S.view[k].id));
  } else {
    turnOn ? S.sel.add(id) : S.sel.delete(id);
  }
  S.lastIdx = idx;
  render();
});
$("#chkAll").addEventListener("change", (e) => {
  for (const i of S.view) if (canSelect(i)) e.target.checked ? S.sel.add(i.id) : S.sel.delete(i.id);
  render();
});
$("#chkAll2").addEventListener("change", (e) => {
  for (const i of S.view) if (canSelect(i)) e.target.checked ? S.sel.add(i.id) : S.sel.delete(i.id);
  render();
});
$("#vCards").addEventListener("click", () => setViewMode("cards"));
$("#vTable").addEventListener("click", () => setViewMode("table"));
applyViewMode();
$("#btnClearSel").addEventListener("click", () => { S.sel.clear(); render(); });

// First click sorts the "most likely to delete" end first: smallest rating/%/plays, oldest dates, biggest files.
function handleSort(th) {
  const k = th.dataset.sort;
  const firstDir = k === "size" ? "desc" : "asc";
  S.sort = S.sort.key === k ? { key: k, dir: S.sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: firstDir };
  sortView(); render(); saveFiltersToStorage();
}
document.querySelectorAll("#tbl th.sortable").forEach((th) => {
  th.addEventListener("click", () => handleSort(th));
  th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleSort(th); } });
});

// ---------- filters ----------
const FILTER_IDS = ["#fQ", "#fType", "#fLib", "#fColl", "#fUser", "#fStale", "#fAge", "#fNew", "#fPlays", "#fPct", "#fRating", "#fUnrated", "#fWatched", "#fAnim", "#fLeave", "#fYear", "#fSize", "#fHideKeep"];
const FILTER_LABELS = {
  fQ: "Search", fType: "Type", fLib: "Library", fColl: "Collection", fUser: "Requested by",
  fStale: "Not watched in", fAge: "Added over", fNew: "Added within", fPlays: "Plays ≤",
  fPct: "Viewed % ≤", fRating: "IMDb ≤", fUnrated: "Unrated", fWatched: "Watched",
  fAnim: "Animation", fLeave: "Leaving soon", fYear: "Before year", fSize: "Size ≥"
};

// Advanced filters drawer toggle
const btnAdvanced = $("#btnAdvanced");
const filtersSection = $("#filters");
if (btnAdvanced && filtersSection) {
  btnAdvanced.addEventListener("click", () => {
    const open = btnAdvanced.getAttribute("aria-expanded") !== "true";
    btnAdvanced.setAttribute("aria-expanded", String(open));
    filtersSection.hidden = !open;
    const bf = $("#btnFilters"); bf.setAttribute("aria-expanded", String(open)); bf.textContent = open ? "Hide filters" : "Filters";
  });
}

// Filter pills rendering
function renderFilterPills() {
  const pills = $("#filterPills");
  if (!pills) return;
  const active = [];
  FILTER_IDS.forEach((id) => {
    const el = $(id);
    if (!el) return;
    const key = id.slice(1);
    if (key === "fHideKeep") return;
    const val = el.type === "checkbox" ? el.checked : el.value;
    if (val && val !== "" && val !== true) {
      let label = FILTER_LABELS[key] || key;
      let display = val;
      if (el.tagName === "SELECT") {
        const opt = el.options[el.selectedIndex];
        display = opt ? opt.text : val;
      }
      if (key === "fStale" || key === "fAge" || key === "fNew") display += " days";
      if (key === "fSize") display += " GB";
      active.push({ key, label, display });
    }
  });
  const html = active.map((f) => `<span class="filter-pill" data-filter="${f.key}">${esc(f.label)}: <b>${esc(f.display)}</b><button type="button" aria-label="Remove filter">×</button></span>`).join("");
  if (pills.innerHTML !== html) pills.innerHTML = html;   // re-rendering identical pills on blur would swallow the click on a pill
}
$("#filterPills")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const pill = btn.closest(".filter-pill");
  const key = pill?.dataset.filter;
  if (key) {
    const el = $("#" + key);
    if (el) { el.value = ""; applyFilters(); saveFiltersToStorage(); renderFilterPills(); }
  }
});

// Save/load filters to localStorage
function saveFiltersToStorage() {
  try {
    const state = {};
    FILTER_IDS.forEach((id) => {
      const el = $(id);
      if (el) state[id.slice(1)] = el.type === "checkbox" ? el.checked : el.value;
    });
    state.sort = S.sort;
    localStorage.setItem("mp-filters", JSON.stringify(state));
  } catch (e) { /* ignore */ }
}
function loadFiltersFromStorage() {
  try {
    const saved = localStorage.getItem("mp-filters");
    if (!saved) return false;
    const state = JSON.parse(saved);
    FILTER_IDS.forEach((id) => {
      const el = $(id);
      const key = id.slice(1);
      if (el && state[key] !== undefined) {
        if (el.type === "checkbox") el.checked = state[key];
        else el.value = state[key];
      }
    });
    if (state.sort) S.sort = state.sort;
    return true;
  } catch (e) { return false; }
}

FILTER_IDS.forEach((id) => {
  $(id)?.addEventListener("input", () => { applyFilters(); saveFiltersToStorage(); renderFilterPills(); });
  $(id)?.addEventListener("change", () => { applyFilters(); saveFiltersToStorage(); renderFilterPills(); });
});
function resetFilters(keepHide = true) {
  ["#fQ", "#fType", "#fLib", "#fColl", "#fUser", "#fStale", "#fAge", "#fNew", "#fPlays", "#fPct", "#fRating", "#fUnrated", "#fWatched", "#fAnim", "#fLeave", "#fYear", "#fSize"].forEach((id) => { const el = $(id); if (el) el.value = ""; });
  const hideKeep = $("#fHideKeep"); if (hideKeep) hideKeep.checked = keepHide;
  renderFilterPills();
  saveFiltersToStorage();
}
$("#btnReset")?.addEventListener("click", () => { resetFilters(); applyFilters(); });
const PRESETS = {
  // automated downloads (e.g. IMDb lists): recently added, nobody requested them in Seerr, and nobody has watched them.
  // Every value is editable afterwards in the filter boxes.
  auto: { fUser: "__none", fNew: 30, fStale: 14, sort: ["added_ts", "desc"] },
  never: { fPct: 0, fAge: 365, sort: ["size", "desc"] },
  stale2: { fStale: 730, sort: ["size", "desc"] },
  lowrated: { fRating: 6, fPlays: 0, sort: ["rating", "asc"] },
  big: { fSize: 20, fStale: 365, sort: ["size", "desc"] },
  leaving: { fLeave: "marked", sort: ["leaving_due", "asc"] },
  leaving_due: { fLeave: "due", sort: ["leaving_due", "asc"] },
};
document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
  const p = PRESETS[b.dataset.preset];
  resetFilters();
  for (const [k, v] of Object.entries(p)) if (k !== "sort") { const el = $("#" + k); if (el) el.value = v; }
  S.sort = { key: p.sort[0], dir: p.sort[1] };
  // Highlight active preset
  document.querySelectorAll("[data-preset]").forEach((btn) => btn.classList.remove("active"));
  b.classList.add("active");
  applyFilters();
  renderFilterPills();
  saveFiltersToStorage();
}));

// ---------- keyboard navigation ----------
let focusedRowIdx = -1;
function updateRowFocus() {
  document.querySelectorAll("#rows tr[data-id]").forEach((tr, i) => {
    tr.classList.toggle("focused", i === focusedRowIdx);
    tr.setAttribute("tabindex", i === focusedRowIdx ? "0" : "-1");
  });
  if (focusedRowIdx >= 0) {
    const tr = document.querySelectorAll("#rows tr[data-id]")[focusedRowIdx];
    if (tr) { tr.focus(); tr.scrollIntoView({ block: "nearest" }); }
  }
}
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, dialog")) return;
  const rows = document.querySelectorAll("#rows tr[data-id]");
  if (!rows.length) return;
  if (e.key === "j" || e.key === "ArrowDown") {
    e.preventDefault();
    focusedRowIdx = Math.min(focusedRowIdx + 1, rows.length - 1);
    updateRowFocus();
  } else if (e.key === "k" || e.key === "ArrowUp") {
    e.preventDefault();
    focusedRowIdx = Math.max(focusedRowIdx - 1, 0);
    updateRowFocus();
  } else if (e.key === " " && focusedRowIdx >= 0) {
    e.preventDefault();
    const tr = rows[focusedRowIdx];
    const id = tr?.dataset.id;
    const item = S.byId.get(id);
    if (item && canSelect(item)) {
      S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id);
      render();
    }
  } else if (e.key === "Escape") {
    if (S.sel.size > 0) { S.sel.clear(); render(); }
    focusedRowIdx = -1;
    updateRowFocus();
  } else if (e.key === "?" && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    alert("Keyboard shortcuts:\n\nj / ↓ = Next row\nk / ↑ = Previous row\nSpace = Toggle selection\nEscape = Clear selection\nShift+Click = Range select");
  }
});

// ---------- data loading ----------
function setItems(list) {
  S.items = list;
  S.byId = new Map(list.map((i) => [i.id, i]));
  const libs = [...new Set(list.map((i) => i.library))].sort();
  const users = [...new Set(list.flatMap((i) => i.requested_by))].sort((a, b) => a.localeCompare(b));
  const keepSel = (sel, vals, first) => {
    const cur = $(sel).value;
    $(sel).innerHTML = first + vals.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    $(sel).value = vals.includes(cur) || cur === "__none" || cur === "__any" ? cur : "";
  };
  keepSel("#fLib", libs, `<option value="">All</option>`);
  const colls = [...new Set(list.flatMap((i) => i.collections))].sort((a, b) => a.localeCompare(b));
  keepSel("#fColl", colls, `<option value="">Any</option><option value="__any">In any collection</option><option value="__none">Not in a collection</option>`);
  keepSel("#fUser", users, `<option value="">Anyone</option><option value="__none">(nobody / unknown)</option>`);
}

async function loadItems() {
  const d = await api("/api/items");
  setItems(d.items);
  S.generated = d.generated_at;
  S.machineId = d.plex_machine_id || null;
  $("#genInfo").textContent = d.generated_at ? `Data from ${new Date(d.generated_at * 1000).toLocaleString()}${S.cfg.refresh_hours ? ` · auto every ${S.cfg.refresh_hours}h` : ""}` : "No data yet";
  const n = [];
  if (d.warnings?.length) n.push(`<div class="notice warn"><b>Heads up</b><ul>${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`);
  if (d.history_source === "plex") n.push(`<div class="notice warn">Play history is coming from Plex itself (one account only), so “Plays (1y)” is unavailable and “Last viewed” may miss other users. Connect Tautulli for accurate numbers.</div>`);
  $("#notices").innerHTML = n.join("");
  applyFilters();
}

let pollTimer = null;
async function startRefresh() {
  $("#btnRefresh").disabled = true;
  try { await post("/api/refresh"); } catch (e) { showErr(e.message); $("#btnRefresh").disabled = false; return; }
  pollStatus();
}
async function pollStatus() {
  clearTimeout(pollTimer);
  const s = await api("/api/status");
  if (s.state === "running") {
    $("#btnRefresh").disabled = true;
    $("#btnRefresh").textContent = "Refreshing…";
    $("#genInfo").textContent = s.message || "Working…";
    pollTimer = setTimeout(pollStatus, 1200);
    return;
  }
  $("#btnRefresh").disabled = false;
  $("#btnRefresh").textContent = "Refresh data";
  if (s.state === "error") showErr("Refresh failed: " + s.error);
  await loadItems();
}
function showErr(msg) {
  $("#notices").insertAdjacentHTML("afterbegin", `<div class="notice err">${esc(msg)}</div>`);
}
$("#btnRefresh").addEventListener("click", startRefresh);
let toastT = null;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 6000);
}

// ---------- keep ----------
$("#btnKeep").addEventListener("click", async () => {
  const ids = [...S.sel];
  const allKept = ids.every((id) => S.byId.get(id)?.keep);
  await post("/api/keep", { ids, keep: !allKept });
  S.sel.clear();
  await loadItems();
});

// ---------- leaving soon (optional; marks only, never deletes) ----------
$("#btnLeave").addEventListener("click", async () => {
  const ids = [...S.sel];
  try {
    const r = await post("/api/leaving/mark", { ids });
    toast(`${r.marked} title${r.marked === 1 ? "" : "s"} marked leaving soon (due ${fmtDate(r.due)})${r.skipped_protected ? `; ${r.skipped_protected} protected skipped` : ""}. The Plex row updates in a moment.`);
  } catch (e) { showErr(e.message); return; }
  S.sel.clear(); await loadItems();
});
$("#btnUnleave").addEventListener("click", async () => {
  try { await post("/api/leaving/unmark", { ids: [...S.sel] }); toast("Taken out of the Leaving Soon row."); } catch (e) { showErr(e.message); return; }
  S.sel.clear(); await loadItems();
});

// ---------- delete ----------
const dlg = $("#dlgDelete");
function openDelete() {
  const chosen = [...S.sel].map((id) => S.byId.get(id)).filter(Boolean);
  const items = chosen.filter((i) => !i.protected.length);
  if (!items.length) { showErr("Everything selected is protected (Keep or a protection rule), so there is nothing to delete."); return; }
  const skipped = chosen.length - items.length;
  const total = items.reduce((a, i) => a + i.size, 0);
  const noArr = items.filter((i) => !i.arr).length;
  const withSeerr = items.filter((i) => i.seerr_media_id).length;
  const hasArr = S.cfg.services.radarr || S.cfg.services.sonarr, hasSeerr = S.cfg.services.seerr;
  const dry = S.cfg.dry_run;
  $("#delTitle").textContent = (dry ? "Dry run: " : "Delete ") + `${items.length} item${items.length === 1 ? "" : "s"} · ${fmtSize(total)}`;
  const list = items.slice(0, 200).map((i) => `<div><span>${esc(i.title)} ${i.year ? `(${i.year})` : ""}</span><span class="dim">${fmtSize(i.size)}</span></div>`).join("")
    + (items.length > 200 ? `<div><span class="dim">…and ${items.length - 200} more</span></div>` : "");
  const isLargeBatch = items.length > 20 && !dry;
  $("#delBody").innerHTML = `
    ${dry ? `<div class="notice info"><b>Dry-run mode is ON.</b> Nothing will be deleted - you'll get a report of exactly what would happen. Set <code>DRY_RUN=false</code> on the container to enable real deletion.</div>`
          : `<div class="notice err"><b>Dry-run is OFF.</b> Files will be permanently deleted from disk.</div>`}
    ${skipped ? `<div class="notice warn">${skipped} protected item${skipped === 1 ? " was" : "s were"} left out of this selection.</div>` : ""}
    <div class="list">${list}</div>
    <label class="opt ${hasArr ? "" : "off"}"><input type="checkbox" id="oArr" ${hasArr ? "checked" : "disabled"}><span><b>Remove from Radarr / Sonarr</b> and delete the files<small>${hasArr ? `${items.length - noArr} of ${items.length} are managed by Radarr/Sonarr.` : "Radarr/Sonarr aren't connected (add them in Settings to use this)."}</small></span></label>
    <label class="opt ${hasArr ? "" : "off"}"><input type="checkbox" id="oExcl" ${hasArr ? "" : "disabled"}><span>Also add to the import exclusion list<small>Stops lists/RSS from re-adding it automatically.</small></span></label>
    <label class="opt ${hasSeerr ? "" : "off"}"><input type="checkbox" id="oSeerr" ${hasSeerr ? "checked" : "disabled"}><span><b>Remove from Seerr</b> (clears the request so it can be requested again)<small>${hasSeerr ? `${withSeerr} of ${items.length} have a Seerr record.` : "Seerr isn't connected (add it in Settings to use this)."}</small></span></label>
    <label class="opt"><input type="checkbox" id="oPlex" checked><span><b>Scan Plex library and empty trash</b> afterwards</span></label>
    <label class="opt"><input type="checkbox" id="oPlexDel" ${noArr ? "checked" : ""}><span>Delete through Plex when not removed via Radarr/Sonarr<small>${noArr} selected item${noArr === 1 ? " isn't" : "s aren't"} in Radarr/Sonarr. Requires “Allow media deletion” in Plex settings.</small></span></label>
    ${dry ? "" : isLargeBatch
      ? `<div class="confirm-box"><p>You are about to delete <b>${items.length} items</b> (${fmtSize(total)}). Type <b>DELETE ${items.length}</b> to confirm:</p><input type="text" id="oConfirmText" placeholder="DELETE ${items.length}" autocomplete="off" spellcheck="false"></div>`
      : `<label class="confirm-box"><input type="checkbox" id="oConfirm"> <span>I understand ${items.length === 1 ? "this item and its files" : `these ${items.length} items and their files`} (${fmtSize(total)}) will be permanently deleted.</span></label>`}`;
  $("#delFoot").innerHTML = `<span class="grow"></span><button class="btn" id="delCancel">Cancel</button><button class="btn ${dry ? "primary" : "danger"}" id="delGo" ${dry ? "" : "disabled"}>${dry ? "Run dry run" : `Delete ${items.length} item${items.length === 1 ? "" : "s"}`}</button>`;
  dlg.showModal();
  $("#delCancel").onclick = () => dlg.close();
  if (!dry) {
    if (isLargeBatch) {
      const expected = `DELETE ${items.length}`;
      $("#oConfirmText").addEventListener("input", (e) => { $("#delGo").disabled = e.target.value !== expected; });
    } else {
      $("#oConfirm").addEventListener("change", (e) => { $("#delGo").disabled = !e.target.checked; });
    }
  }
  $("#delGo").onclick = () => runDelete(items.map((i) => i.id));
}
$("#btnDelete").addEventListener("click", openDelete);

async function runDelete(ids) {
  const options = {
    arr: $("#oArr").checked, exclusion: $("#oExcl").checked, seerr: $("#oSeerr").checked,
    plex_cleanup: $("#oPlex").checked, plex_delete: $("#oPlexDel").checked,
  };
  $("#delGo").disabled = true; $("#delGo").textContent = "Working…"; $("#delCancel").disabled = true;
  let d;
  try { d = await post("/api/delete", { ids, options }); }
  catch (e) { $("#delBody").insertAdjacentHTML("afterbegin", `<div class="notice err">${esc(e.message)}</div>`); $("#delGo").textContent = "Retry"; $("#delGo").disabled = false; $("#delCancel").disabled = false; return; }
  const s = d.summary;
  $("#delTitle").textContent = s.dry_run ? "Dry run complete" : "Deletion complete";
  $("#delBody").innerHTML = `
    <div class="notice ${s.failed ? "warn" : "info"}"><b>${s.ok}</b> ${s.dry_run ? "would be removed" : "removed"} (${fmtSize(s.bytes)})${s.failed ? ` · <b>${s.failed}</b> failed/skipped` : ""}.
      ${s.plex_cleanup ? `<br>${esc(s.plex_cleanup)}` : ""}</div>
    ${d.results.map((r) => `<div class="res-row"><div class="h"><span class="${r.ok ? "ok" : "bad"}">${r.ok ? "✓" : "✗"}</span><b>${esc(r.title)}</b> <span class="dim">${fmtSize(r.size)}</span></div>
      ${r.steps.map((st) => `<div class="res-step ${st.ok ? "" : "bad"}">${esc(st.step)}: ${esc(st.detail)}</div>`).join("")}</div>`).join("")}`;
  $("#delFoot").innerHTML = `<span class="grow"></span><button class="btn primary" id="delDone">Close</button>`;
  $("#delDone").onclick = async () => { dlg.close(); if (!s.dry_run) { S.sel.clear(); await loadItems(); } };
  if (!s.dry_run) { S.sel.clear(); }
}

// ---------- settings ----------
const SVCS = [
  { id: "plex", name: "Plex", url: "plex_url", key: "plex_token", keyLabel: "Token", ph: "http://192.168.1.10:32400", req: true },
  { id: "tautulli", name: "Tautulli", url: "tautulli_url", key: "tautulli_key", keyLabel: "API key", ph: "http://192.168.1.10:8181" },
  { id: "radarr", name: "Radarr", url: "radarr_url", key: "radarr_key", keyLabel: "API key", ph: "http://192.168.1.10:7878" },
  { id: "sonarr", name: "Sonarr", url: "sonarr_url", key: "sonarr_key", keyLabel: "API key", ph: "http://192.168.1.10:8989" },
  { id: "seerr", name: "Seerr", url: "seerr_url", key: "seerr_key", keyLabel: "API key", ph: "http://192.168.1.10:5055" },
];
const dlgSet = $("#dlgSettings");

const TB_SCOPES = { day: "Same day of the year", week: "Within 3 days of today", month: "Same month", custom: "Custom window (days before / after today)" };
const TB_ORDERS = { rating: "Highest IMDb rating first", oldest: "Oldest release first", newest: "Newest release first", title: "Title A-Z", shuffle: "Shuffled (new order each day)" };
function throwbackValues() {
  return { throwback_enabled: String($("#s_tb_enabled").checked), throwback_name: $("#s_tb_name").value.trim(), throwback_scope: $("#s_tb_scope").value,
           throwback_min_years: $("#s_tb_min").value.trim(), throwback_max_years: $("#s_tb_max").value.trim(), throwback_min_rating: $("#s_tb_rating").value.trim(),
           throwback_limit: $("#s_tb_limit").value.trim(), throwback_genres: $("#s_tb_genres").value.trim(), throwback_genre_mode: $("#s_tb_gmode").value, throwback_tags: $("#s_tb_tags").value.trim(), throwback_before: $("#s_tb_before").value.trim(), throwback_after: $("#s_tb_after").value.trim(), throwback_order: $("#s_tb_order").value, throwback_pin: String($("#s_tb_pin").checked) };
}
function leavingValues() {
  return { leaving_enabled: String($("#s_lv_enabled").checked), leaving_name: $("#s_lv_name").value.trim(), leaving_days: $("#s_lv_days").value.trim(), leaving_pin: String($("#s_lv_pin").checked) };
}
function leavingHtml(cur) {
  return `<fieldset class="svc"><legend>Leaving Soon <span class="dim">(Plex home row, off by default)</span></legend>
    <label class="check" style="grid-column:1/-1;flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="s_lv_enabled" ${cur.leaving_enabled === "true" ? "checked" : ""}> Let me mark titles as “leaving soon” and show them in a Plex Home row</label>
    <div class="muted" style="grid-column:1/-1">Off = no row in Plex and no Leaving Soon buttons here. On = you choose titles in Prune and press <b>Leaving soon</b>; they appear in the row with a date. <b>Nothing is ever deleted automatically</b>: when the date passes, the title stays until you delete it yourself.</div>
    <label>Row name shown in Plex<input type="text" id="s_lv_name" maxlength="100" value="${esc(cur.leaving_name)}"></label>
    <label>Default days until due<input type="number" id="s_lv_days" min="1" max="365" value="${esc(cur.leaving_days)}"></label>
    <label class="check" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="s_lv_pin" ${cur.leaving_pin !== "false" ? "checked" : ""}> Pin the row to Home</label>
  </fieldset>`;
}
function throwbackHtml(cur) {
  const opts = (o, v) => Object.entries(o).map(([k, t]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(t)}</option>`).join("");
  return `<fieldset class="svc"><legend>Released on this day <span class="dim">(Plex home row)</span></legend>
    <label class="check" style="grid-column:1/-1;flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="s_tb_enabled" ${cur.throwback_enabled === "true" ? "checked" : ""}> Show a nostalgia row of movies released on this date in past years</label>
    <label>Row name shown in Plex<input type="text" id="s_tb_name" maxlength="100" value="${esc(cur.throwback_name)}"></label>
    <label>Timeframe<select id="s_tb_scope">${opts(TB_SCOPES, cur.throwback_scope)}</select></label>
    <label>Released at least (years ago)<input type="number" id="s_tb_min" min="0" max="150" value="${esc(cur.throwback_min_years)}"></label>
    <label>…and at most (years ago, optional)<input type="number" id="s_tb_max" min="0" max="150" value="${esc(cur.throwback_max_years)}" placeholder="no limit"></label>
    <label>Minimum IMDb rating<input type="number" id="s_tb_rating" min="0" max="10" step="0.1" value="${esc(cur.throwback_min_rating)}"></label>
    <label id="tbBeforeWrap">Include movies whose anniversary was up to… (days ago)<input type="number" id="s_tb_before" min="0" max="183" value="${esc(cur.throwback_before)}"></label>
    <label id="tbAfterWrap">…or is coming up within (days ahead)<input type="number" id="s_tb_after" min="0" max="183" value="${esc(cur.throwback_after)}"></label>
    <label>Maximum movies in the row (blank = all of them)<input type="number" id="s_tb_limit" min="1" max="5000" value="${esc(cur.throwback_limit)}" placeholder="all"></label>
    <label>Genres (optional, comma-separated)<input type="text" id="s_tb_genres" list="tbGenreList" value="${esc(cur.throwback_genres)}" placeholder="e.g. Comedy, Western" autocomplete="off"><datalist id="tbGenreList"></datalist></label>
    <label>…a movie must match<select id="s_tb_gmode"><option value="any" ${cur.throwback_genre_mode !== "all" ? "selected" : ""}>any one of those genres</option><option value="all" ${cur.throwback_genre_mode === "all" ? "selected" : ""}>every one of those genres</option></select></label>
    <label>Tags / Plex labels (optional, comma-separated)<input type="text" id="s_tb_tags" list="tbTagList" value="${esc(cur.throwback_tags)}" placeholder="e.g. Classic, Family Night" autocomplete="off"><datalist id="tbTagList"></datalist></label>
    <label>Order in Plex<select id="s_tb_order">${opts(TB_ORDERS, cur.throwback_order)}</select></label>
    <label class="check" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="s_tb_pin" ${cur.throwback_pin !== "false" ? "checked" : ""}> Pin to Plex Home</label>
    <div class="testrow"><button class="btn small" type="button" id="tbPreview">Preview today</button><button class="btn small" type="button" id="tbPublish">Save &amp; publish now</button><span class="tres" id="tbRes"></span></div>
    <div id="tbAll" class="list" hidden style="grid-column:1/-1"></div>
    <div class="muted" style="grid-column:1/-1"><b>Set and forget:</b> once this is on, the row updates itself every day and after each data refresh, and any change you make here is applied as soon as you save. Turning it off removes the row from Plex. Uses each movie's release date from Plex (Radarr's cinema date if Plex has none); unrated movies are left out. Pinned collections also get Plex's own “See all” link. Drag the row to where you want it on Home (Plex remembers its position).</div>
  </fieldset>`;
}
function tbShowAll(all) {
  const box = $("#tbAll");
  box.innerHTML = all.map((m) => `<div><span>${esc(m.title)} <span class="dim">(${esc((m.released || "").slice(0, 4))})</span></span><span class="dim">${m.rating != null ? "IMDb " + m.rating.toFixed(1) + " · " : ""}${esc(m.released || "")}</span></div>`).join("");
  box.hidden = false; box.style.maxHeight = "260px";
}
function tbSyncScope() {
  const c = $("#s_tb_scope").value === "custom";
  $("#tbBeforeWrap").hidden = !c; $("#tbAfterWrap").hidden = !c;
}
async function tbPreview() {
  const res = $("#tbRes"); res.className = "tres"; res.textContent = "Checking…"; $("#tbAll").hidden = true;
  try {
    const d = await post("/api/throwback/preview", { values: throwbackValues() });
    res.className = "tres ok";
    if (!d.have_report) { res.textContent = "Click Refresh data first, then preview."; return; }
    if (!d.count) { res.textContent = `Nothing matches for ${d.today}. Try a wider timeframe or a lower rating.`; return; }
    res.innerHTML = `${d.count} movie${d.count === 1 ? "" : "s"} for ${esc(d.today)}: ${esc(d.titles.slice(0, 5).join(", "))}${d.count > 5 ? "… " : " "}`;
    if (d.count > 5) { const a = document.createElement("a"); a.href = "#"; a.textContent = `View all ${d.count}`; a.onclick = (e) => { e.preventDefault(); tbShowAll(d.all); }; res.appendChild(a); }
  } catch (e) { res.className = "tres bad"; res.textContent = e.message; }
}
async function tbPublish() {
  const res = $("#tbRes"); res.className = "tres"; res.textContent = "Publishing…";
  try {
    await post("/api/settings", { values: { ...throwbackValues(), throwback_enabled: "true" } });
    $("#s_tb_enabled").checked = true;
    const d = await post("/api/throwback/publish", {});
    res.className = "tres ok";
    res.textContent = `Published ${d.count} movie${d.count === 1 ? "" : "s"}. ` + d.results.map((r) => r.warning || r.action).join(", ");
  } catch (e) { res.className = "tres bad"; res.textContent = e.message; }
}

function settingsValues(svcOnly) {
  const out = {};
  for (const s of SVCS) {
    if (svcOnly && svcOnly !== s.id) continue;
    out[s.url] = $("#s_" + s.url).value.trim();
    out[s.key] = $("#s_" + s.key).value.trim(); // blank = keep saved key
  }
  if (!svcOnly) { out.plex_libraries = $("#s_plex_libraries").value.trim(); out.plex_link_base = $("#s_plex_link_base").value.trim(); out.refresh_hours = $("#s_refresh_hours").value; Object.assign(out, throwbackValues(), leavingValues()); }
  return out;
}

async function openSettings() {
  const cur = await api("/api/settings");
  $("#setBody").innerHTML = `<p class="muted" style="margin:0 0 8px">Saved here, in the app's data folder. These override any values set on the container. Keys are never shown again once saved.</p>`
    + SVCS.map((s) => `<fieldset class="svc"><legend>${s.name}${s.req ? " <span class='dim'>(required)</span>" : ""}</legend>
      <label>URL<input type="text" id="s_${s.url}" value="${esc(cur[s.url])}" placeholder="${esc(s.ph)}" autocomplete="off" spellcheck="false"></label>
      <label>${s.keyLabel}<input type="password" id="s_${s.key}" value="" placeholder="${cur[s.key].set ? "Saved - leave blank to keep" : s.keyLabel}" autocomplete="new-password"></label>
      <div class="testrow"><button class="btn small" data-test="${s.id}">Test connection</button><span class="tres" id="t_${s.id}"></span></div></fieldset>`).join("")
    + `<fieldset class="svc"><legend>Plex libraries <span class='dim'>(optional)</span></legend>
      <label>Only include these libraries (comma-separated, blank = all movie and TV libraries)<input type="text" id="s_plex_libraries" value="${esc(cur.plex_libraries)}" placeholder="Movies, TV Shows" autocomplete="off"></label></fieldset>
      <fieldset class="svc"><legend>Automatic refresh</legend>
      <label>Rebuild the report on its own every<select id="s_refresh_hours">${[0, 1, 2, 4, 6, 12, 24, 48, 168].concat(cur.refresh_hours && ![1, 2, 4, 6, 12, 24, 48, 168].includes(+cur.refresh_hours) ? [+cur.refresh_hours] : []).sort((a, b) => a - b).map((h) => `<option value="${h}" ${String(h) === String(cur.refresh_hours) ? "selected" : ""}>${h === 0 ? "Never - only when I click Refresh data" : h === 168 ? "week" : h === 1 ? "hour" : h + " hours"}</option>`).join("")}</select></label>
      <div class="muted" style="grid-column:1/-1">A refresh reads Plex, Tautulli, Radarr, Sonarr and Seerr (and cast/crew for new items). It never deletes anything. It waits 15 minutes after a deletion, and an open page updates itself when it finishes.</div></fieldset>
      ${throwbackHtml(cur)}
      ${leavingHtml(cur)}
      <fieldset class="svc"><legend>Plex links <span class='dim'>(optional)</span></legend>
      <label>Where “open in Plex” links go. Blank = Plex's hosted app (https://app.plex.tv/desktop), which works anywhere you're signed in. For LAN-only use, enter your server, e.g. http://192.168.1.10:32400/web<input type="text" id="s_plex_link_base" value="${esc(cur.plex_link_base)}" placeholder="https://app.plex.tv/desktop" autocomplete="off" spellcheck="false"></label></fieldset>
      <div id="setErr" class="notice err" hidden></div>`;
  $("#setFoot").innerHTML = `<span class="grow"></span><button class="btn" id="setCancel">Cancel</button><button class="btn primary" id="setSave">Save &amp; refresh data</button>`;
  $("#setCancel").onclick = () => dlgSet.close();
  $("#tbPreview").onclick = tbPreview; $("#tbPublish").onclick = tbPublish;
  $("#s_tb_scope").addEventListener("change", tbSyncScope); tbSyncScope();
  api("/api/facets").then((f) => {   // suggestions from your own library
    $("#tbGenreList").innerHTML = f.genres.map((g) => `<option value="${esc(g)}">`).join("");
    $("#tbTagList").innerHTML = f.tags.map((g) => `<option value="${esc(g)}">`).join("");
  }).catch(() => {});
  $("#setSave").onclick = saveSettings;
  $("#setBody").querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", () => testService(b.dataset.test, b)));
  dlgSet.showModal();
}

async function testService(id, btn) {
  const out = $("#t_" + id);
  out.className = "tres"; out.textContent = "Testing…"; btn.disabled = true;
  try {
    const r = await post("/api/settings/test", { service: id, values: settingsValues(id) });
    out.className = "tres " + (r.ok ? "ok" : "bad");
    out.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
  } catch (e) { out.className = "tres bad"; out.textContent = "✗ " + e.message; }
  btn.disabled = false;
}

async function saveSettings() {
  const err = $("#setErr"); err.hidden = true;
  $("#setSave").disabled = true;
  try {
    const r = await post("/api/settings", { values: settingsValues() });
    S.cfg.services = r.services;
    S.cfg = await api("/api/config");               // picks up a changed Plex link base
    applyServices();
    dlgSet.close();
    $("#notices").innerHTML = "";
    startRefresh();
  } catch (e) { err.textContent = e.message; err.hidden = false; }
  $("#setSave").disabled = false;
}
$("#btnSettings").addEventListener("click", openSettings);

// ---------- views (Candidates / History) ----------
function fmtBig(b) {
  if (!b) return "0 GB";
  const gb = b / 1073741824;
  return gb >= 1024 ? (gb / 1024).toFixed(2) + " TB" : gb >= 100 ? gb.toFixed(0) + " GB" : gb.toFixed(1) + " GB";
}
function showView(name) {
  const hist = name === "history", prot = name === "protect", rws = name === "rows", hlp = name === "help", act = name === "activity", req = name === "request";
  $("#viewReq").hidden = !req;
  $("#tabReq").classList.toggle("active", req);
  $("#viewAct").hidden = !act;
  $("#tabAct").classList.toggle("active", act);
  actStop();
  $("#viewRows").hidden = !rws;
  $("#tabRows").classList.toggle("active", rws);
  $("#viewHelp").hidden = !hlp;
  $("#tabHelp").classList.toggle("active", hlp);
  $("#viewMain").hidden = hist || prot || rws || hlp || act || req;
  $("#viewHist").hidden = !hist;
  $("#viewProt").hidden = !prot;
  $("#tabMain").classList.toggle("active", !hist && !prot && !rws && !hlp && !act && !req);
  $("#tabHist").classList.toggle("active", hist);
  $("#tabProt").classList.toggle("active", prot);
  document.body.classList.toggle("view-hist", hist || prot || rws || hlp || act || req);
  if (req) setTimeout(() => $("#reqQ").focus(), 0);
  if (act) actStart();
  if (hist) loadHistory(true);
  if (prot) loadRules();
  if (rws) loadRows();
}
// ---------- Request tab (search Seerr, request any title) ----------
const REQ = { t: null, n: 0 };
const REQ_LABEL = { available: "Available", partial: "Partially available", processing: "Requested", pending: "Pending", blocklisted: "Blocklisted" };
function reqCard(it) {
  const done = ["available", "processing", "pending", "blocklisted"].includes(it.status);
  const label = REQ_LABEL[it.status];
  return `<div class="reqcard" data-t="${esc(it.type)}" data-id="${it.tmdb_id}">
    ${it.poster ? `<img class="mthumb" src="/api/tmdbimg/${esc(it.poster)}" alt="" loading="lazy" width="60" height="90">` : `<span class="mthumb"></span>`}
    <div class="reqinfo"><div class="mtitle2"><b>${esc(it.title)}</b> ${it.year ? `<span class="dim">(${esc(it.year)})</span>` : ""} <span class="badge">${it.type === "movie" ? "Movie" : "Show"}</span>${it.rating ? ` <span class="dim">★ ${it.rating}</span>` : ""}</div>
      <div class="sub">${esc(it.overview)}</div></div>
    <div class="reqact">${label ? `<span class="badge" data-state="${esc(it.status)}">${esc(label)}</span>` : ""}
      ${done ? "" : `<button class="btn small primary reqbtn" type="button">${it.status === "partial" ? "Request more" : "Request"}</button>`}</div></div>`;
}
function reqNote(msg) { const n = $("#reqNote"); n.hidden = !msg; n.textContent = msg || ""; }
async function reqSearch() {
  const q = $("#reqQ").value.trim(), n = ++REQ.n, box = $("#reqRes");
  reqNote("");
  if (q.length < 2) { box.innerHTML = ""; return; }
  try {
    const j = await api("/api/seerr/search?q=" + encodeURIComponent(q));
    if (n !== REQ.n) return;
    box.innerHTML = j.results.length ? j.results.map(reqCard).join("") : `<p class="muted">Nothing found for “${esc(q)}”.</p>`;
  } catch (e) { if (n === REQ.n) { box.innerHTML = ""; reqNote("Search failed: " + e.message); } }
}
$("#reqQ").addEventListener("input", () => { clearTimeout(REQ.t); REQ.t = setTimeout(reqSearch, 350); });
$("#reqRes").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".reqbtn"); if (!b) return;
  const card = b.closest(".reqcard"), title = card.querySelector("b").textContent;
  b.disabled = true; b.textContent = "Requesting…";
  try {
    await post("/api/seerr/request", { type: card.dataset.t, tmdb_id: Number(card.dataset.id) });
    b.replaceWith(Object.assign(document.createElement("span"), { className: "badge", textContent: "Requested" }));
    card.querySelector(".reqact .badge")?.setAttribute("data-state", "processing");
    toast(`Requested “${title}”`);
  } catch (e) { b.disabled = false; b.textContent = "Request"; reqNote(`Couldn't request “${title}”: ${e.message}`); }
});

// ---------- Activity (Tautulli: now playing + recent plays) ----------
const A = { sess: [], at: 0, sig: null, tick: null, timer: null, start: 0, rows: 0, busy: false, gen: 0 };
const fmtDur = (sec) => (!sec ? "" : sec >= 3600 ? Math.floor(sec / 3600) + "h " + Math.round((sec % 3600) / 60) + "m" : Math.max(1, Math.round(sec / 60)) + "m");
const fmtWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");
const titleOf = (p) => esc(p.title) + (p.subtitle ? ` <span class="muted">${esc(p.subtitle.replace(p.title + " - ", ""))}</span>` : p.year ? ` <span class="muted">(${esc(p.year)})</span>` : "");
function actNotice(msg) { const n = $("#aNotice"); n.hidden = !msg; n.textContent = msg || ""; }
const fmtClock = (sec) => { sec = Math.max(0, sec | 0); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), r = sec % 60; return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(r).padStart(2, "0"); };
const thumbUrl = (p, av) => (p ? `/api/plexthumb?img=${encodeURIComponent(p)}${av ? "&avatar=1" : ""}` : "");
function nowCard(x) {
  const rows = [["Product", x.product], ["Player", x.player], ["Quality", x.quality], null, ["Stream", x.stream], ["Container", x.container],
    ["Video", x.video], ["Audio", x.audio], ["Subtitle", x.subtitles], null, ["Location", (x.secure ? "🔒 " : "") + x.location], ["Bandwidth", x.bandwidth ? x.bandwidth + " Mbps" : ""]];
  const det = rows.map((r) => (r === null ? '<div class="gap"></div>' : r[1] ? `<div class="k">${esc(r[0])}</div><div class="v">${esc(r[1])}</div>` : "")).join("");
  const ends = `<span class="ends">${x.ends_ts ? `Ends at: ${new Date(x.ends_ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</span><br>`;
  const ini = esc((x.user || "?").trim().charAt(0).toUpperCase());
  return `<div class="nowitem"><div class="nowbox">
      <div class="nowposter">${x.poster ? `<img src="${esc(thumbUrl(x.poster))}" alt="" loading="lazy">` : ""}</div>
      <div class="nowdet">${det}<div class="nowends">${ends}<span class="nowclk">${fmtClock(x.elapsed)} / ${fmtClock(x.length)}</span></div></div>
      <div class="nowbar"><span style="width:${Math.min(100, x.progress)}%"></span></div></div>
    <div class="nowfoot"><div class="nowl"><div class="nowtitle"><span class="st">${x.state === "paused" ? "❚❚" : x.state === "buffering" ? "…" : "▶"}</span> ${esc(x.title)}</div>
      <div class="muted">${x.se ? `<b>${esc(x.se)}</b> · ` : ""}${esc(x.subtitle ? x.subtitle.replace(x.title + " - ", "") : x.year || "")}</div></div>
      <div class="nowuser">${x.key ? `<button class="btn small stopbtn" data-key="${esc(x.key)}" data-who="${esc(x.user)}" data-what="${esc(x.title)}" title="Stop this stream">Stop</button>` : ""}<span>${esc(x.user)}</span><span class="av">${ini}${x.avatar ? `<img src="${esc(thumbUrl(x.avatar, true))}" alt="">` : ""}</span></div></div></div>`;
}
// Live updates: poll Tautulli every 5 s, and tick playing streams forward every second in between (no flicker, no reload).
function actTick() {
  const dt = (Date.now() - A.at) / 1000;
  document.querySelectorAll("#aNow .nowitem").forEach((el, i) => {
    const x = A.sess[i]; if (!x) return;
    const el_s = Math.min(x.length || 1e9, x.elapsed + (x.state === "playing" ? dt : 0));
    const bar = el.querySelector(".nowbar span"), clk = el.querySelector(".nowclk");
    if (bar && x.length) bar.style.width = Math.min(100, (el_s / x.length) * 100) + "%";
    if (clk) clk.textContent = `${fmtClock(el_s)} / ${fmtClock(x.length)}`;
  });
}
function actBadge(n) {
  const b = $("#actBadge");
  b.hidden = !n; b.textContent = n || "";
  b.title = n ? `${n} active stream${n > 1 ? "s" : ""}` : "";
  $("#tabAct").setAttribute("aria-label", n ? `Activity, ${n} active stream${n > 1 ? "s" : ""}` : "Activity");
}
// Keep the bubble current on every tab (the Activity tab polls faster itself while it is open).
const bgAct = { timer: null };
async function bgActPoll() {
  if (document.hidden || A.timer || !S.cfg?.services?.tautulli) return;
  try { actBadge((await api("/api/activity")).sessions.length); }
  catch (e) { if (/isn't configured/.test(e.message)) { clearInterval(bgAct.timer); actBadge(0); } }
}
bgAct.timer = setInterval(bgActPoll, 15000);
bgActPoll();
document.addEventListener("visibilitychange", () => { if (!document.hidden) bgActPoll(); });
async function actNow() {
  try {
    const j = await api("/api/activity");
    actNotice("");
    actBadge(j.sessions.length);
    A.sess = j.sessions; A.at = Date.now();
    const sig = JSON.stringify(j.sessions.map((x) => ({ ...x, elapsed: 0, progress: 0, ends_ts: 0, bandwidth: 0 })));
    if (sig !== A.sig) { A.sig = sig; $("#aNow").innerHTML = j.sessions.map(nowCard).join(""); }
    else document.querySelectorAll("#aNow .nowends .ends").forEach((el, i) => { const t = j.sessions[i]; el.textContent = t && t.ends_ts ? `Ends at: ${new Date(t.ends_ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""; });
    $("#aNowEmpty").hidden = j.sessions.length > 0;
    const m = j.summary || {}, parts = [m.direct && `${m.direct} direct play`, m.stream_copy && `${m.stream_copy} direct stream`, m.transcode && `${m.transcode} transcode`].filter(Boolean);
    $("#aNowMeta").textContent = j.sessions.length ? `Sessions: ${j.sessions.length} stream${j.sessions.length > 1 ? "s" : ""}${parts.length ? " (" + parts.join(", ") + ")" : ""} | Bandwidth: ${m.bandwidth} Mbps (LAN: ${m.lan} Mbps)` : "";
    actTick();
  } catch (e) { actNotice(e.message); $("#aNow").innerHTML = ""; A.sig = null; $("#aNowEmpty").hidden = true; }
}
$("#aNow").addEventListener("click", (e) => {
  const b = e.target.closest(".stopbtn"); if (!b) return;
  $("#stopWho").innerHTML = `Stop <b>${esc(b.dataset.who)}</b>'s stream of <b>${esc(b.dataset.what)}</b>? Playback ends immediately on their device.`;
  $("#stopMsg").value = ""; $("#stopErr").hidden = true;
  const go = $("#stopGo"); go.disabled = false; go.dataset.key = b.dataset.key;
  $("#dlgStop").showModal();
});
$("#stopCancel").onclick = () => $("#dlgStop").close();
$("#stopGo").onclick = async () => {
  const go = $("#stopGo"); go.disabled = true;
  try {
    await post("/api/activity/terminate", { key: go.dataset.key, message: $("#stopMsg").value });
    $("#dlgStop").close(); actNow();
  } catch (e) { $("#stopErr").textContent = e.message; $("#stopErr").hidden = false; go.disabled = false; }
};
document.addEventListener("error", (e) => { if (e.target.tagName === "IMG" && e.target.closest(".nowitem")) e.target.remove(); }, true);
async function actPlays(reset) {
  if (A.busy) return;
  A.busy = true;
  const gen = A.gen;
  if (reset) { A.start = 0; A.rows = 0; $("#aRows").innerHTML = ""; }
  try {
    const qs = new URLSearchParams({ start: A.start, length: $("#aLen").value, user: $("#aUser").value, type: $("#aType").value, q: $("#aQ").value.trim() });
    const j = await api("/api/plays?" + qs);
    if (gen !== A.gen) return;
    if (j.users) {
      const cur = $("#aUser").value;
      $("#aUser").innerHTML = `<option value="">Anyone</option>` + j.users.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("");
      $("#aUser").value = cur;
    }
    const hm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    const mins = (sec) => Math.round(sec / 60) + " mins";
    $("#aRows").insertAdjacentHTML("beforeend", j.plays.map((p) => `<tr${p.live ? ' class="live"' : ""}><td>${p.live ? '<span class="liveico" title="Playing now">▶</span> ' : ""}${esc(fmtDate(p.ts))}</td><td>${esc(p.user)}</td>
      <td>${p.wan ? "📍 " : ""}${esc(p.ip)}</td><td>${esc(p.platform)}</td><td>${esc(p.product)}</td><td>${esc(p.player)}</td>
      <td>${p.type === "movie" ? "🎞️" : "📺"} ${esc(p.title)}</td><td>${esc(hm(p.ts))}</td><td>${esc(mins(p.paused))}</td><td>${p.stopped ? esc(hm(p.stopped)) : "n/a"}</td><td>${esc(mins(p.duration))}</td>
      <td class="wst"><span class="wcirc" style="--p:${Math.min(100, p.pct)}%" title="${p.pct}% watched"></span></td></tr>`).join(""));
    labelCells($("#aTbl"));
    A.start += j.plays.length; A.rows += j.plays.length;
    $("#aCount").textContent = A.rows;
    $("#aTotal").textContent = `of ${j.total}`;
    $("#aEmpty").hidden = A.rows > 0;
    $("#aMore").hidden = !j.plays.length || A.rows >= j.total;
    actNotice("");
  } catch (e) { actNotice(e.message); }
  finally { A.busy = false; }
}
function actStart() {
  if (!S.cfg.services.tautulli) { actBadge(0); return; }        // nothing to poll: the "needs Tautulli" panel is showing
  A.sig = null; actNow(); actPlays(true);
  A.timer = setInterval(() => { if (!document.hidden) actNow(); }, 5000);
  A.tick = setInterval(() => { if (!document.hidden) actTick(); }, 1000);
}
function actStop() { clearInterval(A.timer); clearInterval(A.tick); A.timer = A.tick = null; }
document.addEventListener("visibilitychange", () => { if (!document.hidden && A.timer) actNow(); });   // catch up the moment you switch back to the tab
let actDeb = null;
const actReload = () => { A.gen++; A.busy = false; actPlays(true); };
$("#aUser").addEventListener("change", actReload);
$("#aType").addEventListener("change", actReload);
$("#aQ").addEventListener("input", () => { clearTimeout(actDeb); actDeb = setTimeout(actReload, 300); });
$("#aLen").addEventListener("change", actReload);
$("#aQx").addEventListener("click", () => { $("#aQ").value = ""; actReload(); });
$("#aMore").addEventListener("click", () => actPlays(false));
const HASHES = { request: "#request", activity: "#activity", history: "#history", protect: "#protect", rows: "#rows", help: "#help" };
const viewFromHash = () => (location.hash === "#request" ? "request" : location.hash === "#activity" ? "activity" : location.hash === "#history" ? "history" : location.hash === "#protect" ? "protect" : location.hash === "#rows" ? "rows" : location.hash === "#help" ? "help" : "main");
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  const v = t.dataset.view;
  history.replaceState(null, "", HASHES[v] || location.pathname);
  showView(v);
}));
window.addEventListener("hashchange", () => {
  if (location.hash && !Object.values(HASHES).includes(location.hash)) return;   // in-page anchors (help contents, skip link) must not switch tabs
  showView(viewFromHash());
});
$("#helpToc").addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='#help-']");
  if (!a) return;
  e.preventDefault();
  const d = document.getElementById(a.getAttribute("href").slice(1));
  if (d) { d.hidden = false; d.open = true; d.scrollIntoView({ behavior: "smooth", block: "start" }); }
});
document.querySelector(".skip-link")?.addEventListener("click", (e) => { e.preventDefault(); $("#tbl").focus?.(); $("#tbl").scrollIntoView(); });

// ---------- history ----------
const H = { entries: [], polls: 0, poll: null, sig: "", page: 1, size: 25, sortKey: "time", sortDir: "desc" };
try { const n = localStorage.getItem("hSize"); if (n !== null && ["0", "25", "50", "100", "250"].includes(n)) H.size = +n; } catch (e) {}
try { const k = localStorage.getItem("hSortKey"); if (k && ["time", "title", "size"].includes(k)) H.sortKey = k; } catch (e) {}
try { const d = localStorage.getItem("hSortDir"); if (d && ["asc", "desc"].includes(d)) H.sortDir = d; } catch (e) {}
const isLive = (e) => !e.dry_run && e.ok;

async function loadHistory(fresh = false) {
  if (fresh) H.polls = 0;
  const d = await api("/api/log");
  const sig = d.entries.length + ":" + d.entries.filter((e) => e.imdb_id).length + ":" + d.entries.filter((e) => e.restored).length;
  const unchanged = !fresh && sig === H.sig && H.entries.length;      // background poll found nothing new: leave the table alone
  H.sig = sig;
  H.entries = d.entries;
  clearTimeout(H.poll);
  if ((d.pending || d.checking) && H.polls++ < 40) H.poll = setTimeout(() => { if (!$("#viewHist").hidden) loadHistory(); }, 3000);   // IMDb links fill in as they're found
  const cur = $("#hUser").value;
  const users = [...new Set(H.entries.flatMap((e) => e.requested_by || []))].sort((a, b) => a.localeCompare(b));
  $("#hUser").innerHTML = `<option value="">Anyone</option>` + users.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("") + `<option value="__none">(nobody / unknown)</option>`;
  if ([...$("#hUser").options].some((o) => o.value === cur)) $("#hUser").value = cur;
  if (!unchanged) renderHistory();
}

function monthKey(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }

function renderStats() {
  const all = H.entries.filter(isLive);
  const back = all.filter((e) => e.restored);
  const live = all.filter((e) => !e.restored);        // re-downloaded titles don't count as space freed
  const total = live.reduce((a, e) => a + e.size, 0);
  const typeOf = (e) => (e.type || String(e.id).split(":")[0]);
  const movies = live.filter((e) => typeOf(e) === "movie"), shows = live.filter((e) => typeOf(e) === "show");
  const cut = Date.now() / 1000 - 30 * 86400;
  const last30 = live.filter((e) => e.time >= cut);
  const card = (big, label, sub = "") => `<div class="stat"><div class="big">${big}</div><div class="lbl">${label}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
  $("#hStats").innerHTML =
    card(fmtBig(total), "Total space freed", (live.length ? `since ${fmtDate(Math.min(...live.map((e) => e.time)))}` : "nothing deleted yet") + (back.length ? ` · ${back.length} back in Plex (${fmtBig(back.reduce((a, e) => a + e.size, 0))}) not counted` : ""))
    + card(live.length.toLocaleString(), "Items deleted", `${movies.length} movie${movies.length === 1 ? "" : "s"} · ${shows.length} show${shows.length === 1 ? "" : "s"}`)
    + card(fmtBig(last30.reduce((a, e) => a + e.size, 0)), "Freed in the last 30 days", `${last30.length} item${last30.length === 1 ? "" : "s"}`)
    + card(fmtBig(live.length ? total / live.length : 0), "Average per item", live.length ? `${fmtBig(movies.reduce((a, e) => a + e.size, 0))} movies · ${fmtBig(shows.reduce((a, e) => a + e.size, 0))} shows` : "");

  // monthly chart: from the first month with deletions up to this month (at least 3 columns, at most 36), local time
  const byKey = new Map();
  for (const e of live) { const k = monthKey(new Date(e.time * 1000)); byKey.set(k, (byKey.get(k) || 0) + e.size); }
  const now = new Date(); now.setDate(1);
  let span = 3;
  if (live.length) {
    const first = new Date(Math.min(...live.map((e) => e.time)) * 1000);
    span = Math.min(36, Math.max(3, (now.getFullYear() - first.getFullYear()) * 12 + now.getMonth() - first.getMonth() + 1));
  }
  const months = [];
  for (let i = span - 1; i >= 0; i--) {
    const d = new Date(now); d.setMonth(d.getMonth() - i);
    months.push({ key: monthKey(d), label: d.toLocaleString(undefined, { month: "short" }), year: d.getFullYear(), bytes: byKey.get(monthKey(d)) || 0 });
  }
  const max = Math.max(...months.map((m) => m.bytes));
  const showYear = span > 12;
  $("#hChart").innerHTML = max > 0
    ? `<div class="chart ${span <= 6 ? "few" : ""}">${months.map((m, k) => `<div class="col" title="${m.label} ${m.year}: ${fmtBig(m.bytes)}"><div class="val">${m.bytes ? fmtSize(m.bytes) : ""}</div><div class="barwrap"><div class="bar" style="height:${Math.max(2, m.bytes / max * 100)}%${m.bytes ? "" : ";opacity:.25"}"></div></div><div class="mon">${m.label}${showYear && (m.label === "Jan" || k === 0) ? `<small>${m.year}</small>` : ""}</div></div>`).join("")}</div>`
    : `<p class="muted">Nothing deleted yet. Real deletions will show up here.</p>`;

  const top = [...live].sort((a, b) => b.size - a.size).slice(0, 5);
  $("#hTop").innerHTML = top.length
    ? top.map((e) => `<div class="toprow"><span>${esc(e.title)} ${e.year ? `<span class="dim">(${e.year})</span>` : ""}</span><span class="mono">${fmtSize(e.size)}</span></div>`).join("")
    : `<p class="muted">Nothing yet.</p>`;
}

function histView() {
  const mode = $("#hMode").value, q = $("#hQ").value.trim().toLowerCase();
  const filtered = H.entries.filter((e) => {
    if (mode === "live" && !isLive(e)) return false;
    if (mode === "restored" && !e.restored) return false;
    if (mode === "dry" && !e.dry_run) return false;
    if (mode === "skipped" && e.ok) return false;
    const u = $("#hUser").value, rb = e.requested_by || [];
    if (u === "__none" ? rb.length : u && !rb.includes(u)) return false;
    if (q && !`${e.title} ${e.year ?? ""} ${e.library ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  // Sort
  const cmp = H.sortKey === "time" ? (a, b) => a.time - b.time
    : H.sortKey === "title" ? (a, b) => (a.title || "").localeCompare(b.title || "")
    : (a, b) => a.size - b.size;
  filtered.sort(H.sortDir === "asc" ? cmp : (a, b) => cmp(b, a));
  return filtered;
}

function setHistSort(key) {
  if (H.sortKey === key) H.sortDir = H.sortDir === "asc" ? "desc" : "asc";
  else { H.sortKey = key; H.sortDir = key === "time" ? "desc" : "asc"; }
  try { localStorage.setItem("hSortKey", H.sortKey); localStorage.setItem("hSortDir", H.sortDir); } catch (e) {}
  H.page = 1;
  renderHistory();
}

function rerequestBtn(e) {
  if (e.dry_run || !e.ok || !S.cfg.services.seerr) return "";
  return `<button class="btn small rr" data-t="${esc(e.time)}" data-id="${esc(e.id)}">Re-request</button>`;
}
function histNote(msg, bad) {
  const el = $("#hNotice");
  el.className = "notice " + (bad ? "err" : "info"); el.textContent = msg; el.hidden = false;
  el.scrollIntoView({ block: "nearest" });
}
const rrReset = (b) => { b.disabled = false; b.dataset.arm = ""; b.textContent = "Re-request"; b.title = ""; };
$("#hRows").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button.rr");
  if (!b || b.disabled) return;
  const ref = { time: b.dataset.t, id: b.dataset.id };
  if (b.dataset.arm !== "1") {                       // 1st click: find the title in Seerr and show what will be requested
    b.disabled = true; b.textContent = "Looking up…";
    try {
      const d = await post("/api/rerequest/lookup", ref);
      b.disabled = false; b.dataset.arm = "1"; b.textContent = "Confirm?";
      b.title = `Will request “${d.title}”${d.year ? ` (${d.year})` : ""} in Seerr`;
      setTimeout(() => { if (b.isConnected && b.dataset.arm === "1" && !b.disabled) rrReset(b); }, 6000);
    } catch (err) { rrReset(b); histNote("Re-request: " + err.message, true); }
    return;
  }
  const e = H.entries.find((x) => String(x.time) === ref.time && x.id === ref.id);   // 2nd click: send it
  const asUser = $("#hAsUser").checked ? Object.keys((e && e.requester_ids) || {})[0] : undefined;
  b.disabled = true; b.textContent = "Sending…";
  try {
    const r = await post("/api/rerequest", { ...ref, as_user: asUser });
    histNote(`Sent “${r.title}” to Seerr${r.as_user ? ` as ${r.as_user}` : ""}. Radarr/Sonarr will pick it up from there.`, false);
    b.textContent = "Requested ✓"; b.classList.add("ok");
  } catch (err) { rrReset(b); histNote("Re-request failed: " + err.message, true); }
});

// Direct IMDb page when the log recorded the id; older entries fall back to an IMDb title search.
function imdbLink(e) {
  const href = /^tt\d+$/.test(e.imdb_id || "") ? `https://www.imdb.com/title/${e.imdb_id}/`
    : `https://www.imdb.com/find/?s=tt&q=${encodeURIComponent(`${e.title} ${e.year ?? ""}`.trim())}`;
  return `<a class="imdbtag" target="_blank" rel="noopener noreferrer" href="${href}" title="${e.imdb_id ? "Open on IMDb" : "Search IMDb"}">IMDb ↗</a>`;
}

function renderHistory() {
  renderStats();
  const v = histView();
  const pages = H.size ? Math.max(1, Math.ceil(v.length / H.size)) : 1;
  H.page = Math.min(Math.max(1, H.page), pages);
  const start = H.size ? (H.page - 1) * H.size : 0;
  const shown = H.size ? v.slice(start, start + H.size) : v;
  $("#hSize2").value = String(H.size);
  $("#hPageInfo").textContent = v.length ? `${start + 1}–${start + shown.length} of ${v.length.toLocaleString()}` : "";
  $("#hPrev").disabled = H.page <= 1;
  $("#hNext").disabled = H.page >= pages;
  $("#hPager").hidden = v.length === 0;
  // Update sort indicators
  document.querySelectorAll("#hTbl thead th.sortable").forEach((th) => {
    const k = th.dataset.sort;
    th.classList.toggle("sorted", k === H.sortKey);
    const ar = th.querySelector(".arrow"); if (ar) ar.textContent = k === H.sortKey ? (H.sortDir === "asc" ? "▲" : "▼") : "";
    th.setAttribute("aria-sort", k === H.sortKey ? H.sortDir === "asc" ? "ascending" : "descending" : "none");
  });
  $("#hRows").innerHTML = shown.map((e) => `<tr tabindex="0">
    <td class="mono">${new Date(e.time * 1000).toLocaleString()}</td>
    <td class="title-cell"><div class="t">${esc(e.title)} ${e.year ? `<span class="dim">(${e.year})</span>` : ""} ${imdbLink(e)}</div></td>
    <td>${esc(e.library || "")}</td>
    <td class="needs-seerr">${(e.requested_by || []).length ? esc(e.requested_by.join(", ")) : `<span class="dim">–</span>`}</td>
    <td class="num mono">${fmtSize(e.size)}</td>
    <td>${e.dry_run ? `<span class="badge dry" aria-label="Dry run">⚙ dry run</span>` : `<span class="badge live" aria-label="Live deletion">● live</span>`}</td>
    <td class="${e.ok ? "ok" : "bad"}">${e.ok ? (e.restored ? `<span class="back" title="Added to Plex again after this deletion">↩ Restored</span>` : `<span class="removed">✓ Removed</span>`) : `<span class="failed">✗ Failed</span>`}</td>
    <td>${rerequestBtn(e)}</td>
    <td><details><summary>${e.steps.length} step${e.steps.length === 1 ? "" : "s"}</summary>${e.steps.map((s) => `<div class="res-step ${s.ok ? "" : "bad"}">${esc(s.step)}: ${esc(s.detail)}</div>`).join("")}</details></td></tr>`).join("");
  $("#hEmpty").hidden = v.length > 0;
  $("#hEmpty").textContent = H.entries.length ? "Nothing matches this filter." : "Deletions will appear here after you delete items from the Candidates view.";
  $("#hCount").textContent = v.length.toLocaleString();
  $("#hSize").textContent = fmtBig(v.filter((e) => e.ok).reduce((a, e) => a + e.size, 0));
}
const hFirst = () => { H.page = 1; renderHistory(); };
$("#hMode").addEventListener("change", hFirst);
$("#hQ").addEventListener("input", hFirst);
$("#hUser").addEventListener("change", hFirst);
$("#hSize2").addEventListener("change", (e) => {
  H.size = +e.target.value; H.page = 1;
  try { localStorage.setItem("hSize", String(H.size)); } catch (err) {}
  renderHistory();
});
$("#hPrev").addEventListener("click", () => { H.page--; renderHistory(); });
$("#hNext").addEventListener("click", () => { H.page++; renderHistory(); });
// Column sorting
$("#hTbl thead").addEventListener("click", (e) => {
  const th = e.target.closest("th.sortable");
  if (th?.dataset.sort) setHistSort(th.dataset.sort);
});
$("#hTbl thead").addEventListener("keydown", (e) => {
  const th = e.target.closest("th.sortable");
  if (th?.dataset.sort && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setHistSort(th.dataset.sort); }
});
// Keyboard navigation for table rows
$("#hRows").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); (tr.nextElementSibling || tr.parentElement.firstElementChild)?.focus(); }
  else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); (tr.previousElementSibling || tr.parentElement.lastElementChild)?.focus(); }
  else if (e.key === "r") { const btn = tr.querySelector("button.rr"); if (btn && !btn.disabled) btn.click(); }
});
$("#hCsv").addEventListener("click", () => {
  const q = (val) => { let s = String(val ?? ""); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return `"${s.replace(/"/g, '""')}"`; };
  const lines = [["When", "Title", "Year", "Library", "Requested by", "Size (GB)", "Mode", "Result"].join(",")].concat(histView().map((e) =>
    [new Date(e.time * 1000).toISOString(), e.title, e.year, e.library, (e.requested_by || []).join("; "), (e.size / 1073741824).toFixed(2), e.dry_run ? "dry run" : "live", e.ok ? "removed" : "not removed"].map(q).join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = "phlexerr-history.csv"; a.click(); URL.revokeObjectURL(a.href);
});

// ---------- CSV ----------
$("#btnCsv").addEventListener("click", () => {
  const cols = ["Title", "Year", "Type", "Library", "Collections", "IMDb", "Added", "Last viewed", "% viewed", "Requested by", "Plays 1y", "Plays total", "Size (GB)"];
  const q = (v) => { let s = String(v ?? ""); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return `"${s.replace(/"/g, '""')}"`; };
  const lines = [cols.join(",")].concat(S.view.map((i) => [i.title, i.year, i.type, i.library, i.collections.join("; "), i.rating ?? "", fmtDate(i.added_ts), fmtDate(i.last_viewed_ts),
    i.pct, i.requested_by.join("; "), i.plays_1y ?? "", i.plays_total, (i.size / 1073741824).toFixed(2)].map(q).join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = "phlexerr.csv"; a.click(); URL.revokeObjectURL(a.href);
});

// A missing poster just collapses (error events don't bubble, so listen in the capture phase; CSP forbids inline handlers)
document.addEventListener("error", (e) => {
  if (e.target.classList && e.target.classList.contains("poster")) e.target.classList.add("nopic");
}, true);

// ---------- text size ----------
// Desktop is shown at 120%, phones at 100%. (No user control any more; the old saved value is ignored.)
let zoom = 1;
function applyZoom() {
  const z = innerWidth < 760 ? 1 : 1.2;
  if (z === zoom && document.body.style.zoom) return;
  zoom = z;
  document.body.style.zoom = zoom;
  document.documentElement.style.setProperty("--z", zoom);   // viewport-sized things (popups) divide by this
  pop.style.setProperty("--z", zoom);                        // the popover lives outside <body>, so scale it separately
}
applyZoom();
window.addEventListener("resize", applyZoom);

// ---------- light / dark ----------
const darkNow = () => {
  const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};
function syncThemeBtn() { $("#btnTheme").textContent = darkNow() ? "☀" : "☾"; $("#btnTheme").title = darkNow() ? "Switch to light mode" : "Switch to dark mode"; }
$("#btnTheme").addEventListener("click", () => {
  const next = darkNow() ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("mp-theme", next); } catch (e) { /* ignore */ }
  syncThemeBtn();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncThemeBtn);
syncThemeBtn();

// ---------- mobile sort ----------
const SORTS = [["title", "Title"], ["rating", "IMDb"], ["added_ts", "Date added"], ["last_viewed_ts", "Last viewed"], ["pct", "% viewed"], ["plays_1y", "Plays (1y)"], ["size", "Size"]];
$("#mSort").innerHTML = SORTS.flatMap(([k, n]) => [[k, "asc", n + " ↑"], [k, "desc", n + " ↓"]]).map(([k, d, n]) => `<option value="${k}|${d}">${n}</option>`).join("");
$("#mSort").addEventListener("change", (e) => { const [k, d] = e.target.value.split("|"); S.sort = { key: k, dir: d }; applyFilters(); });

// ---------- collapsible filters (phones) ----------
$("#btnFilters").addEventListener("click", (e) => {
  const open = e.currentTarget.getAttribute("aria-expanded") !== "true";
  e.currentTarget.setAttribute("aria-expanded", String(open));
  e.currentTarget.textContent = open ? "Hide filters" : "Filters";
  filtersSection.hidden = !open;
  btnAdvanced?.setAttribute("aria-expanded", String(open));
});

// ---------- pick up refreshes that happened on their own ----------
setInterval(async () => {
  if (document.hidden || $("#btnRefresh").disabled) return;
  try {
    const s = await api("/api/status");
    if (s.state === "running") pollStatus();
    else if (s.finished && S.generated && s.finished > S.generated + 5) await loadItems();
  } catch (e) { /* offline or signed out: ignore */ }
}, 60000);

// Hide what can't work yet because an optional service isn't connected (CSS keys off these body classes).
$("#aSetupBtn").addEventListener("click", () => openSettings());
function applyServices() {
  const v = S.cfg.services || {};
  document.body.classList.toggle("no-seerr", !v.seerr);
  document.body.classList.toggle("no-tautulli", !v.tautulli);
  document.body.classList.toggle("no-arr", !v.radarr && !v.sonarr);
  document.body.classList.toggle("has-leaving", !!S.cfg.leaving);
  const pw = $("#pwWarn"); if (pw) pw.hidden = !S.cfg.default_password;
}

// ---------- boot ----------
(async function boot() {
  try {
    S.cfg = await api("/api/config");
    applyServices();
    const chip = $("#modeChip");
    chip.textContent = S.cfg.dry_run ? "DRY RUN - nothing will be deleted" : "";
    chip.className = "chip dry";
    chip.hidden = !S.cfg.dry_run;
    if (!S.cfg.auth) $("#logoutForm").hidden = true;
    // Load saved filters before fetching items
    loadFiltersFromStorage();
    renderFilterPills();
    await loadItems();
    const st = await api("/api/status");
    if (viewFromHash() !== "main") showView(viewFromHash());
    if (!S.cfg.services.plex) openSettings();            // first run: ask for the Plex details
    else if (st.state === "running") pollStatus();
    else if (!S.generated) startRefresh();
  } catch (e) { if (e.message !== "Signed out") showErr(e.message); }
})();


// ---------- protection rules ----------
const RULE_HELP = {
  actor: 'Exact name, or use * (e.g. "Tom *" or "*Hanks"). Matches anyone in the cast.',
  director: "Exact name, or use * wildcards.",
  studio: 'Exact studio name, or use * (e.g. "Marvel*").',
  collection: 'Collection name ("Alien" also matches "Alien Collection"), or use * (e.g. "*Trilogy"). Uses Plex collections, falling back to Radarr\'s.',
  library: "Protects everything in the chosen Plex library.",
  title: "A word or phrase matches anywhere in the title. With * or ? it must match the whole title.",
};
let ruleTimer = null;
function ruleFields() {
  const lib = $("#rType").value === "library";
  return { type: $("#rType").value, pattern: lib ? $("#rLib").value : $("#rPattern").value, top: $("#rTop").value, note: $("#rNote").value };
}
function syncRuleForm() {
  const t = $("#rType").value;
  $("#rTopWrap").hidden = t !== "actor";
  $("#rLibWrap").hidden = t !== "library";
  $("#rPatWrap").hidden = t === "library";
  $("#rHelp").textContent = RULE_HELP[t];
}
async function previewRule() {
  const box = $("#rPreview");
  if (!ruleFields().pattern.trim()) { box.textContent = ""; return; }
  try {
    const d = await post("/api/rules/preview", ruleFields());
    box.innerHTML = d.count ? `Would protect <b>${d.count}</b> item${d.count === 1 ? "" : "s"} (<b>${fmtBig(d.total_size)}</b>): <span class="dim">${esc(d.titles.slice(0, 8).join(", "))}${d.count > 8 ? "…" : ""}</span>` : `<span class="dim">Matches nothing in the current report.</span>`;
  } catch (e) { box.innerHTML = `<span class="errtxt">${esc(e.message)}</span>`; }
}
async function loadRules() {
  syncRuleForm();
  const d = await api("/api/rules");
  const el = $("#rList");
  $("#rEmpty").hidden = d.rules.length > 0;
  const sm = d.summary, pct = sm.total_size ? Math.round(sm.size / sm.total_size * 100) : 0;
  const pc = (big, label, sub) => `<div class="stat"><div class="big">${big}</div><div class="lbl">${label}</div><div class="sub">${sub}</div></div>`;
  $("#pStats").innerHTML = d.have_report
    ? pc(fmtBig(sm.size), "Disk space protected", `${sm.items.toLocaleString()} item${sm.items === 1 ? "" : "s"} · ${pct}% of your library (${fmtBig(sm.total_size)})`)
      + pc(fmtBig(sm.unwatched_size), "Protected but never watched", `${sm.unwatched_items.toLocaleString()} item${sm.unwatched_items === 1 ? "" : "s"} · space you're holding on to`)
      + pc(`${sm.rule_items.toLocaleString()} / ${sm.keep_items.toLocaleString()}`, "Rules / Marked Keep", `${fmtBig(sm.rule_size)} by enabled rules · ${fmtBig(sm.keep_size)} marked Keep (items can be both)`)
    : `<div class="stat"><div class="sub">Run a refresh to calculate protected disk space.</div></div>`;
  P.data = d;
  renderRules();
  try {
    const f = await api("/api/facets"), cur = $("#rLib").value;
    $("#rLib").innerHTML = f.libraries.length ? f.libraries.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("") : `<option value="">(refresh first)</option>`;
    if (f.libraries.includes(cur)) $("#rLib").value = cur;
  } catch (e) { /* picker stays empty */ }
}
const P = { data: null, key: "created", dir: "asc", editing: null };
function renderRules() {
  const d = P.data, el = $("#rList");
  const m = P.dir === "asc" ? 1 : -1;
  const val = (r) => P.key === "type" ? (d.types[r.type] || r.type).toLowerCase() : P.key === "pattern" ? r.pattern.toLowerCase()
    : P.key === "enabled" ? (r.enabled ? 1 : 0) : (r[P.key] || 0);
  const rules = [...d.rules].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * m; });
  document.querySelectorAll("#rTbl th[data-psort]").forEach((th) => {
    const isActive = th.dataset.psort === P.key;
    th.querySelector(".arrow").textContent = isActive ? (P.dir === "asc" ? "▲" : "▼") : "";
    th.setAttribute("aria-sort", isActive ? (P.dir === "asc" ? "ascending" : "descending") : "none");
  });
  el.innerHTML = rules.map((r) => `<tr data-rid="${esc(r.id)}" class="${r.enabled ? "" : "off"}${P.editing === r.id ? " editing" : ""}" tabindex="0">
    <td><label class="switch"><input type="checkbox" class="rOn" ${r.enabled ? "checked" : ""} aria-label="Enabled"></label></td>
    <td><span class="badge">${esc(d.types[r.type] || r.type)}</span></td>
    <td><b>${esc(r.pattern)}</b>${r.top ? ` <span class="dim">top ${r.top} billed</span>` : ""}${r.note ? `<div class="sub">${esc(r.note)}</div>` : ""}</td>
    <td class="num mono">${d.have_report ? r.matches : "?"}</td>
    <td class="num mono">${d.have_report ? fmtBig(r.size) : "?"}</td>
    <td class="num"><button class="btn small rPrev">Preview</button> <button class="btn small rEdit">Edit</button> <button class="btn small rDel">Remove</button></td></tr>`).join("");
}
function editRule(id) {
  const rule = P.data?.rules.find((r) => r.id === id);
  if (!rule) return;
  P.editing = id;
  $("#rType").value = rule.type;
  syncRuleForm();
  if (rule.type === "library") { $("#rLib").value = rule.pattern; }
  else { $("#rPattern").value = rule.pattern; }
  $("#rTop").value = rule.top || "";
  $("#rNote").value = rule.note || "";
  $("#rForm button[type=submit]").textContent = "Update rule";
  $("#rCancel").hidden = false;
  $("#rErr").textContent = "";
  renderRules();
  $("#rPattern").focus();
}
function cancelEdit() {
  P.editing = null;
  $("#rPattern").value = "";
  $("#rNote").value = "";
  $("#rTop").value = "";
  $("#rPreview").textContent = "";
  $("#rErr").textContent = "";
  $("#rForm button[type=submit]").textContent = "Add rule";
  $("#rCancel").hidden = true;
  renderRules();
}
$("#rCancel").addEventListener("click", cancelEdit);
// ---------- Protect: export / import ----------
const IMP = { data: null };
function impHide() { IMP.data = null; $("#rImp").hidden = true; $("#rImp").innerHTML = ""; $("#rFile").value = ""; }
$("#rImport").addEventListener("click", () => $("#rFile").click());
$("#rFile").addEventListener("change", async () => {
  const f = $("#rFile").files[0]; if (!f) return;
  const box = $("#rImp");
  try {
    if (f.size > 2 * 1024 * 1024) throw new Error("that file is too large");
    IMP.data = JSON.parse(await f.text());
    const j = await post("/api/rules/import", { data: IMP.data, mode: "merge", dry: true });
    box.hidden = false; box.className = "notice info";
    box.innerHTML = `<b>${esc(f.name)}</b>: adding would bring in <b>${j.added}</b> new rule${j.added === 1 ? "" : "s"} and <b>${j.keep_added}</b> Keep mark${j.keep_added === 1 ? "" : "s"}` +
      `${j.duplicates ? `, skipping ${j.duplicates} you already have` : ""}${j.invalid ? `, ignoring ${j.invalid} invalid` : ""}.
      <div class="rtools"><button class="btn small primary" type="button" id="impMerge">Add to my rules</button>
      <button class="btn small" type="button" id="impReplace">Replace all my rules</button>
      <button class="btn small" type="button" id="impCancel">Cancel</button></div>`;
    const run = async (mode) => {
      if (mode === "replace" && !window.confirm("Replace ALL your current protection rules and Keep marks with the ones in this file?")) return;
      try {
        const r = await post("/api/rules/import", { data: IMP.data, mode });
        toast(`Imported: ${r.added} rule${r.added === 1 ? "" : "s"}, ${r.keep_added} Keep mark${r.keep_added === 1 ? "" : "s"}`);
        impHide(); await loadRules(); loadItems();
      } catch (e) { box.className = "notice warn"; box.textContent = "Import failed: " + e.message; }
    };
    $("#impMerge").onclick = () => run("merge");
    $("#impReplace").onclick = () => run("replace");
    $("#impCancel").onclick = impHide;
  } catch (e) { impHide(); box.hidden = false; box.className = "notice warn"; box.textContent = "Couldn't read that file: " + e.message; }
});

function checkDuplicate() {
  const f = ruleFields();
  if (!f.pattern.trim()) return null;
  return P.data?.rules.find((r) => r.type === f.type && r.pattern.toLowerCase() === f.pattern.toLowerCase() && r.id !== P.editing);
}
async function toggleRulePreview(tr) {
  const nxt = tr.nextElementSibling;
  if (nxt && nxt.classList.contains("rPrevRow")) { nxt.remove(); return; }
  const row = document.createElement("tr");
  row.className = "rPrevRow";
  row.innerHTML = `<td colspan="6"><div class="rPrevBox muted">Loading…</div></td>`;
  tr.after(row);
  const box = row.querySelector(".rPrevBox");
  try {
    const d = await post("/api/rules/preview", { id: tr.dataset.rid });
    box.innerHTML = d.count
      ? `<b>${d.count}</b> item${d.count === 1 ? "" : "s"} protected · <b>${fmtBig(d.total_size)}</b> on disk${tr.classList.contains("off") ? " <span class='dim'>(rule is currently off)</span>" : ""}:<ul>${d.titles.map((t, k) => `<li>${esc(t)} <span class="dim">${fmtSize(d.sizes[k])}</span></li>`).join("")}</ul>${d.count > d.titles.length ? `<div class="dim">…and ${d.count - d.titles.length} more</div>` : ""}`
      : `<span class="dim">Matches nothing in the current report.</span>`;
  } catch (e) { box.innerHTML = `<span class="errtxt">${esc(e.message)}</span>`; }
}
async function ruleAction(body) {
  try { await post("/api/rules", body); $("#rErr").textContent = ""; await loadRules(); await loadItems(); }
  catch (e) { $("#rErr").textContent = e.message; $("#rPreview").textContent = ""; }
}
$("#rType").addEventListener("change", () => { syncRuleForm(); previewRule(); });
$("#rLib").addEventListener("change", previewRule);
["#rPattern", "#rTop"].forEach((id) => $(id).addEventListener("input", () => { clearTimeout(ruleTimer); ruleTimer = setTimeout(previewRule, 250); }));
$("#rForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const dup = checkDuplicate();
  if (dup && !P.editing) {
    $("#rErr").textContent = `A rule for "${dup.pattern}" (${dup.type}) already exists.`;
    return;
  }
  if (P.editing) {
    await ruleAction({ update: P.editing, ...ruleFields() });
  } else {
    await ruleAction({ add: ruleFields() });
  }
  if (!$("#rErr").textContent) { cancelEdit(); }
});
$("#rTbl thead").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-psort]");
  if (!th || !P.data) return;
  const k = th.dataset.psort;
  P.dir = P.key === k ? (P.dir === "asc" ? "desc" : "asc") : (["matches", "size"].includes(k) ? "desc" : "asc");
  P.key = k;
  renderRules();
});
$("#rList").addEventListener("click", (e) => {
  const id = e.target.closest("tr")?.dataset.rid;
  if (id && e.target.classList.contains("rDel")) { cancelEdit(); ruleAction({ delete: id }); }
  if (id && e.target.classList.contains("rEdit")) editRule(id);
  if (id && e.target.classList.contains("rPrev")) toggleRulePreview(e.target.closest("tr"));
});
// Keyboard navigation for Protection table
$("#rTbl thead").addEventListener("keydown", (e) => {
  const th = e.target.closest("th[data-psort]");
  if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); th.click(); }
});
$("#rList").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); (tr.nextElementSibling || tr.parentElement.firstElementChild)?.focus(); }
  if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); (tr.previousElementSibling || tr.parentElement.lastElementChild)?.focus(); }
  if (e.key === "e") { e.preventDefault(); editRule(tr.dataset.rid); }
});
// Escape to cancel edit
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && P.editing) { cancelEdit(); }
});
$("#rList").addEventListener("change", (e) => {
  const id = e.target.closest("tr")?.dataset.rid;
  if (id && e.target.classList.contains("rOn")) ruleAction({ update: id, enabled: e.target.checked });
});


// ---------- home rows ----------
const W = { crit: [], editing: null, timer: null, csvs: [], keepCsv: false, manual: [] };
function rowSources() {
  const out = $("#wListUrl").value.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean).map((list) => ({ type: /mdblist\.com/i.test(list) ? "mdblist" : "imdb", list }));
  if (W.csvs.length) out.push({ type: "csv", csvs: W.csvs });
  return out;
}
function rowDraft() {
  const num = (id) => $(id).value;
  return { manual_order: W.manual, sources: rowSources(), keep_csv: !!(W.editing && W.keepCsv && !W.csvs.length), name: $("#wName").value, media: $("#wMedia").value, sort: $("#wSort").value, sort_dir: $("#wDir").value, limit: num("#wLimit"), criteria: W.crit,
           min_rating: num("#wRating"), added_days: num("#wDays"), unwatched: $("#wUnwatched").checked, pin: $("#wPin").checked, shared: $("#wShared").checked, auto: $("#wAuto").checked };
}
function rowNote(msg, bad) {
  const el = $("#rNotice"); el.className = "notice " + (bad ? "err" : "info"); el.textContent = msg; el.hidden = false; el.scrollIntoView({ block: "nearest" });
}
function renderCrit() {
  $("#wCrit").innerHTML = W.crit.map((c, n) => `<span class="badge">${esc(c.type)}: ${esc(c.pattern)}${c.top ? ` (top ${c.top})` : ""}<button type="button" data-n="${n}" aria-label="Remove">×</button></span>`).join("");
}
async function previewRow() {
  const box = $("#wPreview");
  if (!$("#wName").value.trim() && !W.crit.length && !$("#wListUrl").value.trim() && !W.csvs.length) { box.textContent = ""; return; }
  try {
    const d = await post("/api/rows", { preview: { ...rowDraft(), name: $("#wName").value.trim() || "preview" } });
    const lt = d.list_total != null ? ` (the list has ${d.list_total}; the rest aren't in Plex)` : "";
    box.innerHTML = d.count ? `Matches <b>${d.count}</b> item${d.count === 1 ? "" : "s"}${lt}${d.shown < d.count ? ` (row shows the first ${d.shown})` : ""}: <span class="dim">${esc(d.titles.join(", "))}${d.shown > 12 ? "…" : ""}</span>` : `<span class="dim">Matches nothing in the current report.</span>`;
  } catch (e) { box.innerHTML = `<span class="errtxt">${esc(e.message)}</span>`; }
}
const schedPreview = () => { clearTimeout(W.timer); W.timer = setTimeout(previewRow, 300); };
async function loadRows() {
  $("#wTopWrap").hidden = $("#wType").value !== "actor";
  const d = await api("/api/rows");
  if (!$("#wSort").options.length) $("#wSort").innerHTML = Object.entries(d.sorts).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  const tb = d.throwback;
  syncArrange();
  $("#wEmpty").hidden = d.rows.length > 0 || !!tb;
  const tbs = tb.state || {};
  const tbStatus = !tb.enabled ? `<span class="dim">off</span> · turn it on in Settings`
    : tbs.error ? `<span class="errtxt" title="${esc(tbs.error)}">last sync failed</span>`
    : tbs.time ? `<span class="ok">synced ${esc(ago(tbs.time))}</span>${tb.pin ? " · on Home" : ""}` : `<span class="dim">not published yet</span>`;
  const ord = d.order, pos = (id) => ord.indexOf(id);
  const arrows = (id) => `<td class="nowrap"><button class="btn small wUp" ${pos(id) <= 0 ? "disabled" : ""} aria-label="Move up" title="Show higher on Home">↑</button><button class="btn small wDown" ${pos(id) >= ord.length - 1 ? "disabled" : ""} aria-label="Move down" title="Show lower on Home">↓</button></td>`;
  const tbRow = `<tr data-tb="1" data-rid="__tb__">${arrows("__tb__")}<td><b>${esc(tb.name)}</b><div class="sub">built-in · movies released on this day · refreshes daily</div></td>
      <td>Anniversary of release date (set the timeframe, rating, genres in Settings)</td><td class="num mono">${tb.count ?? (tb.enabled ? "?" : "–")}</td><td>${tbStatus}</td>
      <td class="num">${tb.enabled ? `<button class="btn small tbPub">Publish to Plex</button> ` : ""}<button class="btn small tbSet">Settings</button></td></tr>`;
  const DEF_DIR = { title: "asc" };
  const byId = Object.fromEntries(d.rows.map((r) => [r.id, r]));
  $("#wList").innerHTML = ord.map((id) => id === "__tb__" ? tbRow : wRowHtml(byId[id])).join("");
  function wRowHtml(r) {
    const p = r.published;
    const st = !p ? `<span class="dim">not published yet</span>` : p.error ? `<span class="errtxt">last sync failed</span><div class="sub errtxt">${esc(p.error)}</div>` : `<span class="ok">synced ${esc(ago(p.time))}</span>${r.pin ? (r.shared === false ? " · on your Home" : " · on everyone's Home") : ""}`;
    const sk = r.sort === "newest" ? "added" : r.sort, sd = r.sort_dir || (r.sort === "newest" ? "desc" : DEF_DIR[sk] || "desc");
    return `<tr data-rid="${esc(r.id)}" tabindex="0">${arrows(r.id)}
      <td><b>${esc(r.name)}</b><div class="sub">${r.auto ? "auto-sync" : "manual"} · ${esc(d.sorts[sk] || sk)} ${sd === "asc" ? "↑" : "↓"} · max ${r.limit}</div></td>
      <td>${esc(r.summary)}${r.list_note ? `<div class="sub">${esc(r.list_note)}</div>` : ""}</td><td class="num mono">${d.have_report ? r.shown : "?"}</td><td>${st}</td>
      <td class="num"><button class="btn small wPub">Publish to Plex</button> ${(r.sources || []).length ? `<button class="btn small wMiss">Missing…</button> ` : ""}<button class="btn small wEdit">Edit</button> <button class="btn small wDel">Remove</button></td></tr>`;
  }
  W.rows = d.rows; W.order = d.order;
  if (d.jobs?.length && $("#wProg").hidden) trackJob(d.jobs[0]);
}
// ---- titles on a list that aren't in Plex: check Seerr, then request ----
const M = { row: null, items: [], maxBatch: 50, busy: false };
const ST_TXT = { none: "Not requested", requested: "Already requested", available: "Already available", unmatched: "Can't match", error: "Error", pending: "Checking…" };
function mSelected() { return M.items.filter((m) => m.sel && m.status === "none").map((m) => m.imdb_id); }
const mDate = (m) => m.released || m.seerr_released || (m.year ? String(m.year) : "");
const mRating = (m) => (m.rating != null ? { v: m.rating, src: m.rating_src || "IMDb" } : m.seerr_rating != null ? { v: m.seerr_rating, src: "TMDB" } : null);
function mView() {
  const q = $("#mQ").value.trim().toLowerCase(), st = $("#mStatus").value, ty = $("#mType").value, an = $("#mAnim").value;
  const min = $("#mRate").value === "" ? null : parseFloat($("#mRate").value);
  const key = $("#mvSort").value, m = $("#mvDir").value === "asc" ? 1 : -1;
  const list = M.items.map((it, n) => ({ it, n })).filter(({ it }) => {
    if (q && !`${it.title || ""} ${it.year || ""}`.toLowerCase().includes(q)) return false;
    if (st && (it.status || "pending") !== st) return false;
    if (ty && it.type !== ty) return false;
    if (an && !!it.animation !== (an === "only")) return false;
    if (min !== null && !((mRating(it)?.v ?? -1) >= min)) return false;
    return true;
  });
  const val = ({ it, n }) => key === "released" ? mDate(it) : key === "rating" ? (mRating(it)?.v ?? null) : key === "title" ? (it.title || "").toLowerCase() : n;
  // titles with no value for the chosen sort always sit at the bottom
  const has = list.filter((x) => val(x) !== "" && val(x) !== null), none = list.filter((x) => val(x) === "" || val(x) === null);
  has.sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : a.n - b.n) * m);
  return [...has, ...none].map((x) => x.it);
}
function renderMissing() {
  const view = mView();
  $("#mRows").innerHTML = view.map((it) => {
    const ok = it.status === "none";
    const st = it.status || "pending";
    const link = `<a href="https://www.imdb.com/title/${esc(it.imdb_id)}/" target="_blank" rel="noopener noreferrer">IMDb ↗</a>`;
    const r = mRating(it);
    return `<tr class="${ok ? "" : "dis"}"><td><input type="checkbox" class="mSel" data-id="${esc(it.imdb_id)}" ${ok ? "" : "disabled"} ${it.sel ? "checked" : ""} aria-label="Select"></td>
      <td><div class="mtitle">${it.poster ? `<img class="mthumb" src="/api/tmdbimg/${esc(it.poster.slice(1))}" alt="" loading="lazy" width="30" height="45">` : `<span class="mthumb"></span>`}<div>${esc(it.title || it.imdb_id)} ${it.year ? `<span class="dim">(${it.year})</span>` : ""} ${it.type ? `<span class="badge">${it.type === "movie" ? "Movie" : "Show"}</span>` : ""} ${it.animation ? `<span class="badge anim" title="Animation" aria-label="Animation">${ANIM_ICON}</span>` : ""} ${link}${it.detail ? `<div class="sub">${esc(it.detail)}</div>` : ""}</div></div></td>
      <td class="mono">${esc(mDate(it)) || `<span class="dim">–</span>`}</td>
      <td class="num mono">${r ? `<span title="${esc(r.src)} rating">${r.v.toFixed(1)}</span> <span class="dim">${esc(r.src)}</span>` : `<span class="dim">–</span>`}</td>
      <td class="st ${st === "none" ? "ok" : st === "error" ? "bad" : ""}">${ST_TXT[st] || st}</td></tr>`;
  }).join("");
  $("#mEmpty").hidden = view.length > 0 || !M.items.length;
  syncMissingSel();
}
function syncMissingSel() {
  const ids = mSelected(), n = ids.length;
  const shown = new Set([...document.querySelectorAll("#mRows input.mSel:checked")].map((c) => c.dataset.id));
  const hidden = ids.filter((i) => !shown.has(i)).length;
  $("#mSel").textContent = n ? `${n} selected${hidden ? ` (${hidden} hidden by the filters)` : ""}${n > M.maxBatch ? ` - request at most ${M.maxBatch} at a time` : ""}` : "Nothing selected";
  $("#mRequest").disabled = !n || n > M.maxBatch || M.busy;
  $("#mRequest").textContent = n ? `Request ${n} in Seerr` : "Request in Seerr";
}
async function openMissing(id) {
  M.row = id; M.items = []; M.busy = false;
  $("#mResult").innerHTML = ""; $("#mProg").hidden = true; $("#mNotice").hidden = true; $("#mAll").checked = false; $("#mRows").innerHTML = ""; $("#mCheck").textContent = "";
  $("#mQ").value = ""; $("#mStatus").value = ""; $("#mType").value = ""; $("#mAnim").value = ""; $("#mRate").value = ""; $("#mvSort").value = "released"; $("#mvDir").value = "desc"; $("#mEmpty").hidden = true;
  const row = W.rows.find((x) => x.id === id);
  $("#mTitle").textContent = `Not in Plex yet: ${row.name}`;
  $("#mSummary").textContent = "Loading the list…";
  $("#dlgMissing").showModal();
  let d;
  try { d = await post("/api/rows", { missing: id }); }
  catch (e) { $("#mSummary").textContent = ""; $("#mNotice").className = "notice err"; $("#mNotice").textContent = e.message; $("#mNotice").hidden = false; return; }
  M.maxBatch = d.max_batch; M.items = d.items;
  $("#mSummary").textContent = `${d.total} titles on the list; ${d.in_plex} already in Plex; ${d.items.length} not.`;
  if (d.notes?.length) { $("#mNotice").className = "notice warn"; $("#mNotice").textContent = d.notes.join("; "); $("#mNotice").hidden = false; }
  if (!d.seerr) { $("#mNotice").className = "notice warn"; $("#mNotice").textContent = "Seerr isn't connected, so you can see what's missing but can't request it from here. Add Seerr's URL and API key in Settings to turn requesting on."; $("#mNotice").hidden = false; renderMissing(); return; }
  renderMissing();
  for (let i = 0; i < M.items.length && $("#dlgMissing").open && M.row === id; i += 20) {
    $("#mCheck").textContent = `Checking Seerr ${Math.min(i + 20, M.items.length)} of ${M.items.length}…`;
    try {
      const r = await post("/api/rows", { seerr_check: id, ids: M.items.slice(i, i + 20).map((x) => x.imdb_id) });
      for (const x of r.results) Object.assign(M.items.find((m) => m.imdb_id === x.imdb_id) || {}, x);
    } catch (e) { M.items.slice(i, i + 20).forEach((m) => { m.status = "error"; m.detail = e.message; }); }
    renderMissing();
  }
  $("#mCheck").textContent = `${M.items.filter((x) => x.status === "none").length} can be requested`;
}
$("#mRows").addEventListener("change", (e) => { if (e.target.classList.contains("mSel")) { M.items.find((m) => m.imdb_id === e.target.dataset.id).sel = e.target.checked; syncMissingSel(); } });
$("#mAll").addEventListener("change", (e) => { const shown = new Set(mView().map((m) => m.imdb_id)); M.items.forEach((m) => { if (shown.has(m.imdb_id) && m.status === "none") m.sel = e.target.checked; }); renderMissing(); });
["#mQ", "#mStatus", "#mType", "#mAnim", "#mRate", "#mvSort", "#mvDir"].forEach((id) => $(id).addEventListener("input", renderMissing));
$("#mClose").addEventListener("click", () => { M.row = null; $("#dlgMissing").close(); });
$("#mRequest").addEventListener("click", async () => {
  const ids = mSelected();
  if (!ids.length || M.busy) return;
  M.busy = true; syncMissingSel();
  $("#mProg").hidden = false; $("#mProgStage").textContent = "Starting"; $("#mProgBar").value = 1; $("#mProgPct").textContent = "";
  let j;
  try { j = (await post("/api/rows", { seerr_request: M.row, ids })).job; }
  catch (e) { M.busy = false; $("#mProg").hidden = true; $("#mNotice").className = "notice err"; $("#mNotice").textContent = e.message; $("#mNotice").hidden = false; syncMissingSel(); return; }
  for (;;) {
    await new Promise((r) => setTimeout(r, 500));
    try { j = (await api("/api/rows/job/" + j.id)).job; } catch (e) { break; }
    $("#mProgStage").textContent = `${j.stage} · ${j.seconds}s`; $("#mProgBar").value = j.pct; $("#mProgPct").textContent = j.pct + "%";
    if (j.state !== "running") break;
  }
  M.busy = false; $("#mProg").hidden = true;
  if (j.state === "error") { $("#mNotice").className = "notice err"; $("#mNotice").textContent = j.error || "The request failed."; $("#mNotice").hidden = false; syncMissingSel(); return; }
  const done = j.results.filter((r) => r.ok), skipped = j.results.filter((r) => !r.ok);
  $("#mResult").innerHTML = `<div class="notice info"><b>${done.length} requested in Seerr.</b> Seerr passes them on to Radarr/Sonarr (it may need approval first).</div>`
    + (skipped.length ? `<ul class="res-list">${skipped.map((r) => `<li class="bad">${esc(r.title)}: ${esc(r.detail)}</li>`).join("")}</ul>` : "")
    + (done.length ? `<ul class="res-list">${done.map((r) => `<li class="ok">${esc(r.title)}</li>`).join("")}</ul>` : "");
  // re-check what we just requested so the list shows their new status
  const redo = j.results.map((r) => r.imdb_id);
  try { const r = await post("/api/rows", { seerr_check: M.row, ids: redo.slice(0, 25) }); for (const x of r.results) Object.assign(M.items.find((m) => m.imdb_id === x.imdb_id) || {}, x, { sel: false }); } catch (e) { /* the list stays as it was */ }
  renderMissing();
});

// ---- manual order editor ----
const O = { items: [], drag: null };
function syncArrange() {
  const manual = $("#wSort").value === "manual";
  $("#wArrangeWrap").hidden = !manual;
  $("#wArrange").textContent = W.manual.length ? `Arrange titles… (${W.manual.length} placed)` : "Arrange titles…";
  $("#wDir").closest("label").hidden = manual || $("#wSort").value === "shuffle";
}
function renderOrder() {
  const q = $("#oFilter").value.trim().toLowerCase();
  $("#oList").innerHTML = O.items.map((it, n) => `<li draggable="true" data-id="${esc(it.id)}" ${q && !`${it.title} ${it.year ?? ""}`.toLowerCase().includes(q) ? "hidden" : ""}>
    <span class="grip">⋮⋮</span><input class="opos" type="number" min="1" max="${O.items.length}" value="${n + 1}" aria-label="Position">
    <span class="t">${esc(it.title)} <span class="dim">${it.year ? `(${it.year})` : ""}</span></span>
    <button class="btn small oUp" type="button" aria-label="Move up">↑</button><button class="btn small oDown" type="button" aria-label="Move down">↓</button></li>`).join("");
  $("#oCount").textContent = `${O.items.length} titles`;
}
function moveTo(id, pos) {
  const from = O.items.findIndex((x) => x.id === id);
  if (from < 0) return;
  const to = Math.max(0, Math.min(O.items.length - 1, pos));
  const [it] = O.items.splice(from, 1);
  O.items.splice(to, 0, it);
  renderOrder();
}
async function openOrder() {
  try {
    const d = await post("/api/rows", { members: { ...rowDraft(), sort: "manual" } });
    O.items = d.items;
  } catch (e) { rowNote(e.message, true); return; }
  $("#oFilter").value = ""; renderOrder(); $("#dlgOrder").showModal();
}
$("#wArrange").addEventListener("click", openOrder);
$("#wSort").addEventListener("change", syncArrange);
$("#oFilter").addEventListener("input", renderOrder);
$("#oCancel").addEventListener("click", () => $("#dlgOrder").close());
$("#oSave").addEventListener("click", () => {
  W.manual = O.items.map((x) => x.id); syncArrange(); $("#dlgOrder").close(); schedPreview();
  rowNote(`Order set for ${W.manual.length} titles. Press “Save row”, then Publish.`, false);
});
$("#oByRelease").addEventListener("click", () => { O.items.sort((a, b) => (a.released || String(a.year || "9999")).localeCompare(b.released || String(b.year || "9999"))); renderOrder(); });
$("#oByTitle").addEventListener("click", () => { O.items.sort((a, b) => a.title.localeCompare(b.title)); renderOrder(); });
$("#oReverse").addEventListener("click", () => { O.items.reverse(); renderOrder(); });
$("#oList").addEventListener("click", (e) => {
  const id = e.target.closest("li")?.dataset.id;
  if (!id) return;
  const n = O.items.findIndex((x) => x.id === id);
  if (e.target.classList.contains("oUp")) moveTo(id, n - 1);
  else if (e.target.classList.contains("oDown")) moveTo(id, n + 1);
});
$("#oList").addEventListener("change", (e) => {
  const id = e.target.closest("li")?.dataset.id;
  if (id && e.target.classList.contains("opos")) moveTo(id, (parseInt(e.target.value, 10) || 1) - 1);
});
$("#oList").addEventListener("dragstart", (e) => { const li = e.target.closest("li"); if (!li) return; O.drag = li.dataset.id; li.classList.add("drag"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", O.drag); });
$("#oList").addEventListener("dragend", () => { O.drag = null; document.querySelectorAll("#oList li").forEach((l) => l.classList.remove("drag", "over")); });
$("#oList").addEventListener("dragover", (e) => { if (!O.drag) return; e.preventDefault(); document.querySelectorAll("#oList li.over").forEach((l) => l.classList.remove("over")); e.target.closest("li")?.classList.add("over"); });
$("#oList").addEventListener("drop", (e) => {
  e.preventDefault();
  const li = e.target.closest("li");
  if (!li || !O.drag || li.dataset.id === O.drag) return;
  moveTo(O.drag, O.items.findIndex((x) => x.id === li.dataset.id));
});

let jobTimer = null;
function showJob(j) {
  $("#wProg").hidden = false;
  $("#wProgName").textContent = `Publishing “${j.name}”`;
  $("#wProgStage").textContent = `${j.stage} · ${j.seconds}s`;
  $("#wProgBar").value = j.pct; $("#wProgPct").textContent = j.pct + "%";
}
function trackJob(j) {
  clearTimeout(jobTimer);
  document.querySelectorAll("#wList .wPub").forEach((b) => (b.disabled = true));
  showJob(j);
  const tick = async () => {
    let cur;
    try { cur = (await api("/api/rows/job/" + j.id)).job; }
    catch (e) { $("#wProg").hidden = true; rowNote("Lost track of the publish job (the app may have restarted). Check the table for its status.", true); loadRows(); return; }
    if (cur.state === "running") { showJob(cur); jobTimer = setTimeout(tick, 600); return; }
    $("#wProg").hidden = true;
    if (cur.state === "done") rowNote(`Published “${cur.name}” in ${cur.seconds}s: ` + cur.results.map((r) => `${r.action}${r.items ? ` (${r.items} items)` : ""}`).join(", ") + ". Check Plex's Home screen.", false);
    else rowNote(`Publishing “${cur.name}” failed after ${cur.seconds}s: ${cur.error}`, true);
    loadRows();
  };
  jobTimer = setTimeout(tick, 400);
}
async function rowAction(body, busy) {
  if (busy) busy.disabled = true;
  try { const d = await post("/api/rows", body); $("#wErr").textContent = ""; await loadRows(); return d; }
  catch (e) { $("#wErr").textContent = e.message; rowNote(e.message, true); }
  finally { if (busy) busy.disabled = false; }
}
function resetRowForm() {
  W.editing = null; W.crit = []; W.csvs = []; W.keepCsv = false; W.manual = []; syncArrange(); $("#wListUrl").value = ""; $("#wCsv").value = ""; $("#wListInfo").textContent = ""; renderCrit();
  ["#wName", "#wPattern", "#wTop", "#wRating", "#wDays"].forEach((i) => ($(i).value = ""));
  $("#wLimit").value = 50; $("#wMedia").value = "both"; $("#wUnwatched").checked = false; $("#wPin").checked = true; $("#wShared").checked = true; $("#wAuto").checked = true; $("#wDir").value = "desc";
  $("#wSave").textContent = "Save row"; $("#wCancel").hidden = true; $("#wPreview").textContent = ""; $("#wErr").textContent = "";
}
$("#wCancel").addEventListener("click", resetRowForm);
$("#wCsv").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  W.csvs = await Promise.all(files.map((f) => f.text()));
  if (W.csvs.length) W.keepCsv = false;
  $("#wListInfo").textContent = files.length ? `${files.length} file${files.length === 1 ? "" : "s"} loaded` : (W.keepCsv ? $("#wListInfo").textContent : "");
  previewRow();
});
$("#wListUrl").addEventListener("input", schedPreview);
$("#wType").addEventListener("change", () => { $("#wTopWrap").hidden = $("#wType").value !== "actor"; });
$("#wAddCrit").addEventListener("click", () => {
  const pattern = $("#wPattern").value.trim();
  if (!pattern) return;
  W.crit.push({ type: $("#wType").value, pattern, top: $("#wType").value === "actor" ? $("#wTop").value : "" });
  $("#wPattern").value = ""; $("#wTop").value = ""; renderCrit(); previewRow();
});
$("#wPattern").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#wAddCrit").click(); } });
$("#wCrit").addEventListener("click", (e) => { const n = e.target.dataset.n; if (n !== undefined) { W.crit.splice(+n, 1); renderCrit(); previewRow(); } });
["#wName", "#wMedia", "#wSort", "#wDir", "#wLimit", "#wRating", "#wDays", "#wUnwatched"].forEach((id) => $(id).addEventListener("input", schedPreview));
$("#wSave").addEventListener("click", async () => {
  const d = await rowAction(W.editing ? { update: W.editing, ...rowDraft() } : { add: rowDraft() }, $("#wSave"));
  if (d) { resetRowForm(); rowNote('Saved. Press “Publish to Plex” to create the collection on your Home screen.', false); }
});
$("#wList").addEventListener("click", async (e) => {
  if (e.target.classList.contains("tbSet")) { openSettings(); return; }
  if (e.target.classList.contains("tbPub")) {
    e.target.disabled = true; e.target.textContent = "Publishing…";
    try { await post("/api/throwback/publish"); rowNote('Published the “Released on this day” row to Plex.', false); }
    catch (err) { rowNote(err.message, true); }
    await loadRows(); return;
  }
  const id = e.target.closest("tr")?.dataset.rid;
  if (!id) return;
  if (e.target.classList.contains("wUp") || e.target.classList.contains("wDown")) {
    const ids = [...W.order], n = ids.indexOf(id), m = n + (e.target.classList.contains("wUp") ? -1 : 1);
    if (m < 0 || m >= ids.length) return;
    [ids[n], ids[m]] = [ids[m], ids[n]];
    const d = await rowAction({ order: ids }, e.target);
    if (d) rowNote(d.order_note ? `Order saved, but Plex wouldn't move the rows on Home (${d.order_note}).` : "Order saved and applied to Plex's Home screen.", !!d.order_note);
    return;
  }
  if (e.target.classList.contains("wPub")) {
    e.target.disabled = true;
    try { const d = await post("/api/rows", { publish: id }); trackJob(d.job); }
    catch (err) { rowNote(err.message, true); e.target.disabled = false; }
  } else if (e.target.classList.contains("wMiss")) {
    openMissing(id);
  } else if (e.target.classList.contains("wDel")) {
    if (e.target.dataset.arm !== "1") { e.target.dataset.arm = "1"; e.target.textContent = "Confirm: also removes it from Plex"; return; }
    await rowAction({ delete: id }, e.target);
  } else if (e.target.classList.contains("wEdit")) {
    const r = W.rows.find((x) => x.id === id);
    W.editing = id; W.crit = r.criteria.map((c) => ({ ...c, top: c.top || "" }));
    W.csvs = []; $("#wCsv").value = ""; W.manual = r.manual_order || [];
    const csvSrc = (r.sources || []).filter((x) => x.type === "csv");
    W.keepCsv = csvSrc.length > 0;
    $("#wListUrl").value = (r.sources || []).filter((x) => x.type === "imdb" || x.type === "mdblist").map((x) => (x.type === "mdblist" ? "https://mdblist.com/lists/" + x.list : x.list)).join("\n");
    $("#wListInfo").textContent = W.keepCsv ? `Keeping ${csvSrc.reduce((a, x) => a + x.count, 0)} titles from uploaded file(s). Choose new files to replace them.` : "";
    $("#wName").value = r.name; $("#wMedia").value = r.media; $("#wSort").value = r.sort === "newest" ? "added" : r.sort; $("#wDir").value = r.sort_dir || (r.sort === "newest" || r.sort !== "title" ? "desc" : "asc"); $("#wLimit").value = r.limit;
    $("#wRating").value = r.min_rating ?? ""; $("#wDays").value = r.added_days ?? ""; $("#wUnwatched").checked = r.unwatched; $("#wPin").checked = r.pin; $("#wShared").checked = r.shared !== false; $("#wAuto").checked = r.auto;
    if ((r.sources || []).length || W.keepCsv) $("#wAdvanced").open = true; $("#wSave").textContent = "Update row"; $("#wCancel").hidden = false; syncArrange(); renderCrit(); previewRow(); window.scrollTo({ top: 0, behavior: "smooth" });
  }
});
// Keyboard navigation for Plex Home Screen table
$("#wList").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); (tr.nextElementSibling || tr.parentElement.firstElementChild)?.focus(); }
  if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); (tr.previousElementSibling || tr.parentElement.lastElementChild)?.focus(); }
  if (e.key === "e") { e.preventDefault(); tr.querySelector(".wEdit")?.click(); }
});

// ---------- help search ----------
function clearHelpHighlights() {
  document.querySelectorAll("#viewHelp mark.search-match").forEach((m) => {
    const parent = m.parentNode;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
}
function highlightMatches(el, q) {
  if (!q) return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  const matches = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const idx = node.textContent.toLowerCase().indexOf(q);
    if (idx >= 0) matches.push({ node, idx, len: q.length });
  }
  matches.reverse().forEach(({ node, idx, len }) => {
    const range = document.createRange();
    range.setStart(node, idx);
    range.setEnd(node, idx + len);
    const mark = document.createElement("mark");
    mark.className = "search-match";
    range.surroundContents(mark);
  });
}
$("#helpQ").addEventListener("input", () => {
  const q = $("#helpQ").value.trim().toLowerCase();
  clearHelpHighlights();
  let shown = 0;
  document.querySelectorAll("#viewHelp .hs").forEach((d) => {
    const hit = !q || d.textContent.toLowerCase().includes(q);
    d.hidden = !hit;
    if (hit) shown++;
    if (q && hit) { d.open = true; highlightMatches(d, q); }
  });
  $("#helpNone").hidden = shown > 0;
  $("#helpToc").hidden = !!q;
});

$("#logoutBtn").addEventListener("click", async () => {
  try { await post("/api/logout"); } catch (e) { /* already signed out */ }
  location.href = "/login";
});

// keep the phone card labels in sync whenever those tables are re-rendered
["#rTbl", "#wTbl", "#hTbl", "#mTbl"].forEach((id) => {
  const t = $(id);
  if (!t) return;
  new MutationObserver(() => labelCells(t)).observe(t.tBodies[0], { childList: true });
  labelCells(t);
});
