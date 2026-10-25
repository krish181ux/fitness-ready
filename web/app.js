// Ready: the dashboard and the coach panel.
//
// Every number on the page comes from the same named tools the agent calls
// (tools/server.py); nothing here recomputes the rubric. The verdict, its
// checks and the data gaps are shown as the tools return them.

// ------------------------------------------------------------------ helpers

const $ = (sel, root = document) => root.querySelector(sel);

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const parse = (text) => {
  const [y, m, d] = text.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
};
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const sameDay = (a, b) => a && b && iso(a) === iso(b);
const fmt = (d, opts) => new Intl.DateTimeFormat("en-GB", opts).format(d);
const short = (d) => fmt(d, { weekday: "short", day: "numeric", month: "short" });
const long = (d) => fmt(d, { weekday: "long", day: "numeric", month: "long" });
const clock = (text) => (text ? text.slice(11, 16) : "");
const daysBetween = (a, b) => Math.round((b - a) / 86400000);
const today = () => { const t = new Date(); return new Date(t.getFullYear(), t.getMonth(), t.getDate()); };

const hm = (minutes) => {
  if (minutes == null) return "—";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
};
const hmBig = (minutes) => {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `<span class="big num">${h}<span class="unit">h</span> ${String(m).padStart(2, "0")}<span class="unit">m</span></span>`;
};
const signed = (n) => (n > 0 ? `+${n}` : `${n}`);
const thousands = (n) => (n == null ? "—" : Math.round(n).toLocaleString("en-GB"));
const upper = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const listJoin = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`);

async function api(path, params = {}) {
  const url = new URL(path, location.href);
  for (const [key, value] of Object.entries(params)) {
    if (value != null) url.searchParams.set(key, value);
  }
  const res = await fetch(url);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

const store = {
  get(key) { try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; } },
  set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

// -------------------------------------------------------------------- icons

const ICONS = {
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  wave: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
  timer: '<path d="M10 2h4"/><path d="m12 14 3-3"/><circle cx="12" cy="14" r="8"/>',
  battery: '<rect width="16" height="10" x="2" y="7" rx="2"/><path d="M22 11v2"/><path d="M6 11v2"/><path d="M10 11v2"/>',
  bolt: '<path d="M13 2 4.5 13.5H12L11 22l8.5-11.5H12L13 2Z"/>',
  book: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path d="m3 3 18 18"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z"/>',
  chevL: '<path d="m15 18-6-6 6-6"/>',
  chevR: '<path d="m9 18 6-6-6-6"/>',
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  compose: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  sunrise: '<path d="M12 2v8"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m8 6 4-4 4 4"/><path d="M16 18a4 4 0 0 0-8 0"/>',
  run: '<circle cx="14.5" cy="4" r="1.8"/><path d="M7.5 21.5 10.5 16l3 2.5V22"/><path d="M5 11.5 8.5 8l4.5.5 2.5 3.5 3.5 1.2"/><path d="m10.5 16 2.2-7.3"/>',
  walk: '<path d="M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6C9.37 2 10 3.8 10 5.5c0 3.11-2 5.66-2 8.68V16a2 2 0 1 1-4 0Z"/><path d="M20 20v-2.38c0-2.12 1.03-3.12 1-5.62-.03-2.72-1.49-6-4.5-6C14.63 6 14 7.8 14 9.5c0 3.11 2 5.66 2 8.68V20a2 2 0 1 0 4 0Z"/><path d="M16 17h4"/><path d="M4 13h4"/>',
  bike: '<circle cx="18.5" cy="17.5" r="3.5"/><circle cx="5.5" cy="17.5" r="3.5"/><circle cx="15" cy="5" r="1"/><path d="M12 17.5V14l-3-3 4-3 2 3h2"/>',
  dumbbell: '<path d="M6.5 6.5v11"/><path d="M17.5 6.5v11"/><path d="M3 9.5v5"/><path d="M21 9.5v5"/><path d="M6.5 12h11"/>',
  racquet: '<ellipse cx="14.5" cy="9.5" rx="5.2" ry="6.4" transform="rotate(45 14.5 9.5)"/><path d="m10.4 13.6-7 7"/><path d="M11 6.5l7 7M9.5 9l5.5 5.5"/>',
  swim: '<path d="M2 7c.6.5 1.2 1 2.5 1C7 8 7 6 9.5 6c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 17c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
  mountain: '<path d="m8 3 4 8 5-5 5 15H2L8 3z"/>',
  lotus: '<path d="M12 20c-4 0-8-2-9-6 3 0 6 1 9 4 3-3 6-4 9-4-1 4-5 6-9 6Z"/><path d="M12 18c-2-2-3-5-3-8 1.2.6 2.2 1.5 3 2.6.8-1.1 1.8-2 3-2.6 0 3-1 6-3 8Z"/>',
};

const icon = (name, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] ?? ICONS.wave}</svg>`;

const ACTIVITY_ICONS = [
  [/run/, "run"], [/walk/, "walk"], [/hik|mountain/, "mountain"], [/cycl|bik|ride/, "bike"],
  [/strength|weight|gym|hiit/, "dumbbell"], [/badminton|tennis|squash|padel|pickle/, "racquet"],
  [/swim/, "swim"], [/yoga|pilates|breath|stretch/, "lotus"],
];
const activityIcon = (type) => ACTIVITY_ICONS.find(([re]) => re.test(type || ""))?.[1] ?? "wave";
const activityName = (type) => upper((type || "activity").replaceAll("_", " "));

// ------------------------------------------------------------------ meaning

// One action per colour (SPEC §8: train hard / easy / rest), no more.
const VERDICT = {
  green: { label: "Green", title: "Ready for a hard session", action: "Train hard today", icon: "bolt" },
  amber: { label: "Amber", title: "Keep it easy today", action: "Easy session only", icon: "walk" },
  red: { label: "Red", title: "Make it a rest day", action: "Rest, or a gentle walk", icon: "moon" },
  insufficient_data: { label: "No call", title: "Not enough data to call it", action: "Sync the watch, then check again", icon: "info" },
};

const CHECKS = {
  sleep: { label: "Sleep", icon: "moon", accent: "var(--sleep)" },
  hrv: { label: "HRV", icon: "wave", accent: "var(--hrv)" },
  resting_hr: { label: "Resting HR", icon: "heart", accent: "var(--rhr)" },
  recovery: { label: "Recovery", icon: "timer", accent: "var(--load)" },
};
const CHECK_ORDER = ["sleep", "hrv", "resting_hr", "recovery"];
const SESSIONS_SHOWN = 5;

