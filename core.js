/* Shared core for the four Dost Textiles dashboards (A board report, B sales cockpit,
   C mill floor, D ledger desk). Data: business_dashboard_data.json (+ ledgers json),
   written daily by 40_business_dashboard.py to the Drive reports folder; each page embeds a
   snapshot and re-reads the live files through the viewer's Google Drive connector. */
const EMBED = /*__DASHBOARD_DATA__*/null;
const EMBED_L = /*__LEDGER_DATA__*/null;
const DRIVE_FILE_ID = "__DRIVE_FILE_ID__";
const LEDGER_FILE_ID = "__LEDGER_FILE_ID__";
const DRIVE_SERVER = "Google Drive";

let D = EMBED, LG = EMBED_L, X = null;
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MN = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const FYM = ["07","08","09","10","11","12","01","02","03","04","05","06"];
const mLabel = m => MN[+m.slice(5) - 1] + " " + m.slice(2, 4);
const mLong = m => MN[+m.slice(5) - 1] + " " + m.slice(0, 4);
const fyOf = m => { const y = +m.slice(0, 4), mo = +m.slice(5, 7); return mo >= 7 ? y + 1 : y; };
const fyLabel = y => "FY" + String(y).slice(2);
const fyPos = m => (+m.slice(5, 7) + 5) % 12;          // Jul = 0 ... Jun = 11
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const dLong = d => d ? `${+d.slice(8)} ${MN[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}` : "–";
const dShort = d => d ? `${+d.slice(8)} ${MN[+d.slice(5, 7) - 1]} ${d.slice(2, 4)}` : "–";

function rs(v, short) {
  if (v == null || isNaN(v)) return "–";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e7) return s + "Rs " + (a / 1e7).toFixed(2) + " cr";
  if (a >= 1e5) return s + "Rs " + (a / 1e5).toFixed(short ? 1 : 2) + " lac";
  return s + "Rs " + Math.round(a).toLocaleString("en-PK");
}
const full = v => v == null || isNaN(v) ? "–" : (v < 0 ? "−" : "") + Math.round(Math.abs(v)).toLocaleString("en-PK");
const mtr = v => Math.round(v || 0).toLocaleString("en-PK") + " m";
const lacTick = v => { const a = Math.abs(v); return a >= 1e7 ? (v / 1e7).toFixed(1) + " cr" : a >= 1e5 ? (v / 1e5).toFixed(0) + " lac" : Math.round(v).toLocaleString("en-PK"); };
const pctTxt = (a, b) => b ? ((a - b) / Math.abs(b) * 100) : null;
function chg(cur, prev, goodUp = true, lbl = "") {
  const d = pctTxt(cur, prev); if (d == null || cur == null) return "";
  const up = d >= 0, good = up === goodUp;
  return `<span class="chg ${good ? "good" : "bad"}">${up ? "▲" : "▼"} ${Math.abs(d).toFixed(Math.abs(d) < 10 ? 1 : 0)}%</span>${lbl ? " " + lbl : ""}`;
}

/* sparse series */
function flows(pairs) { const a = new Array(D.months.length).fill(0); pairs.forEach(([i, v]) => a[i] = v); return a; }
function balAt(p, i) { if (i < p.from || i > p.to) return null; let v = null; for (const [k, b] of p.pairs) { if (k > i) break; v = b; } return v; }
const t12 = (arr, i) => sum(arr.slice(Math.max(0, i - 11), i + 1));

const EXP_GROUPS = [
  ["Salaries & wages", c => /^4002|^4501/.test(c)], ["Power & fuel", c => /^4004/.test(c)],
  ["Stores & spares", c => /^4003|^450201/.test(c)], ["Sizing & direct", c => c === "400701"],
  ["Repairs & vehicles", c => /^4005|^4006|^450202|^450208/.test(c)], ["Freight", c => c === "400702"],
  ["Selling", c => c === "450216"], ["Admin & other", c => true]];

function prep() {
  const n = D.months.length; X = { n };
  X.salesTotal = D.months.map((_, i) => sum(Object.values(D.sales.series).map(s => s[i])));
  X.customers = D.sales.customers.map(c => ({ name: c.name, s: flows(c.pairs) }));
  X.looms = D.production.looms.map(l => { const m = new Array(n).fill(null); l.pairs.forEach(([i, a, b, g, t]) => m[i] = { mtr: a, m100: b, good: g, tot: t }); return { loom: l.loom, m }; });
  const grp = EXP_GROUPS.map(([name]) => ({ name, heads: [] }));
  D.expenses.heads.forEach(h => grp[EXP_GROUPS.findIndex(([, f]) => f(h.code))].heads.push(h));
  grp.forEach(g => g.series = D.months.map((_, i) => sum(g.heads.map(h => h.series[i]))));
  X.expGroups = grp.filter(g => g.heads.length);
  X.expTotal = D.months.map((_, i) => sum(X.expGroups.map(g => g.series[i])));
  X.gradeTot = D.months.map((_, i) => sum(["A","A2","B","C","CP"].map(k => D.production.grades[k][i])));
  X.eff = D.production.mtr.map((m, k) => D.production.mtr100[k] ? m / D.production.mtr100[k] * 100 : null);
  X.aShare = D.months.map((_, k) => X.gradeTot[k] ? (D.production.grades.A[k] + D.production.grades.A2[k]) / X.gradeTot[k] * 100 : null);
  X.lastFull = Math.max(0, n - 2);          // last complete month
  X.iOf = m => D.months.indexOf(m);
  X.fyIdx = y => D.months.map((m, i) => fyOf(m) === y ? i : -1).filter(i => i >= 0);
  X.fyTot = (arr, y, upto) => sum(X.fyIdx(y).filter(i => upto == null || fyPos(D.months[i]) <= upto).map(i => arr[i]));
}

/* chart plumbing */
const charts = {};
function chartOpts(stacked, yFmt, extra = {}) {
  const o = {
    responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "bottom", labels: { color: css("--ink-2"), boxWidth: 10, boxHeight: 10, font: { size: 12 } } },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: css("--ink-2"), borderColor: css("--axis"), borderWidth: 1, padding: 10,
        callbacks: { label: c => ` ${c.dataset.label}: ${yFmt(c.parsed.y)}` } }
    },
    scales: {
      x: { stacked, grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkipPadding: 10, font: { size: 11 } }, border: { color: css("--axis") } },
      y: { stacked, grid: { color: css("--grid") }, border: { display: false }, ticks: { color: css("--muted"), font: { size: 11 }, callback: v => yFmt(v, true) } }
    }
  };
  const merged = Object.assign(o, extra);
  merged.plugins = Object.assign(o.plugins, extra.plugins || {});
  return merged;
}
function draw(id, cfg) {
  if (charts[id]) charts[id].destroy();
  const el = $(id); if (!el) return;
  charts[id] = new Chart(el, cfg);
}
const barDs = (label, data, color, extra = {}) => Object.assign({ label, data, backgroundColor: color, borderRadius: 2, borderSkipped: "bottom", maxBarThickness: 30 }, extra);
const lineDs = (label, data, color, extra = {}) => Object.assign({ type: "line", label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, cubicInterpolationMode: "monotone", spanGaps: true }, extra);
const SER = () => [1,2,3,4,5,6,7,8].map(i => css("--s" + i));

/* ledgers */
const VT = { GPOUT: "Invoice", GPIN: "Fabric return", JV: "Journal", BR: "Bank receipt", CR: "Cash receipt", BP: "Bank payment",
  CP: "Cash payment", SRN: "Store GRN", SAL: "Store sale", IGP_SIZED_BEAMS: "Sizing bill", OGP_YARN: "Yarn sale", OPN: "Opening" };