const KINDS = {
  rpe: { label: "Effort", glyph: "RPE", c: "var(--load)", max: 10 },
  soreness: { label: "Soreness", glyph: "So", c: "var(--rhr)", max: 10 },
  energy: { label: "Energy", glyph: "En", c: "var(--battery)", max: 10 },
  stress: { label: "Stress", glyph: "St", c: "var(--amber)", max: 10 },
  alcohol: { label: "Alcohol", glyph: "Al", c: "var(--rem)", unit: ["drink", "drinks"] },
  caffeine: { label: "Caffeine", glyph: "Ca", c: "var(--light)", unit: ["cup", "cups"] },
  illness: { label: "Illness", glyph: "Il", c: "var(--red)" },
  injury: { label: "Injury", glyph: "In", c: "var(--red)" },
  note: { label: "Note", glyph: "N", c: "var(--muted)" },
};

// ------------------------------------------------------------------- state

const state = {
  latest: null,     // last day the database has daily rows for
  selected: null,   // the day the board shows
  weekEnd: null,    // last day in the week strip
  token: 0,         // guards against a slow response for a day no longer shown
  verdict: null,
  allSessions: false,
  lastSessions: null,
};
const readinessCache = new Map();

function readiness(day) {
  const key = iso(day);
  if (!readinessCache.has(key)) {
    readinessCache.set(key, api("tools/get_readiness_inputs", { date: key }).catch((error) => {
      readinessCache.delete(key);
      throw error;
    }));
  }
  return readinessCache.get(key);
}

const el = {
  board: $("#board"), sync: $("#sync"), week: $("#week"), hero: $("#hero"), checks: $("#checks"),
  sleep: $("#sleepCard"), hrv: $("#hrvCard"), rhr: $("#rhrCard"), day: $("#dayCard"),
  sessions: $("#sessionsCard"), journal: $("#journalCard"), gaps: $("#gapsCard"), tip: $("#tip"),
};

// ------------------------------------------------------------------- charts
// Charts are drawn at the host's real width (no stretched viewBox, so dots
// stay round) and redrawn when it resizes. Each draw returns its SVG and the
// points the hover guide snaps to.

const chartDraws = new WeakMap();
const resizer = new ResizeObserver((entries) => entries.forEach((e) => paint(e.target)));

function chart(host, draw) {
  chartDraws.set(host, draw);
  resizer.observe(host);
  paint(host);
}

function paint(host) {
  const draw = chartDraws.get(host);
  if (!draw || !host.isConnected) { resizer.unobserve(host); return; }
  const width = Math.round(host.clientWidth);
  if (!width || host.dataset.width === String(width)) return;
  host.dataset.width = width;
  const { svg, points, height } = draw(width);
  host.innerHTML = svg;
  bindHover(host, points, height);
}

function bindHover(host, points, height) {
  if (!points?.length) return;
  const guide = host.querySelector(".guide");
  const show = (event) => {
    const box = host.getBoundingClientRect();
    const x = event.clientX - box.left;
    const near = points.reduce((a, b) => (Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a));
    if (guide) {
      guide.setAttribute("x1", near.x);
      guide.setAttribute("x2", near.x);
      guide.classList.add("on");
    }
    el.tip.innerHTML = near.tip;
    el.tip.style.left = `${box.left + near.x}px`;
    el.tip.style.top = `${box.top + (near.y ?? height * 0.3)}px`;
    el.tip.classList.add("on");
  };
  const hide = () => { el.tip.classList.remove("on"); guide?.classList.remove("on"); };
  host.onpointermove = show;
  host.onpointerdown = show;
  host.onpointerleave = hide;
}

const scale = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);

function niceTicks(lo, hi, count = 3) {
  const step = Math.max(1, Math.ceil((hi - lo) / count / 5) * 5);
  const start = Math.ceil(lo / step) * step;
  const ticks = [];
  for (let v = start; v <= hi; v += step) ticks.push(v);
  return ticks;
}

function linePath(pts) {
  // Gaps (null) break the line rather than bridging a night the watch missed.
  let d = "";
  let pen = false;
  for (const p of pts) {
    if (p == null) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
    pen = true;
  }
  return d;
}

function xLabels(days, x, y) {
  const picks = [0, Math.floor((days.length - 1) / 2), days.length - 1];
  return picks.map((i, n) => `<text class="axis" x="${x(i)}" y="${y}" text-anchor="${["start", "middle", "end"][n]}">${fmt(days[i], { day: "numeric", month: "short" })}</text>`).join("");
}

// ------------------------------------------------------------------ chrome

function renderSync() {
  const age = daysBetween(state.latest, today());
  const stale = age > 1;
  el.sync.classList.toggle("stale", stale);
  el.sync.innerHTML = `<span class="pulse"></span><span>${stale ? "Last synced day" : "Synced through"} <b>${short(state.latest)}</b></span>`
    + (stale ? `<span class="long">· ${age} days ago</span>` : "");
}

function renderWeek() {
  const days = Array.from({ length: 7 }, (_, i) => addDays(state.weekEnd, i - 6));
  const atEnd = sameDay(state.weekEnd, state.latest);
  el.week.innerHTML = `<button class="nav" type="button" data-shift="-7" aria-label="Previous week">${icon("chevL")}</button>`
    + days.map((d) => {
      const current = sameDay(d, state.selected);
      return `<button class="day" type="button" data-day="${iso(d)}" ${d > state.latest ? "disabled" : ""}
        ${current ? 'aria-current="date"' : ""} aria-label="${long(d)}">
        <small>${fmt(d, { weekday: "short" })}</small><b class="num">${d.getDate()}</b>
        <span class="dot" data-dot="${iso(d)}"></span></button>`;
    }).join("")
    + `<button class="nav" type="button" data-shift="7" ${atEnd ? "disabled" : ""} aria-label="Next week">${icon("chevR")}</button>`;
  for (const d of days) {
    if (d > state.latest) continue;
    readiness(d).then((r) => {
      const dot = el.week.querySelector(`[data-dot="${iso(d)}"]`);
      if (dot) dot.className = `dot ${r.verdict.colour}`;
    }).catch(() => {});
  }
}

el.week.addEventListener("click", (event) => {
  const shift = event.target.closest("[data-shift]");
  if (shift) {
    let end = addDays(state.weekEnd, Number(shift.dataset.shift));
    if (end > state.latest) end = state.latest;
    state.weekEnd = end;
    selectDay(end);
    return;
  }
  const day = event.target.closest("[data-day]");
  if (day && !day.disabled) selectDay(parse(day.dataset.day));
});

// -------------------------------------------------------------------- hero

function dialSvg(checks) {
  const byName = Object.fromEntries(checks.map((c) => [c.name, c]));
  const cx = 110, cy = 110, r = 84;
  const at = (deg, radius = r) => {
    const a = ((deg - 90) * Math.PI) / 180;
    return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)];
  };
  const arc = (a0, a1) => {
    const [x0, y0] = at(a0);
    const [x1, y1] = at(a1);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  const parts = CHECK_ORDER.map((name, i) => {
    const a0 = i * 90 + 8;
    const a1 = (i + 1) * 90 - 8;
    const result = byName[name]?.result ?? "missing";
    const [lx, ly] = at(i * 90 + 45, r + 24);
    return `<path class="track" d="${arc(a0, a1)}"/>
      <path class="seg ${result}" d="${arc(a0, a1)}" pathLength="1" style="animation-delay:${120 + i * 110}ms"/>
      <text class="lbl" x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" text-anchor="${lx > cx ? "start" : "end"}">${CHECKS[name].label}</text>`;
  });
  return `<svg viewBox="-60 0 340 220" role="img" aria-label="${checks.map((c) => `${CHECKS[c.name]?.label}: ${c.result}`).join(", ")}">${parts.join("")}</svg>`;
}

function checkPhrase(name, r) {
  const { sleep, hrv, resting_hr: rhr, last_hard_session: hard } = r;
  switch (name) {
    case "sleep":
      return sleep.trustworthy ? `sleep scored ${sleep.score}, under 70` : "Garmin flagged last night's sleep as untrustworthy";
    case "hrv":
      return `HRV is ${hrv.vs_baseline} its baseline (${hrv.last_night_avg} ms against ${hrv.baseline_low}–${hrv.baseline_high})`;
    case "resting_hr":
      return `resting HR is ${signed(rhr.delta)} bpm on its 30-day mean`;
    case "recovery":
      return `${hard.recovery_remaining_hours} h of recovery remain from ${activityName(hard.type).toLowerCase()}`;
    default:
      return name;
  }
}

function heroSentence(r) {
  const { failed, missing, colour } = r.verdict;
  const sentences = [];
  if (failed.length) {
    sentences.push(upper(listJoin(failed.map((name) => checkPhrase(name, r)))) + ".");
  } else if (colour !== "insufficient_data") {
    const passed = CHECK_ORDER.filter((n) => !missing.includes(n)).map((n) => CHECKS[n].label.replace("HR", "heart rate"));
    sentences.push(`${upper(listJoin(passed).toLowerCase().replace("hrv", "HRV"))} all check out.`);
  }
  if (missing.length) {
    const names = listJoin(missing.map((n) => CHECKS[n].label.replace("Resting HR", "resting HR").replace("Sleep", "sleep").replace("Recovery", "recovery")));
    sentences.push(colour === "insufficient_data"
      ? `There's no ${names} to go on, so the rubric won't make a call.`
      : `No ${names} to go on, so the call rests on the other checks.`);
  }
  return sentences.join(" ");
}

function renderHero(r, day) {
  const meta = VERDICT[r.verdict.colour] ?? VERDICT.insufficient_data;
  state.verdict = r.verdict.colour;
  document.documentElement.dataset.verdict = r.verdict.colour;
  const when = sameDay(day, state.latest) && sameDay(day, today()) ? "today" : `on ${long(day)}`;
  el.hero.innerHTML = `
    <div class="dial">${dialSvg(r.verdict.checks)}
      <div class="dial-center"><b>${meta.label}</b><small>${esc(r.verdict.based_on ?? "")}</small></div>
    </div>
    <div class="hero-copy">
      <div class="eyebrow">Readiness · ${long(day)}</div>
      <h1>${meta.title}</h1>
      <p>${esc(heroSentence(r))}</p>
      <div class="hero-actions">
        <span class="action">${icon(meta.icon)} ${meta.action}</span>
        <button class="cta" type="button" data-ask="Why is my readiness ${meta.label.toLowerCase()} ${when}, and what should I do about it?">${icon("sparkle")} Ask the coach why</button>
      </div>
    </div>`;
}

function checkTile(check, r) {
  const meta = CHECKS[check.name];
  let big = "—", unit = "", sub = esc(check.value);
  if (check.name === "sleep" && r.sleep.score != null) {
    big = r.sleep.score; unit = "score";
    sub = `${hm(r.sleep.total_min)} asleep${r.sleep.trustworthy ? "" : ` · flagged ${esc(r.sleep.validation ?? "")}`}`;
  } else if (check.name === "hrv" && r.hrv.last_night_avg != null) {
    big = r.hrv.last_night_avg; unit = "ms";
    sub = r.hrv.baseline_low != null
      ? `${upper(r.hrv.vs_baseline)} baseline ${r.hrv.baseline_low}–${r.hrv.baseline_high}`
      : "No baseline yet: Garmin needs about three weeks of nights";
  } else if (check.name === "resting_hr" && r.resting_hr.delta != null) {
    big = signed(r.resting_hr.delta); unit = "bpm";
    sub = `${r.resting_hr.today} today · 30-day mean ${r.resting_hr.mean_30d}`;
  } else if (check.name === "recovery") {
    const hard = r.last_hard_session;
    big = hard ? hard.recovery_remaining_hours : 0; unit = "h left";
    sub = hard
      ? `${activityName(hard.type)} ${Math.round(hard.hours_ago)} h ago${hard.recovery_basis === "assumed" ? " · assumed 24 h window" : ""}`
      : "No hard session on record";
  }
  const label = { pass: "Pass", fail: "Fail", missing: "Missing" }[check.result] ?? check.result;
  return `<div class="check ${check.result}" style="--accent:${meta.accent}">
    <div class="check-top"><span class="check-name">${icon(meta.icon)}${meta.label}</span><span class="chip ${check.result}">${label}</span></div>
    <div class="metric"><span class="big num">${big}</span><span class="unit">${unit}</span></div>
    <div class="sub">${sub}</div>
    <div class="rule">${esc(upper(check.rule))}</div>
  </div>`;
}

function renderChecks(r) {
  const byName = Object.fromEntries(r.verdict.checks.map((c) => [c.name, c]));
  el.checks.innerHTML = CHECK_ORDER.filter((n) => byName[n]).map((n) => checkTile(byName[n], r)).join("");
}

// ------------------------------------------------------------------- cards

const head = (title, iconName, accent, right = "") => `
  <div class="card-head" style="--accent:${accent}">
    <div class="card-title"><span class="ic">${icon(iconName)}</span>${title}</div>${right}
  </div>`;
const askBtn = (question, label = "Ask") => `<button class="ask" type="button" data-ask="${esc(question)}">${icon("sparkle")}${label}</button>`;
const failed = (card, title, iconName, accent, error) => {
  card.innerHTML = head(title, iconName, accent) + `<div class="empty">Couldn't load: ${esc(error.message)}</div>`;
};