const GROUP_NAME = { conversion: "Conversion parties", store: "Store & supplier parties", accrued: "Accrued & other payables", cash: "Cash in hand", bank: "Bank accounts" };
function ledgerBalance(a) { return sum(a.lines.map(l => l[4])); }
function ledgerPosition(a, bal) {
  if (Math.abs(bal) < 1) return { txt: "Settled", cls: "" };
  if (a.group === "conversion") return bal > 0 ? { txt: "Owes us", cls: "warn" } : { txt: "Advance received", cls: "good" };
  if (a.group === "cash") return bal > 0 ? { txt: "Cash in hand", cls: "good" } : { txt: "Credit balance - check", cls: "crit" };
  if (a.group === "bank") return bal > 0 ? { txt: "In bank", cls: "good" } : { txt: "Overdrawn / credit", cls: "warn" };
  return bal < 0 ? { txt: "We owe", cls: "warn" } : { txt: "Advance paid", cls: "good" };
}

/* live refresh from the daily Drive files */
function b64ToText(b) { const bin = atob(b); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new TextDecoder().decode(u); }
function extractJson(payload, key) {
  if (!payload) return null;
  if (typeof payload === "object" && payload[key]) return payload;
  const cands = []; const walk = v => { if (typeof v === "string") cands.push(v); else if (v && typeof v === "object") Object.values(v).forEach(walk); };
  walk(payload);
  for (const s of cands) {
    if (s.length < 50) continue;
    for (const t of [s, (() => { try { return b64ToText(s.replace(/\s/g, "")); } catch (e) { return ""; } })()]) {
      if (t && t.trim().startsWith("{")) { try { const j = JSON.parse(t); if (j[key]) return j; } catch (e) {} }
    }
  }
  return null;
}
function setLive(on, txt) { const d = $("live-dot"), t = $("live-txt"); if (d) d.classList.toggle("on", on); if (t) t.textContent = txt; }
function errText(c) {
  if (c === "needs_reauth") return "Snapshot · reconnect Google Drive in claude.ai Settings → Connectors";
  if (c === "server_not_connected" || c === "selection_required") return "Snapshot · add Google Drive in claude.ai Settings → Connectors";
  if (c === "not_in_manifest" || c === "not_granted" || c === "capability_disabled") return "Snapshot · Drive access not allowed for this page";
  if (c === "tool_error") return "Snapshot · Drive file not found";
  return "Snapshot · Drive unreachable, showing saved data";
}
async function startLive(onMain, onLedger) {
  if (!window.claude || !window.claude.use) return;
  const mcp = await window.claude.use("mcp"); if (!mcp) return;
  setLive(false, "Checking Drive…");
  const opts = { cache: { staleTime: 300000, gcTime: 86400000 }, refetchInterval: 3600000 };
  if (!DRIVE_FILE_ID.startsWith("__")) mcp.watchTool(DRIVE_SERVER, "download_file_content", { fileId: DRIVE_FILE_ID }, ev => {
    if (ev.type === "data") {
      const j = extractJson(ev.result.payload ?? ev.result.content, "months");
      if (j && j.switch_index != null) { if (!D || j.generated_at >= D.generated_at) { D = j; X = null; onMain(); } setLive(true, "Live · data of " + D.generated_at); }
      else setLive(false, "Snapshot · Drive file unreadable");
    } else setLive(false, errText(ev.error && ev.error.code));
  }, opts);
  if (onLedger && !LEDGER_FILE_ID.startsWith("__")) mcp.watchTool(DRIVE_SERVER, "download_file_content", { fileId: LEDGER_FILE_ID }, ev => {
    if (ev.type === "data") { const j = extractJson(ev.result.payload ?? ev.result.content, "accounts"); if (j && (!LG || j.generated_at >= LG.generated_at)) { LG = j; onLedger(); } }
  }, opts);
}
function onTheme(fn) {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fn);
  new MutationObserver(fn).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
}
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