function renderSleep(data, day) {
  const nights = Array.from({ length: 14 }, (_, i) => addDays(day, i - 13));
  const byDate = new Map(data.rows.map((row) => [row.date, row]));
  const night = byDate.get(iso(day));
  const isLatest = sameDay(day, state.latest);
  const question = isLatest ? "How did I sleep last night?" : `How did I sleep on the night into ${long(day)}?`;
  let body;
  if (!night) {
    body = `<div class="metric"><span class="big num">—</span></div>
      <div class="note calm">${icon("info")}<span>No sleep yet for the night of ${short(addDays(day, -1))} into ${short(day)}.
      If that's last night, it isn't synced: Garmin has it once the phone uploads.</span></div>`;
  } else {
    const parts = [["Deep", night.deep_min, "var(--deep)"], ["Light", night.light_min, "var(--light)"], ["REM", night.rem_min, "var(--rem)"], ["Awake", night.awake_min, "var(--awake)"]];
    const total = parts.reduce((s, p) => s + (p[1] || 0), 0) || 1;
    const scoreChip = night.sleep_score != null
      ? `<span class="chip ${night.sleep_score >= 70 ? "pass" : "warn"}">Score ${night.sleep_score}</span>` : "";
    body = `<div class="metric">${hmBig(night.total_min)}${scoreChip}</div>
      <div class="window">${icon("moon")}${clock(night.sleep_start)} → ${clock(night.sleep_end)}
        ${night.avg_spo2 ? `<span>· SpO₂ ${night.avg_spo2}%</span>` : ""}${night.avg_respiration ? `<span>· ${night.avg_respiration} brpm</span>` : ""}</div>
      ${night.trustworthy ? "" : `<div class="note">${icon("alert")}<span>Garmin flagged this night (${esc(night.validation)}), so readiness counts sleep as a fail rather than trusting the score.</span></div>`}
      <div class="stagebar">${parts.map(([n, m, c], i) => `<span title="${n}" style="flex:${(m || 0) / total};background:${c};animation-delay:${i * 80}ms"></span>`).join("")}</div>
      <div class="stages">${parts.map(([n, m, c]) => `<div class="stage"><small><i style="background:${c}"></i>${n}</small><b class="num">${hm(m)}</b><em>${Math.round(((m || 0) / total) * 100)}%</em></div>`).join("")}</div>`;
  }
  el.sleep.innerHTML = head("Sleep", "moon", "var(--sleep)", askBtn(question)) + body
    + `<div class="chart" id="sleepChart"></div>
       <div class="legend"><span><i style="background:var(--sleep)"></i>Score ≥ 70</span><span><i style="background:color-mix(in srgb,var(--sleep) 40%,transparent)"></i>Under 70</span><span><i style="background:repeating-linear-gradient(135deg,var(--amber) 0 2px,transparent 2px 4px)"></i>Untrustworthy</span></div>`;

  chart($("#sleepChart"), (w) => {
    const h = 132, top = 8, bottom = 20, left = 24;
    const values = nights.map((d) => byDate.get(iso(d))?.total_min ?? null);
    const max = Math.max(9 * 60, ...values.filter((v) => v != null));
    const y = scale(0, max, h - bottom, top);
    const slot = (w - left) / nights.length;
    const bw = Math.min(22, slot * 0.62);
    const x = (i) => left + slot * i + slot / 2;
    const grid = [4, 8].map((hr) => `<line class="grid-line" x1="${left}" x2="${w}" y1="${y(hr * 60)}" y2="${y(hr * 60)}"/><text class="axis" x="0" y="${y(hr * 60) + 3.5}">${hr}h</text>`).join("");
    const bars = nights.map((d, i) => {
      const row = byDate.get(iso(d));
      const cx = x(i) - bw / 2;
      if (!row) return `<rect x="${cx}" y="${h - bottom - 6}" width="${bw}" height="6" rx="3" fill="none" stroke="var(--faint)" stroke-dasharray="2 2"/>`;
      const yy = y(row.total_min);
      const sel = sameDay(d, day);
      const fill = !row.trustworthy ? "url(#hatch)" : row.sleep_score >= 70 ? "var(--sleep)" : "color-mix(in srgb, var(--sleep) 40%, transparent)";
      return `<rect x="${cx}" y="${yy}" width="${bw}" height="${h - bottom - yy}" rx="${Math.min(6, bw / 2)}" fill="${fill}" opacity="${sel ? 1 : 0.78}" ${sel ? 'stroke="white" stroke-width="1.5"' : ""}/>`;
    }).join("");
    const points = nights.map((d, i) => {
      const row = byDate.get(iso(d));
      const label = `<small>Night into ${short(d)}</small>`;
      return {
        x: x(i),
        y: row ? y(row.total_min) : h - bottom,
        tip: row
          ? `<b>${hm(row.total_min)}</b> · score ${row.sleep_score ?? "—"}${row.trustworthy ? "" : ` · <span style="color:var(--amber)">not trustworthy</span>`}${label}`
          : `<b>No sleep recorded</b>${label}`,
      };
    });
    return {
      height: h,
      points,
      svg: `<svg width="${w}" height="${h}">
        <defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="color-mix(in srgb, var(--amber) 14%, transparent)"/><line x1="0" y1="0" x2="0" y2="5" stroke="var(--amber)" stroke-width="2"/></pattern></defs>
        ${grid}${bars}
        <line class="guide" y1="${top}" y2="${h - bottom}"/>
        ${xLabels(nights, x, h - 4)}</svg>`,
    };
  });
}

function renderHrv(data, ready, day) {
  const days = Array.from({ length: data.days }, (_, i) => addDays(parse(data.as_of), i - data.days + 1));
  const byDate = new Map(data.rows.map((row) => [row.date, row]));
  const last = byDate.get(iso(day));
  const position = data.latest_vs_baseline;
  const chipClass = { within: "pass", below: "warn", above: "info" }[position] ?? "missing";
  const chipText = { within: "Within baseline", below: "Below baseline", above: "Above baseline" }[position] ?? "No baseline";
  const big = last?.last_night_avg != null
    ? `<span class="big num">${last.last_night_avg}</span><span class="unit">ms</span><span class="chip ${chipClass}">${chipText}</span>`
    : `<span class="big num">—</span><span class="chip missing">No reading</span>`;
  const stats = `<div class="stats">
    <div class="stat"><small>7-day</small><b class="num">${data.mean_7d ?? "—"}<em>ms</em></b></div>
    <div class="stat"><small>60-day</small><b class="num">${data.mean_60d ?? "—"}<em>ms</em></b></div>
    <div class="stat"><small>Baseline</small><b class="num">${data.baseline_low != null ? `${data.baseline_low}–${data.baseline_high}` : "—"}</b></div>
  </div>`;
  const noBaseline = data.baseline_low == null
    ? `<div class="note calm">${icon("info")}<span>Garmin needs about three weeks of consecutive nights before it reports an HRV baseline. Until then readiness treats this check as missing, not failed.</span></div>`
    : "";
  el.hrv.innerHTML = head("Heart rate variability", "wave", "var(--hrv)", askBtn("What is my HRV trend telling me?"))
    + `<div class="metric">${big}</div>${stats}${noBaseline}<div class="chart" id="hrvChart"></div>
    <div class="legend"><span><i style="background:color-mix(in srgb,var(--hrv) 22%,transparent)"></i>Baseline band</span><span><i style="background:var(--hrv)"></i>Within</span><span><i style="background:var(--amber)"></i>Below</span><span><i style="background:var(--light)"></i>Above</span></div>`;

  chart($("#hrvChart"), (w) => {
    const h = 150, top = 10, bottom = 20, left = 24, right = 6;
    const vals = data.rows.flatMap((r) => [r.last_night_avg, r.baseline_low, r.baseline_high, r.weekly_avg]).filter((v) => v != null);
    const lo = Math.floor((Math.min(...vals, 40) - 4) / 5) * 5;
    const hi = Math.ceil((Math.max(...vals, 60) + 4) / 5) * 5;
    const y = scale(lo, hi, h - bottom, top);
    const x = scale(0, days.length - 1, left + 4, w - right - 4);
    const rows = days.map((d) => byDate.get(iso(d)));
    // The band follows Garmin's baseline night by night, stepping where it moves.
    let band = "";
    let run = [];
    const flush = () => {
      if (run.length) {
        const upperEdge = run.map(([i, r]) => `${x(i)},${y(r.baseline_high)}`);
        const lowerEdge = run.slice().reverse().map(([i, r]) => `${x(i)},${y(r.baseline_low)}`);
        band += `<polygon points="${[...upperEdge, ...lowerEdge].join(" ")}" fill="color-mix(in srgb, var(--hrv) 12%, transparent)" stroke="color-mix(in srgb, var(--hrv) 35%, transparent)" stroke-width="1" stroke-dasharray="3 3"/>`;
      }
      run = [];
    };
    rows.forEach((r, i) => (r?.baseline_low != null ? run.push([i, r]) : flush()));
    flush();
    const weekly = linePath(rows.map((r, i) => (r?.weekly_avg != null ? [x(i), y(r.weekly_avg)] : null)));
    const grid = niceTicks(lo, hi).map((v) => `<line class="grid-line" x1="${left}" x2="${w}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="0" y="${y(v) + 3.5}">${v}</text>`).join("");
    const colour = (r) => {
      if (r.baseline_low == null) return "var(--hrv)";
      if (r.last_night_avg < r.baseline_low) return "var(--amber)";
      if (r.last_night_avg > r.baseline_high) return "var(--light)";
      return "var(--hrv)";
    };
    const dots = rows.map((r, i) => {
      if (r?.last_night_avg == null) return `<circle cx="${x(i)}" cy="${h - bottom}" r="2" fill="none" stroke="var(--faint)"/>`;
      const sel = sameDay(days[i], day);
      return `${sel ? `<circle cx="${x(i)}" cy="${y(r.last_night_avg)}" r="9" fill="${colour(r)}" opacity="0.22"/>` : ""}
        <circle cx="${x(i)}" cy="${y(r.last_night_avg)}" r="${sel ? 4.6 : 3.3}" fill="${colour(r)}" stroke="var(--bg)" stroke-width="1.5"/>`;
    }).join("");
    const points = rows.map((r, i) => ({
      x: x(i),
      y: r?.last_night_avg != null ? y(r.last_night_avg) : h - bottom,
      tip: r?.last_night_avg != null
        ? `<b>${r.last_night_avg} ms</b>${r.baseline_low != null ? ` · band ${r.baseline_low}–${r.baseline_high}` : ""}<small>${short(days[i])}${r.weekly_avg ? ` · 7-day avg ${r.weekly_avg}` : ""}</small>`
        : `<b>No HRV reading</b><small>${short(days[i])}</small>`,
    }));
    return {
      height: h,
      points,
      svg: `<svg width="${w}" height="${h}">${grid}${band}
        <path d="${weekly}" fill="none" stroke="color-mix(in srgb, var(--hrv) 55%, white)" stroke-width="1.6" stroke-linecap="round" opacity="0.55"/>
        <line class="guide" y1="${top}" y2="${h - bottom}"/>${dots}${xLabels(days, x, h - 4)}</svg>`,
    };
  });
}

function renderRhr(daily, ready, day) {
  const days = Array.from({ length: 30 }, (_, i) => addDays(day, i - 29));
  const byDate = new Map(daily.rows.map((row) => [row.date, row]));
  const rhr = ready?.resting_hr ?? {};
  const delta = rhr.delta;
  const chip = delta == null ? "" : `<span class="chip ${delta <= 3 ? "pass" : "fail"}">${signed(delta)} bpm vs 30-day</span>`;
  el.rhr.innerHTML = head("Resting heart rate", "heart", "var(--rhr)", askBtn("Is my resting heart rate trending up or down?"))
    + `<div class="metric"><span class="big num">${rhr.today ?? "—"}</span><span class="unit">bpm</span>${chip}</div>
       <div class="stats">
         <div class="stat"><small>30-day mean</small><b class="num">${rhr.mean_30d ?? "—"}<em>bpm</em></b></div>
         <div class="stat"><small>Lowest 30d</small><b class="num">${Math.min(...daily.rows.map((r) => r.resting_hr).filter(Boolean)) || "—"}<em>bpm</em></b></div>
         <div class="stat"><small>Rule</small><b class="num">≤ +3<em>bpm</em></b></div>
       </div>
       <div class="chart" id="rhrChart"></div>`;

  chart($("#rhrChart"), (w) => {
    const h = 130, top = 10, bottom = 20, left = 24, right = 6;
    const vals = days.map((d) => byDate.get(iso(d))?.resting_hr ?? null);
    const present = vals.filter((v) => v != null);
    if (!present.length) return { height: h, points: [], svg: `<div class="empty">No resting HR in the last 30 days.</div>` };
    const lo = Math.min(...present, rhr.mean_30d ?? Infinity) - 3;
    const hi = Math.max(...present, rhr.mean_30d ?? -Infinity) + 3;
    const y = scale(lo, hi, h - bottom, top);
    const x = scale(0, days.length - 1, left + 4, w - right - 4);
    const pts = vals.map((v, i) => (v == null ? null : [x(i), y(v)]));
    const line = linePath(pts);
    const firstIdx = vals.findIndex((v) => v != null);
    const lastIdx = vals.length - 1 - [...vals].reverse().findIndex((v) => v != null);
    const area = `${linePath(pts.map((p) => p))} L${x(lastIdx)} ${h - bottom} L${x(firstIdx)} ${h - bottom} Z`;
    const mean = rhr.mean_30d != null
      ? `<line x1="${left}" x2="${w}" y1="${y(rhr.mean_30d)}" y2="${y(rhr.mean_30d)}" stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1"/>
         <line x1="${left}" x2="${w}" y1="${y(rhr.mean_30d + 3)}" y2="${y(rhr.mean_30d + 3)}" stroke="color-mix(in srgb, var(--red) 55%, transparent)" stroke-dasharray="2 4" stroke-width="1"/>
         <text class="axis" x="${w}" y="${y(rhr.mean_30d + 3) - 4}" text-anchor="end">+3 limit</text>` : "";
    const grid = niceTicks(Math.ceil(lo), Math.floor(hi)).map((v) => `<text class="axis" x="0" y="${y(v) + 3.5}">${v}</text>`).join("");
    const lastV = vals[lastIdx];
    const points = vals.map((v, i) => ({
      x: x(i),
      y: v != null ? y(v) : h - bottom,
      tip: v != null ? `<b>${v} bpm</b><small>${short(days[i])}</small>` : `<b>No reading</b><small>${short(days[i])}</small>`,
    }));
    return {
      height: h,
      points,
      svg: `<svg width="${w}" height="${h}">
        <defs><linearGradient id="rhrFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--rhr)" stop-opacity="0.28"/><stop offset="1" stop-color="var(--rhr)" stop-opacity="0"/></linearGradient></defs>
        ${grid}${mean}
        <path d="${area}" fill="url(#rhrFill)"/>
        <path d="${line}" fill="none" stroke="var(--rhr)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        <line class="guide" y1="${top}" y2="${h - bottom}"/>
        ${lastV != null && lastIdx === vals.length - 1 ? `<circle cx="${x(lastIdx)}" cy="${y(lastV)}" r="8" fill="var(--rhr)" opacity="0.2"/><circle cx="${x(lastIdx)}" cy="${y(lastV)}" r="4" fill="var(--rhr)" stroke="var(--bg)" stroke-width="1.5"/>` : ""}
        ${xLabels(days, x, h - 4)}</svg>`,
    };
  });
}

function renderDay(daily, day) {
  const row = daily.rows.find((r) => r.date === iso(day));
  const title = sameDay(day, state.latest) ? "Today's body" : "That day's body";
  const ask = askBtn("How have my body battery and stress looked this week?");
  if (!row) {
    el.day.innerHTML = head(title, "battery", "var(--battery)", ask) + `<div class="empty">No daily metrics for ${short(day)}.</div>`;
    return;
  }
  const hiB = row.body_battery_high, loB = row.body_battery_low;
  const battery = hiB != null
    ? `<div class="battery"><div class="battery-track"><div class="battery-fill" style="left:${loB ?? 0}%;width:${hiB - (loB ?? 0)}%"></div></div>
         <div class="battery-scale"><span>0</span><span>Body battery range ${loB ?? "—"} → ${hiB}</span><span>100</span></div></div>` : "";
  el.day.innerHTML = head(title, "battery", "var(--battery)", ask)
    + `<div class="metric"><span class="big num">${hiB ?? "—"}</span><span class="unit">peak body battery</span></div>${battery}
       <div class="stats">
         <div class="stat"><small>Steps</small><b class="num">${thousands(row.steps)}</b></div>
         <div class="stat"><small>Intensity</small><b class="num">${row.intensity_minutes ?? "—"}<em>min</em></b></div>
         <div class="stat"><small>Avg stress</small><b class="num">${row.avg_stress ?? "—"}</b></div>
         <div class="stat"><small>Active</small><b class="num">${thousands(row.active_calories)}<em>kcal</em></b></div>
         <div class="stat"><small>Min HR</small><b class="num">${row.min_hr ?? "—"}<em>bpm</em></b></div>
         <div class="stat"><small>Max HR</small><b class="num">${row.max_hr ?? "—"}<em>bpm</em></b></div>
       </div>`;
}

function renderSessions(acts, journal, day) {
  const days = Array.from({ length: 14 }, (_, i) => addDays(day, i - 13));
  const rpeBySession = new Map();
  for (const entry of journal?.rows ?? []) {
    if (entry.kind === "rpe" && entry.activity_id != null) rpeBySession.set(entry.activity_id, entry);
  }
  const rpeFor = (s) => (s.parts ?? [s.activity_id]).map((id) => rpeBySession.get(id)).find(Boolean);
  const group = (sessions) => {
    const map = new Map();
    for (const s of sessions) {
      const key = s.start_time.slice(0, 10);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(s);
    }
    return map;
  };
  const all = group(acts.rows);
  const newest = acts.rows.slice().sort((a, b) => b.start_time.localeCompare(a.start_time));
  const hidden = state.allSessions ? 0 : Math.max(0, newest.length - SESSIONS_SHOWN);
  const byDay = group(newest.slice(0, newest.length - hidden).reverse());
  state.lastSessions = [acts, journal, day];
  const bars = days.map((d) => {
    const list = all.get(iso(d)) ?? [];
    const cls = list.some((s) => s.hard_session) ? "hard" : list.length ? "easy" : "";
    const height = cls === "hard" ? 100 : cls === "easy" ? 55 : 22;
    return `<span class="${cls}" style="height:${height}%" title="${short(d)}: ${list.length ? list.map((s) => activityName(s.type)).join(", ") : "rest"}"></span>`;
  }).join("");
  const right = `<div style="display:flex;align-items:center;gap:12px"><div class="hardbar" aria-hidden="true">${bars}</div>${askBtn("How has my training load looked over the last two weeks?")}</div>`;
  const label = (d) => (sameDay(d, state.latest) ? "Latest day · " : "") + long(d);
  const groups = [...byDay.keys()].sort().reverse().map((key) => {
    const rows = byDay.get(key).map((s) => {
      const rpe = rpeFor(s);
      const meta = [clock(s.start_time), `${Math.round(s.duration_min)} min`];
      if (s.distance_km > 0.2) meta.push(`${s.distance_km.toFixed(1)} km`);
      if (s.avg_hr) meta.push(`avg ${s.avg_hr} bpm`);
      const tags = [
        s.hard_session ? '<span class="chip warn">Hard</span>' : '<span class="chip missing">Easy</span>',
        s.aerobic_te != null ? `<span class="load-pill">TE <b class="num">${s.aerobic_te}</b></span>` : "",
        s.hard_session && s.recovery_time_hours ? `<span class="load-pill"><b class="num">${s.recovery_time_hours}</b> h recovery</span>` : "",
        rpe ? `<span class="load-pill">RPE <b class="num">${rpe.value}</b>${rpe.session_load ? ` · load <b class="num">${rpe.session_load}</b>` : ""}</span>` : "",
        s.parts?.length > 1 ? `<span class="chip info">Saved in ${s.parts.length} parts</span>` : "",
      ].join("");
      return `<div class="session ${s.hard_session ? "hard" : ""}">
        <div class="tile">${icon(activityIcon(s.type))}</div>
        <div class="what"><b>${esc(activityName(s.type))}</b><small class="num">${meta.join(" · ")}</small></div>
        <div class="tags">${tags}</div></div>`;
    }).join("");
    return `<div class="day-group"><h3>${label(parse(key))}</h3>${rows}</div>`;
  }).join("");
  const summary = `${acts.hard_session_count} hard of ${acts.count} · last 14 days`;
  el.sessions.innerHTML = head("Sessions", "bolt", "var(--load)", right)
    + `<div class="card-sub" style="margin:-6px 0 14px">${summary}</div>`
    + (groups ? `<div class="days">${groups}</div>` : `<div class="empty">No sessions recorded in the last 14 days.</div>`)
    + (hidden || state.allSessions && newest.length > SESSIONS_SHOWN
      ? `<button class="more" type="button" data-sessions-toggle>${hidden ? `Show ${hidden} earlier session${hidden > 1 ? "s" : ""}` : "Show fewer"}</button>` : "");
}

el.sessions.addEventListener("click", (event) => {
  if (!event.target.closest("[data-sessions-toggle]")) return;
  state.allSessions = !state.allSessions;
  renderSessions(...state.lastSessions);
});

function renderJournal(journal, day) {
  const prompts = `<div class="starters" style="margin-top:12px">
      <button class="starter log" type="button" data-prefill="That session was a 7 out of 10">${icon("pencil")}That session was a 7 out of 10</button>
      <button class="starter log" type="button" data-prefill="Soreness this morning is 3 out of 10">${icon("pencil")}Soreness this morning is 3 out of 10</button>
    </div>`;
  const rows = (journal.rows ?? []).slice().reverse().slice(0, 8);
  const head_ = head("Journal", "book", "var(--rem)", `<span class="card-sub">What the watch can't see</span>`);
  if (!rows.length) {
    el.journal.innerHTML = head_ + `<div class="empty" style="padding-top:4px">Nothing logged in the last 14 days. Effort, soreness, illness and alcohol are invisible to the watch: tell the coach and it logs them.</div>${prompts}`;
    return;
  }
  el.journal.innerHTML = head_ + `<div class="entries">${rows.map((e) => {
    const k = KINDS[e.kind] ?? KINDS.note;
    let value = "";
    if (k.max && e.value != null) {
      value = `<span class="scale" title="${e.value} of ${k.max}">${Array.from({ length: k.max }, (_, i) => `<i class="${i < e.value ? "on" : ""}"></i>`).join("")}</span>`;
    }
    const amount = k.unit && e.value != null ? `${e.value} ${k.unit[e.value === 1 ? 0 : 1]}` : k.max && e.value != null ? `${e.value}/${k.max}` : "";
    const detail = [amount, e.session_load ? `load ${e.session_load}` : "", e.note ? esc(e.note) : ""].filter(Boolean).join(" · ");
    return `<div class="entry" style="--c:${k.c}">
      <span class="glyph">${k.glyph}</span>
      <div><b>${k.label}</b><small>${detail || "&nbsp;"}</small></div>
      <div style="display:grid;justify-items:end;gap:4px">${value}<time>${short(parse(e.date))}</time></div></div>`;
  }).join("")}</div>`;
}

function cleanGap(text) {
  // Gap strings are written for the agent; trim the instructions aimed at it.
  const out = text
    .replace(/\s*Say so;.*$/s, "")
    .replace(/:\s*see `parts`/, "")
    .replace(/\d{4}-\d{2}-\d{2}/g, (m) => fmt(parse(m), { day: "numeric", month: "short" }));
  return upper(out);
}

function renderGaps(lists) {
  const seen = new Set();
  const items = lists.flat().map(cleanGap).filter((g) => !seen.has(g) && seen.add(g));
  const always = "Effort, soreness, illness and alcohol stay invisible unless you log them.";
  el.gaps.innerHTML = head("What the data can't see", "eye", "var(--muted)", `<span class="card-sub">${items.length ? `${items.length} gap${items.length > 1 ? "s" : ""}` : "No gaps"}</span>`)
    + `<ul class="gaps">${(items.length ? items : ["Every input the rubric needs was recorded for this day."]).concat(always)
      .map((g) => `<li>${icon(g === always ? "book" : items.length ? "alert" : "info")}<span>${esc(g)}</span></li>`).join("")}</ul>`;
}

// -------------------------------------------------------------------- load

function skeletons() {
  const block = (h, w = "100%") => `<div class="skel" style="height:${h}px;width:${w}"></div>`;
  el.hero.innerHTML = `<div class="skel" style="width:200px;height:200px;border-radius:50%"></div><div style="display:grid;gap:12px">${block(14, "40%")}${block(44, "75%")}${block(16, "90%")}${block(16, "60%")}</div>`;
  el.checks.innerHTML = Array.from({ length: 4 }, () => `<div class="check">${block(14, "50%")}${block(30, "40%")}${block(12, "80%")}</div>`).join("");
  for (const card of [el.sleep, el.hrv, el.rhr, el.day, el.sessions, el.journal, el.gaps]) {
    card.innerHTML = `<div style="display:grid;gap:14px">${block(14, "30%")}${block(40, "45%")}${block(110)}</div>`;
  }
}

async function selectDay(day) {
  const first = state.selected == null;
  state.selected = day;
  const token = ++state.token;
  renderWeek();
  if (first) skeletons();
  el.board.classList.add("loading");
  const d = iso(day);
  const from = (n) => iso(addDays(day, -n));
  const [ready, sleep, hrv, daily, acts, journal] = await Promise.allSettled([
    readiness(day),
    api("tools/get_sleep", { start_date: from(13), end_date: d }),
    api("tools/get_hrv_trend", { days: 30, as_of: d }),
    api("tools/get_daily_metrics", { start_date: from(29), end_date: d }),
    api("tools/list_activities", { start_date: from(13), end_date: d }),
    api("tools/get_journal", { start_date: from(13), end_date: d }),
  ]);
  if (token !== state.token) return;
  el.board.classList.remove("loading");
  const ok = (p) => (p.status === "fulfilled" ? p.value : null);

  if (ready.status === "fulfilled") {
    renderHero(ready.value, day);
    renderChecks(ready.value);
  } else {
    el.hero.innerHTML = `<div class="hero-copy"><div class="eyebrow">Readiness</div><h1>Couldn't load readiness</h1><p>${esc(ready.reason.message)}</p></div>`;
    el.checks.innerHTML = "";
  }
  sleep.status === "fulfilled" ? renderSleep(sleep.value, day) : failed(el.sleep, "Sleep", "moon", "var(--sleep)", sleep.reason);
  hrv.status === "fulfilled" ? renderHrv(hrv.value, ok(ready), day) : failed(el.hrv, "Heart rate variability", "wave", "var(--hrv)", hrv.reason);
  if (daily.status === "fulfilled") {
    renderRhr(daily.value, ok(ready), day);
    renderDay(daily.value, day);
  } else {
    failed(el.rhr, "Resting heart rate", "heart", "var(--rhr)", daily.reason);
    failed(el.day, "Body", "battery", "var(--battery)", daily.reason);
  }
  acts.status === "fulfilled" ? renderSessions(acts.value, ok(journal), day) : failed(el.sessions, "Sessions", "bolt", "var(--load)", acts.reason);
  journal.status === "fulfilled" ? renderJournal(journal.value, day) : failed(el.journal, "Journal", "book", "var(--rem)", journal.reason);
  renderGaps([ok(ready)?.data_gaps ?? [], ok(sleep)?.data_gaps ?? [], ok(hrv)?.data_gaps?.slice(-3) ?? []]);
  if (!coach.messages.length) renderThread();
}

function fatal(error) {
  document.documentElement.dataset.verdict = "insufficient_data";
  el.board.innerHTML = `<div class="fatal">
    <h1>The data isn't reachable</h1>
    <p>${esc(error.message)}</p>
    <p>Start the tools server with <code>make serve</code> (or <code>docker compose up -d</code>), then reload.</p>
  </div>`;
}

// ------------------------------------------------------------------- coach

const coach = {
  sessionId: store.get("coach.session") ?? crypto.randomUUID(),
  messages: store.get("coach.messages") ?? [],
  busy: false,
};
const thread = $("#thread");
const input = $("#input");
const sendBtn = $("#send");
const save = () => { store.set("coach.session", coach.sessionId); store.set("coach.messages", coach.messages); };

function md(source) {
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
  const lines = esc(source).split(/\r?\n/);
  let html = "";
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
  const flushList = () => {
    if (list) { html += `<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`; list = null; }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    let m;
    if (!line) { flushPara(); flushList(); continue; }
    if (line.startsWith("|") && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] ?? "")) {
      flushPara(); flushList();
      const rows = [line];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++].trim());
      i--;
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      html += `<table><thead><tr>${cells(rows[0]).map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>`
        + rows.slice(1).map((r) => `<tr>${cells(r).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
      continue;
    }
    if ((m = line.match(/^#{1,6}\s+(.*)$/))) { flushPara(); flushList(); html += `<h4>${inline(m[1])}</h4>`; continue; }
    if ((m = line.match(/^[-*•]\s+(.*)$/))) {
      flushPara();
      if (list?.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (list?.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { flushPara(); flushList(); continue; }
    flushList();
    para.push(line);
  }
  flushPara();
  flushList();
  return html;
}

function starters() {
  const label = VERDICT[state.verdict]?.label.toLowerCase();
  const asks = [
    "Should I train hard today?",
    state.verdict === "amber" || state.verdict === "red" ? `Why is my readiness ${label}?` : "What's driving my readiness today?",
    "How did I sleep last night?",
    "Is my HRV trending up or down?",
  ];
  return `<div class="starters">${asks.map((q) => `<button class="starter" type="button" data-ask="${esc(q)}">${icon("sparkle")}${esc(q)}</button>`).join("")}
    <button class="starter log" type="button" data-prefill="That session was a ">${icon("pencil")}Log how hard a session felt</button></div>`;
}

function renderThread() {
  if (!coach.messages.length) {
    thread.innerHTML = `<div class="welcome">
      <h2>Ask about your training, sleep or recovery.</h2>
      <p>The coach reads the same numbers as this page, cites them, and says when the watch didn't record something.</p>
      ${starters()}</div>`;
    return;
  }
  thread.innerHTML = coach.messages.map((m, i) => {
    if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
    if (m.error) {
      return `<div class="msg bot error"><span class="orb"></span><div class="body"><p>${esc(m.error)}</p>
        <button class="retry" type="button" data-retry="${i}">Try again</button></div></div>`;
    }
    return `<div class="msg bot"><span class="orb"></span><div class="body">${md(m.text)}</div></div>`;
  }).join("");
  thread.scrollTop = thread.scrollHeight;
}

async function send(text) {
  text = text.trim();
  if (!text || coach.busy) return;
  coach.busy = true;
  coach.messages.push({ role: "user", text });
  save();
  renderThread();
  input.value = "";
  autosize();

  const started = Date.now();
  const pending = document.createElement("div");
  pending.className = "msg bot";
  pending.innerHTML = `<span class="orb busy"></span><div class="thinking"><span class="shine">Reading your data</span><time>0s</time></div>`;
  thread.append(pending);
  thread.scrollTop = thread.scrollHeight;
  document.querySelectorAll(".coach-head .orb, .coach-fab .orb").forEach((o) => o.classList.add("busy"));
  const timer = setInterval(() => { $("time", pending).textContent = `${Math.round((Date.now() - started) / 1000)}s`; }, 1000);

  try {
    const res = await fetch("chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: coach.sessionId, message: text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `The coach returned HTTP ${res.status}.`);
    coach.messages.push({ role: "bot", text: data.reply });
  } catch (error) {
    coach.messages.push({ role: "bot", error: error.message === "Failed to fetch" ? "Couldn't reach the tools server." : error.message, retry: text });
  } finally {
    clearInterval(timer);
    coach.busy = false;
    document.querySelectorAll(".orb.busy").forEach((o) => o.classList.remove("busy"));
    save();
    renderThread();
    updateSend();
  }
  // The coach may have logged a journal entry; show it.
  if (state.selected) refreshJournal();
}

async function refreshJournal() {
  const day = state.selected;
  const token = state.token;
  try {
    const [journal, acts] = await Promise.all([
      api("tools/get_journal", { start_date: iso(addDays(day, -13)), end_date: iso(day) }),
      api("tools/list_activities", { start_date: iso(addDays(day, -13)), end_date: iso(day) }),
    ]);
    if (token !== state.token) return;
    renderJournal(journal, day);
    renderSessions(acts, journal, day);
  } catch { /* the board keeps what it had */ }
}

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}
const updateSend = () => { sendBtn.disabled = coach.busy || !input.value.trim(); };

function openCoach() {
  document.body.classList.add("coach-open");
}
function closeCoach() {
  document.body.classList.remove("coach-open");
}

function wireCoach() {
  $("#newChat").innerHTML = icon("compose");
  $("#coachClose").innerHTML = icon("x");
  sendBtn.innerHTML = icon("arrowUp");
  $("#composer").addEventListener("submit", (event) => { event.preventDefault(); send(input.value); });
  input.addEventListener("input", () => { autosize(); updateSend(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(input.value);
    }
  });
  $("#newChat").addEventListener("click", () => {
    if (coach.busy) return;
    coach.messages = [];
    coach.sessionId = crypto.randomUUID();
    save();
    renderThread();
    input.focus();
  });
  $("#coachOpen").addEventListener("click", () => { openCoach(); setTimeout(() => input.focus(), 300); });
  $("#coachClose").addEventListener("click", closeCoach);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeCoach(); });

  document.addEventListener("click", (event) => {
    const ask = event.target.closest("[data-ask]");
    if (ask) { openCoach(); send(ask.dataset.ask); return; }
    const prefill = event.target.closest("[data-prefill]");
    if (prefill) {
      openCoach();
      input.value = prefill.dataset.prefill;
      autosize();
      updateSend();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      return;
    }
    const retry = event.target.closest("[data-retry]");
    if (retry) {
      const index = Number(retry.dataset.retry);
      const failedTurn = coach.messages[index];
      // Drop the failed reply and the question it answered, then ask again.
      coach.messages.splice(index - 1, 2);
      save();
      send(failedTurn.retry);
    }
  });
  renderThread();
}

// -------------------------------------------------------------------- boot

async function boot() {
  wireCoach();
  let health;
  try {
    health = await api("health");
  } catch (error) {
    fatal(error);
    return;
  }
  if (!health.latest_date) {
    fatal(new Error("The database has no daily rows yet. Run a sync or `make load` first."));
    return;
  }
  state.latest = parse(health.latest_date);
  state.weekEnd = state.latest;
  renderSync();
  await selectDay(state.latest);
}

boot();
