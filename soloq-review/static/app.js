// SoloQ Review front end: hash-routed single page, no build step.

const view = document.getElementById("view");
const POLL_MS = 90_000;
const LIVE_POLL_MS = 3_000;
let status = null;
let ddragonVersion = null;
let live = null;            // /api/live: League client state and the current game
let autoPlanKey = null;     // game the plan screen already opened itself for
let planState = {};         // what the plan screen currently shows (to update only what changed)
const matchupCache = {};

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clock = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const signed = (n) => (n == null ? "–" : n > 0 ? `+${n}` : `${n}`);
const signClass = (n) => (n == null || n === 0 ? "" : n > 0 ? "pos" : "neg");
const when = (ms) => new Date(ms).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const LABELS = {
  won: "Won", even: "Even", lost: "Lost",
  matchup: "The matchup", jungler: "Junglers", mistakes: "My mistakes", team: "Team / map",
  yes: "Yes", partly: "Partly", no: "No",
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), isError ? 7000 : 3500);
}

async function loadDdragonVersion() {
  try {
    const versions = await (await fetch("https://ddragon.leagueoflegends.com/api/versions.json")).json();
    ddragonVersion = versions[0];
  } catch { ddragonVersion = null; }
}

function champ(name, size = "") {
  if (!name) return `<span class="champ ${size} champ-fallback">?</span>`;
  if (!ddragonVersion) return `<span class="champ ${size} champ-fallback" title="${esc(name)}">${esc(name.slice(0, 2))}</span>`;
  const src = `https://ddragon.leagueoflegends.com/cdn/${ddragonVersion}/img/champion/${encodeURIComponent(name)}.png`;
  return `<img class="champ ${size}" src="${src}" alt="${esc(name)}" title="${esc(name)}" loading="lazy">`;
}

function choiceGroup(name, options, current) {
  return `<div class="choices" data-choice="${name}">${options
    .map((o) => `<button type="button" data-value="${o}" class="${current === o ? "on" : ""}">${esc(LABELS[o] || o)}</button>`)
    .join("")}</div>`;
}

function stars(name, current) {
  return `<div class="choices stars" data-choice="${name}">${[1, 2, 3, 4, 5]
    .map((n) => `<button type="button" data-value="${n}" class="${current >= n ? "on" : ""}" aria-label="${n} stars">★</button>`)
    .join("")}</div>`;
}

// Wire up all choice groups inside `root`; returns a getter for selected values.
function bindChoices(root) {
  const values = {};
  root.querySelectorAll("[data-choice]").forEach((group) => {
    const name = group.dataset.choice;
    const isStars = group.classList.contains("stars");
    const selected = [...group.querySelectorAll("button.on")].pop();
    values[name] = selected ? (isStars ? Number(selected.dataset.value) : selected.dataset.value) : null;
    group.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const v = isStars ? Number(btn.dataset.value) : btn.dataset.value;
      values[name] = values[name] === v ? null : v; // click again to clear
      group.querySelectorAll("button").forEach((b) => {
        const bv = isStars ? Number(b.dataset.value) : b.dataset.value;
        b.classList.toggle("on", values[name] != null && (isStars ? bv <= values[name] : bv === values[name]));
      });
    });
  });
  return () => ({ ...values });
}

function gameFlags(g) {
  const flags = [];
  if (g.remake) flags.push(`<span class="chip">Remake</span>`);
  else if (!g.is_solo) flags.push(`<span class="chip">${esc(g.queue_name)} · not counted</span>`);
  if (g.off_role && !g.remake) flags.push(`<span class="chip">Off-role (${esc(g.role || "?")})</span>`);
  return flags.join(" ");
}

function resultBadge(g) {
  if (g.remake) return `<span class="badge neutral">REMAKE</span>`;
  return g.win ? `<span class="badge win">WIN</span>` : `<span class="badge loss">LOSS</span>`;
}

function reviewBadge(g) {
  if (g.review?.skipped) return `<span class="badge neutral">Skipped</span>`;
  if (g.review) return `<span class="badge neutral">Reviewed</span>`;
  if (g.needs_review) return `<span class="badge todo">Needs review</span>`;
  return "";
}

function gamesTable(games, { showFocus = false } = {}) {
  if (!games.length) return `<p class="muted">No games yet.</p>`;
  return `<table>
    <thead><tr><th></th><th>Matchup</th><th class="num">KDA</th><th class="num hide-sm">CS@10 diff</th>
    ${showFocus ? `<th class="num">Focus</th>` : ""}<th class="hide-sm">When</th><th></th></tr></thead>
    <tbody>${games.map((g) => `
      <tr class="clickable ${g.counts ? "" : "dim"}" data-href="#/game/${esc(g.match_id)}">
        <td>${resultBadge(g)}</td>
        <td><div class="row">${champ(g.champion, "sm")}<span class="vs">vs</span>${champ(g.opponent, "sm")}
          <span class="small">${esc(g.champion)} vs ${esc(g.opponent || "?")}</span> ${gameFlags(g)}</div></td>
        <td class="num">${g.kills}/${g.deaths}/${g.assists}</td>
        <td class="num hide-sm ${signClass(g.csd10)}">${signed(g.csd10)}</td>
        ${showFocus ? `<td class="num">${g.review?.focus_rating ? "★".repeat(g.review.focus_rating) : "–"}</td>` : ""}
        <td class="hide-sm small muted">${when(g.game_start)}</td>
        <td>${reviewBadge(g)}</td>
      </tr>`).join("")}</tbody></table>`;
}

function bindRowLinks(root) {
  root.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => (location.hash = tr.dataset.href)));
}

// ---------- sync / polling ----------
async function syncNow({ quiet = false } = {}) {
  try {
    const result = await api("/api/sync", { method: "POST" });
    if (result.busy) return [];
    if (!quiet || result.imported.length) toast(result.imported.length ? `Imported ${result.imported.length} new game(s)` : "No new games yet");
    result.errors.forEach((e) => console.warn(e));
    return result.imported;
  } catch (err) {
    if (!quiet) toast(err.message, true);
    return [];
  }
}

async function poll() {
  if (!status?.active_session || status.setup_problems.length) return;
  const imported = await syncNow({ quiet: true });
  if (!imported.length) return;
  status = await api("/api/status");
  // Jump straight to the newest game that needs a review — but only from the home screen.
  const fresh = status.active_session?.games.filter((g) => imported.includes(g.match_id) && g.needs_review).pop();
  if (fresh && (location.hash === "" || location.hash === "#/")) location.hash = `#/game/${fresh.match_id}`;
  else if (location.hash === "" || location.hash === "#/") render();
}

// ---------- views ----------
async function renderHome() {
  status = await api("/api/status");
  document.getElementById("whoami").textContent = status.demo ? "Demo mode (fake data)" : status.riot_id;
  const parts = [];

  if (status.setup_problems.length) {
    parts.push(`<section class="card warn"><h2>Setup needed</h2><ul>${status.setup_problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <p class="muted small">Edit the <code>.env</code> file in the soloq-review folder, then reload this page. See README.md for the steps.</p></section>`);
  }

  const unwrapped = status.unwrapped_session;
  if (unwrapped && unwrapped.games.length && !status.active_session) {
    parts.push(`<section class="card highlight row spread"><div><h2>Last session wasn't wrapped up</h2>
      <p class="muted small">${esc(unwrapped.session.focus_label)} · ${unwrapped.solo_games} solo game(s)</p></div>
      <div class="row"><a class="btn primary" href="#/wrap/${unwrapped.session.id}">Wrap up</a>
      <button data-skip-wrap="${unwrapped.session.id}">Skip</button></div></section>`);
  }

  const active = status.active_session;
  if (active) {
    const s = active.session;
    const done = active.solo_games;
    parts.push(`<section class="card highlight stack">
      <div class="focus-banner"><div><h3>Today's focus</h3><div class="focus">${esc(s.focus_label)}</div></div>
        <div><div class="progress">${Array.from({ length: Math.max(s.target_games, done) }, (_, i) => `<span class="${i < done ? "done" : ""}"></span>`).join("")}</div>
        <div class="small muted">${done} of ${s.target_games} solo games · ${active.wins}W ${active.losses}L</div></div>
        <div class="row" style="margin-left:auto"><button id="sync">Check for new games</button>
        <a class="btn" href="#/wrap/${s.id}">End session</a></div></div>
      ${done >= s.target_games ? `<p>You've hit your ${s.target_games}-game target. <a href="#/wrap/${s.id}">Wrap up the session →</a></p>` : ""}
      ${gamesTable(active.games, { showFocus: true })}
      <p class="muted small">New games are checked every 90 seconds while this page is open; the review opens automatically.</p>
    </section>`);
  } else {
    parts.push(startSessionCard());
  }

  const backlog = status.needs_review.filter((g) => !active || g.session_id !== active.session.id);
  if (backlog.length) {
    parts.push(`<section class="card"><h2>Still needs a review</h2>${gamesTable(backlog)}</section>`);
  }

  view.innerHTML = parts.join("");
  bindRowLinks(view);
  view.querySelector("#sync")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Checking…";
    await syncNow(); render();
  });
  view.querySelectorAll("[data-skip-wrap]").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/sessions/${b.dataset.skipWrap}/wrap`, { method: "POST", body: {} }); render();
  }));
  if (!active) bindStartSession();
}

function startSessionCard() {
  const last = status.last_session;
  const suggest = last && last.keep_focus !== 0 ? last : null;
  return `<section class="card stack" id="start">
    <h2>Start a session</h2>
    <div class="question"><div class="q">What's today's focus?</div>
      ${suggest ? `<div><button type="button" class="primary" id="same-focus">Same as last session: ${esc(suggest.focus_label)}</button></div>` : ""}
      <div class="choices" data-choice="focus">${status.focus_presets.map((p) => `<button type="button" data-value="${p.key}">${esc(p.label)}</button>`).join("")}
        <button type="button" data-value="custom">Custom…</button></div>
      <input type="text" id="custom-focus" placeholder="Describe your focus" hidden maxlength="80">
    </div>
    <div class="question"><label class="q" style="max-width:220px">Games planned
      <input type="number" id="target" min="1" max="10" value="${last?.target_games || 3}"></label></div>
    <div><button class="primary" id="start-btn">Start session</button></div>
  </section>`;
}

function bindStartSession() {
  const card = view.querySelector("#start");
  const values = bindChoices(card);
  const custom = card.querySelector("#custom-focus");
  card.querySelector('[data-choice="focus"]').addEventListener("click", () => {
    custom.hidden = values().focus !== "custom";
    if (!custom.hidden) custom.focus();
  });
  const start = async (focus_key, focus_label) => {
    try {
      await api("/api/sessions", { method: "POST", body: { focus_key, focus_label, target_games: Number(card.querySelector("#target").value) || 3 } });
      await syncNow({ quiet: true });
      render();
    } catch (err) { toast(err.message, true); }
  };
  card.querySelector("#same-focus")?.addEventListener("click", () => {
    const last = status.last_session;
    start(last.focus_key, last.focus_label);
  });
  card.querySelector("#start-btn").addEventListener("click", () => {
    const key = values().focus;
    if (!key) return toast("Pick a focus first", true);
    start(key, key === "custom" ? custom.value : null);
  });
}

async function renderGame(matchId) {
  const g = await api(`/api/games/${encodeURIComponent(matchId)}`);
  const r = g.review || {};
  const enemies = g.participants.filter((p) => p.team === "enemy");
  const session = g.session;
  const cspm = g.duration_s ? (g.cs / (g.duration_s / 60)).toFixed(1) : "–";

  const tiles = [
    ["KDA", `${g.kills} / ${g.deaths} / ${g.assists}`],
    ["CS", `${g.cs} <span class="small muted">(${cspm}/min)</span>`],
    ["CS @ 10", `${g.cs10 ?? "–"} <span class="small ${signClass(g.csd10)}">${signed(g.csd10)}</span>`],
    ["CS @ 14", `${g.cs14 ?? "–"} <span class="small ${signClass(g.csd14)}">${signed(g.csd14)}</span>`],
    ["Gold diff @ 10", `<span class="${signClass(g.gd10)}">${signed(g.gd10)}</span>`],
    ["Gold diff @ 14", `<span class="${signClass(g.gd14)}">${signed(g.gd14)}</span>`],
    ["Deaths before 14:00", `${g.deaths_pre14} <span class="small muted">(${g.solo_deaths_pre14} solo)</span>`],
    [`Deaths with ${esc(g.enemy_jungler || "enemy JG")} involved`, `${g.deaths_enemy_jg} <span class="small muted">(${g.deaths_enemy_jg_pre14} pre-14)</span>`],
    [`Kills with ${esc(g.my_jungler || "my JG")}`, `${g.kills_with_my_jg} <span class="small muted">(${g.kills_with_my_jg_pre14} pre-14)</span>`],
  ];

  const events = g.events.map((e) => {
    const isDeath = e.kind === "death";
    const other = isDeath ? e.killer : e.victim;
    const text = isDeath ? `Killed by <b>${esc(other)}</b>` : `Killed <b>${esc(other)}</b>`;
    const chips = [];
    if (e.solo) chips.push(`<span class="chip">solo</span>`);
    if (e.enemy_jg_involved) chips.push(`<span class="chip bad">enemy jungler</span>`);
    if (e.my_jg_involved) chips.push(`<span class="chip good">with jungler</span>`);
    if (e.ts_ms < 14 * 60_000) chips.push(`<span class="chip">laning phase</span>`);
    return `<div class="event">
      <div class="time">${clock(e.ts_ms)}</div>
      <div class="badge-cell"><span class="badge ${e.kind}">${e.kind.toUpperCase()}</span></div>
      <div class="body"><div class="row">${champ(other, "sm")} <span>${text}</span>
        ${e.assists.length ? `<span class="small muted">+ ${e.assists.map(esc).join(", ")}</span>` : ""}</div>
        <div class="row">${chips.join(" ")}<span class="small muted">Replay: start at ${clock(Math.max(0, e.ts_ms - 15000))}</span></div></div>
      <div class="note"><input type="text" data-event="${e.id}" value="${esc(e.note)}" placeholder="Note (optional): what happened?"></div>
    </div>`;
  }).join("");

  view.innerHTML = `
    <section class="card stack">
      <div class="row spread">
        <div class="row">${champ(g.champion, "lg")}<span class="vs">vs</span>${champ(g.opponent, "lg")}
          <div><h1>${esc(g.champion)} vs ${esc(g.opponent || "unknown")}</h1>
          <div class="row small muted">${resultBadge(g)} ${esc(g.queue_name)} · ${when(g.game_start)} · ${clock(g.duration_s * 1000)} · patch ${esc(g.patch)} ${gameFlags(g)}</div></div></div>
        <a class="btn" href="#/">← Back</a>
      </div>
      <div class="row small">
        <span class="muted">Lane opponent${g.opponent_manual ? " (set by you)" : " (from Riot data)"}:</span>
        <select id="opponent" style="width:auto">${enemies.map((p) => `<option value="${esc(p.champion)}" ${p.champion === g.opponent ? "selected" : ""}>${esc(p.champion)} (${esc(p.role || "?")})</option>`).join("")}</select>
        ${g.opponent_manual ? `<button class="link" id="opponent-reset">Use Riot's (${esc(g.opponent_riot)})</button>` : ""}
      </div>
      <div class="tiles">${tiles.map(([l, v]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
    </section>

    ${g.plan?.note ? `<section class="card highlight"><h3>Your pre-game plan</h3><p class="plan-note">${esc(g.plan.note)}</p>
      <p class="muted small">Did it work? Say so in the review below.</p></section>` : ""}

    <section class="card"><h2>Kills &amp; deaths</h2>
      ${events || `<p class="muted">No kills or deaths this game.</p>`}
    </section>

    <section class="card" id="review"><h2>Review</h2>
      <div class="question"><div class="q">How did lane go?</div>${choiceGroup("lane_result", ["won", "even", "lost"], r.lane_result)}</div>
      <div class="question"><div class="q">What decided it?</div>${choiceGroup("lane_reason", ["matchup", "jungler", "mistakes", "team"], r.lane_reason)}</div>
      <div class="question"><div class="q">Did waves 1–3 go to plan?</div>${choiceGroup("waves_plan", ["yes", "partly", "no"], r.waves_plan)}</div>
      ${session ? `<div class="question"><div class="q">How much attention did you give your focus? <span class="muted">(${esc(session.focus_label)})</span></div>
        ${stars("focus_rating", r.focus_rating)}
        <input type="text" id="focus_note" value="${esc(r.focus_note)}" placeholder="Focus note (optional)"></div>` : ""}
      <div class="question"><label class="q">Notes — trades, griefed lane, where the game broke…
        <textarea id="note" placeholder="Anything worth remembering from this game">${esc(r.note)}</textarea></label></div>
      <div class="row"><button class="primary" id="save">Save review</button>
        ${g.review ? "" : `<button id="skip">Skip this game</button>`}
        <span class="muted small">${g.review && !g.review.skipped ? "Saved " + when(g.review.reviewed_at) : ""}</span></div>
    </section>`;

  const values = bindChoices(view.querySelector("#review"));

  view.querySelectorAll("input[data-event]").forEach((input) => {
    let saved = input.value;
    input.addEventListener("blur", async () => {
      if (input.value === saved) return;
      try { await api(`/api/events/${input.dataset.event}/note`, { method: "PUT", body: { note: input.value } }); saved = input.value; toast("Note saved"); }
      catch (err) { toast(err.message, true); }
    });
  });

  view.querySelector("#opponent").addEventListener("change", async (e) => {
    try { await api(`/api/games/${encodeURIComponent(matchId)}/opponent`, { method: "PUT", body: { champion: e.target.value } }); renderGame(matchId); }
    catch (err) { toast(err.message, true); }
  });
  view.querySelector("#opponent-reset")?.addEventListener("click", async () => {
    await api(`/api/games/${encodeURIComponent(matchId)}/opponent`, { method: "PUT", body: { champion: null } }); renderGame(matchId);
  });

  const afterSave = async () => {
    status = await api("/api/status");
    const active = status.active_session;
    if (active && g.session_id === active.session.id && active.solo_games >= active.session.target_games) {
      location.hash = `#/wrap/${active.session.id}`;
    } else {
      location.hash = "#/";
    }
  };

  view.querySelector("#save").addEventListener("click", async () => {
    // Note fields save on blur; make sure the last one typed in is saved too.
    document.activeElement?.blur();
    const body = { ...values(), note: view.querySelector("#note").value || null, focus_note: view.querySelector("#focus_note")?.value || null };
    try { await api(`/api/games/${encodeURIComponent(matchId)}/review`, { method: "PUT", body }); toast("Review saved"); afterSave(); }
    catch (err) { toast(err.message, true); }
  });
  view.querySelector("#skip")?.addEventListener("click", async () => {
    await api(`/api/games/${encodeURIComponent(matchId)}/review`, { method: "PUT", body: { skipped: true } });
    afterSave();
  });
}

async function renderWrap(sessionId) {
  const s = await api(`/api/sessions/${sessionId}`);
  const sess = s.session;
  const tiles = [
    ["Solo games", s.solo_games],
    ["Record", `${s.wins}W ${s.losses}L`],
    ["Avg focus attention", s.avg_focus_rating ? `${s.avg_focus_rating} ★` : "–"],
    ["Reviewed", `${s.reviewed} / ${s.games.length}`],
  ];
  if (s.metric_label) tiles.push([`Avg ${s.metric_label}`, s.avg_metric ?? "–"]);

  view.innerHTML = `
    <section class="card stack">
      <div class="row spread"><div><h3>Session wrap-up</h3><h1>${esc(sess.focus_label)}</h1>
        <div class="muted small">${when(sess.started_at)}</div></div><a class="btn" href="#/">← Back</a></div>
      <div class="tiles">${tiles.map(([l, v]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
      ${gamesTable(s.games, { showFocus: true })}
    </section>
    <section class="card" id="wrap">
      <div class="question"><div class="q">How did the session go overall?</div>${stars("rating", sess.wrap_rating)}</div>
      <div class="question"><label class="q">One or two lines: what did you learn?
        <textarea id="wrap-note" placeholder="e.g. Tracking the jungler stopped two ganks, but I forgot about it after first back">${esc(sess.wrap_note)}</textarea></label></div>
      <div class="question"><div class="q">Keep "${esc(sess.focus_label)}" as the focus next session?</div>
        ${choiceGroup("keep", ["yes", "no"], sess.keep_focus == null ? null : sess.keep_focus ? "yes" : "no")}</div>
      <div class="row"><button class="primary" id="finish">${sess.ended_at ? "Save wrap-up" : "End session"}</button></div>
    </section>`;
  bindRowLinks(view);
  const values = bindChoices(view.querySelector("#wrap"));
  view.querySelector("#finish").addEventListener("click", async () => {
    const v = values();
    try {
      await api(`/api/sessions/${sessionId}/wrap`, { method: "POST", body: {
        rating: v.rating, note: view.querySelector("#wrap-note").value || null,
        keep_focus: v.keep == null ? null : v.keep === "yes",
      } });
      toast("Session saved. GG.");
      location.hash = "#/";
    } catch (err) { toast(err.message, true); }
  });
}

async function renderGames() {
  const games = await api("/api/games");
  const counted = games.filter((g) => g.counts);
  const wins = counted.filter((g) => g.win).length;
  view.innerHTML = `<section class="card stack">
    <div class="row spread"><h1>All games</h1><button id="sync">Check for new games</button></div>
    <p class="muted small">Ranked solo games count toward stats (${wins}W ${counted.length - wins}L across ${counted.length}). Other queues and remakes are kept but greyed out.</p>
    ${gamesTable(games, { showFocus: true })}</section>`;
  bindRowLinks(view);
  view.querySelector("#sync").addEventListener("click", async () => { await syncNow(); renderGames(); });
}

// ---------- League client: live polling ----------
const STAGE_TEXT = { champ_select: "Champ select", in_game: "In game", ended: "Game over" };

function updateClientPill() {
  const pill = document.getElementById("client-pill");
  if (!live?.enabled) { pill.hidden = true; return; }
  let text = live.client === "connected" ? "Client connected" : "Client offline";
  let cls = live.client === "connected" ? "" : "off";
  if (STAGE_TEXT[live.stage]) { text = STAGE_TEXT[live.stage]; cls = "on"; }
  if (live.stage !== "champ_select" && live.stage !== "in_game" && live.review?.status === "waiting") {
    text = "Waiting for match data…"; cls = "on";
  }
  pill.textContent = text;
  pill.className = `pill ${cls}`;
  pill.title = live.message || "";
  pill.hidden = false;
}

async function pollLive() {
  try { live = await api("/api/live"); } catch { live = null; }
  updateClientPill();
  if (!live?.enabled) return;
  const g = live.game;
  const hash = location.hash || "#/";
  const onHome = hash === "#/";
  const onPlan = hash.startsWith("#/plan");

  // Champ select started (or the game is already running): open the plan screen once per game.
  if (g && !g.ended && (live.stage === "champ_select" || live.stage === "in_game") && autoPlanKey !== g.key) {
    autoPlanKey = g.key;
    if (onHome) { location.hash = "#/plan"; return; }
  }
  // Game over and imported: open its review once.
  const r = live.review;
  if (r?.status === "ready" && !r.opened) {
    api("/api/live/review-opened", { method: "POST", body: { match_id: r.match_id } }).catch(() => {});
    if (onHome || onPlan) { location.hash = `#/game/${encodeURIComponent(r.match_id)}`; return; }
    toast("Your last game is imported and ready to review");
  }
  if (onPlan) refreshPlan();
}

// ---------- pre-game plan ----------
const POSITION_TEXT = { TOP: "Top", JUNGLE: "Jungle", MIDDLE: "Mid", BOTTOM: "Bot", UTILITY: "Support" };
const fmtCd = (cd, haste) => {
  if (cd == null) return "–";
  const v = Math.round((cd * 100) / (100 + (Number(haste) || 0)) * 10) / 10;
  return String(v);
};
const imgUrl = (version, path) => `https://ddragon.leagueoflegends.com/cdn/${version}/img/${path}`;

function spellIcons(spells, version) {
  return (spells || []).map((s) => {
    const label = `${s.name || "?"}${s.cooldown ? ` · ${s.cooldown}s` : ""}`;
    return s.image && version
      ? `<img class="spell" src="${imgUrl(version, s.image)}" alt="${esc(s.name)}" title="${esc(label)}">`
      : `<span class="chip" title="${esc(label)}">${esc(s.name || "?")}</span>`;
  }).join("");
}

async function getMatchup(me, vs) {
  const key = `${me || ""}|${vs || ""}`;
  if (!matchupCache[key]) {
    const params = new URLSearchParams();
    if (me) params.set("me", me);
    if (vs) params.set("vs", vs);
    matchupCache[key] = api(`/api/matchup?${params}`).catch((err) => { delete matchupCache[key]; throw err; });
  }
  return matchupCache[key];
}

function cooldownTable(champion, version, haste, side) {
  if (!champion) return `<div class="cd-card"><p class="muted">No champion yet.</p></div>`;
  const ranks = Math.max(...champion.spells.map((s) => s.cooldowns.length), 1);
  const rows = champion.spells.map((s) => `
    <tr>
      <td class="cd-key"><img class="spell" src="${imgUrl(version, s.image)}" alt=""><b>${s.key}</b></td>
      <td class="cd-name" title="${esc(s.description)}">${esc(s.name)}${s.charges ? ` <span class="chip" title="Uses charges: the cooldown shown may be the recharge time">${s.charges} charges</span>` : ""}</td>
      ${Array.from({ length: ranks }, (_, i) => `<td class="num">${i < s.cooldowns.length ? fmtCd(s.cooldowns[i], haste) : ""}</td>`).join("")}
    </tr>`).join("");
  const p = champion.passive;
  return `<div class="cd-card">
    <div class="row spread"><div class="row">${champ(champion.id, "")}<div><b>${esc(champion.name)}</b>
      <div class="small muted">${side === "me" ? "You" : "Lane opponent"}</div></div></div>
      <label class="haste small">Ability haste <input type="number" min="0" max="300" step="5" data-haste="${side}" value="${Number(haste) || 0}"></label></div>
    <div class="table-wrap"><table class="cd">
      <thead><tr><th colspan="2"></th>${Array.from({ length: ranks }, (_, i) => `<th class="num">Rank ${i + 1}</th>`).join("")}</tr></thead>
      <tbody>${rows}
        <tr class="dim-row"><td class="cd-key">${p.image ? `<img class="spell" src="${imgUrl(version, p.image)}" alt="">` : ""}<b>P</b></td>
        <td class="cd-name" title="${esc(p.description)}" colspan="${ranks + 1}">${esc(p.name)} <span class="small muted">(passive · hover for details)</span></td></tr>
      </tbody></table></div>
  </div>`;
}

function historyBlock(h, myChampion) {
  if (!h) return "";
  const name = (id) => esc(id || "?");
  if (!h.all.games) return `<p class="muted small">No ranked solo games against ${name(h.opponent)} yet.</p>`;
  const line = (s) => `${s.games} game${s.games === 1 ? "" : "s"} · ${s.wins}W ${s.games - s.wins}L · CS diff @10 <span class="${signClass(s.avg_csd10)}">${signed(s.avg_csd10)}</span> · gold diff @10 <span class="${signClass(s.avg_gd10)}">${signed(s.avg_gd10)}</span>`;
  const recent = h.recent.map((g) => {
    const notes = [g.plan_note && `<div><span class="muted">Plan:</span> ${esc(g.plan_note)}</div>`,
      g.note && `<div><span class="muted">Review:</span> ${esc(g.note)}</div>`].filter(Boolean).join("");
    return `<div class="hist-game">
      <div class="row">${g.win ? `<span class="badge win">WIN</span>` : `<span class="badge loss">LOSS</span>`}
        ${champ(g.champion, "sm")}<span class="small">${esc(g.champion)}</span>
        <span class="small ${signClass(g.csd10)}">CS ${signed(g.csd10)} @10</span>
        ${g.lane_result ? `<span class="chip">lane ${esc(LABELS[g.lane_result] || g.lane_result)}${g.lane_reason ? ` · ${esc(LABELS[g.lane_reason] || g.lane_reason)}` : ""}</span>` : ""}
        <a class="small muted" href="#/game/${encodeURIComponent(g.match_id)}">${when(g.game_start)}</a></div>
      ${notes ? `<div class="small hist-notes">${notes}</div>` : ""}</div>`;
  }).join("");
  return `<div class="stack">
    <div class="small"><b>vs ${name(h.opponent)}:</b> ${line(h.all)}</div>
    ${h.on_champion && h.on_champion.games ? `<div class="small"><b>On ${name(myChampion)}:</b> ${line(h.on_champion)}</div>` : ""}
    ${recent}</div>`;
}

async function renderMatchupInto(el, me, vs, { hasteMe = 0, hasteVs = 0 } = {}) {
  const pair = `${me || ""}|${vs || ""}`;
  el.dataset.pair = pair;
  if (!me && !vs) { el.innerHTML = ""; return; }
  el.innerHTML = `<p class="muted small">Loading cooldowns…</p>`;
  try {
    const m = await getMatchup(me, vs);
    if (el.dataset.pair !== pair) return; // a newer matchup was requested meanwhile
    el.innerHTML = `
      ${vs ? `<div class="stack"><h2>History in this matchup</h2>${historyBlock(m.history, me)}</div>` : ""}
      <div class="stack"><h2>Cooldowns</h2>
        <div class="cd-grid">${cooldownTable(m.me, m.version, hasteMe, "me")}${cooldownTable(m.vs, m.version, hasteVs, "vs")}</div>
        <p class="muted small">Base cooldowns in seconds from Data Dragon (patch ${esc(m.version)}), with the ability haste you enter applied.
          Passives, charge-based abilities and some reworked spells can differ from what you see in game.</p></div>`;
    // Ability haste inputs recompute in place.
    el.querySelectorAll("[data-haste]").forEach((input) => input.addEventListener("input", () => {
      const champion = input.dataset.haste === "me" ? m.me : m.vs;
      const table = input.closest(".cd-card").querySelector("tbody");
      table.querySelectorAll("tr:not(.dim-row)").forEach((tr, i) => {
        tr.querySelectorAll("td.num").forEach((td, r) => {
          const cd = champion.spells[i].cooldowns[r];
          if (cd != null) td.textContent = fmtCd(cd, input.value);
        });
      });
    }));
  } catch (err) {
    if (el.dataset.pair === pair) el.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

async function renderPlan() {
  planState = {};
  if (!status) { try { status = await api("/api/status"); } catch { /* focus reminder is optional */ } }
  view.innerHTML = `<div id="plan-live" class="stack"></div>
    <section class="card stack" id="lookup">
      <h2>Look up a matchup</h2>
      <div class="row lookup-row">
        <label class="small">You <input type="text" id="lookup-me" list="champion-list" placeholder="e.g. Renekton"></label>
        <span class="vs">vs</span>
        <label class="small">Opponent <input type="text" id="lookup-vs" list="champion-list" placeholder="e.g. Darius"></label>
        <button id="lookup-go">Compare</button>
      </div>
      <datalist id="champion-list"></datalist>
      <div id="lookup-result" class="stack"></div>
    </section>`;
  api("/api/champions").then((d) => {
    view.querySelector("#champion-list").innerHTML = d.champions.map((c) => `<option value="${esc(c.name)}">`).join("");
  }).catch(() => {});
  const go = () => renderMatchupInto(view.querySelector("#lookup-result"),
    view.querySelector("#lookup-me").value.trim(), view.querySelector("#lookup-vs").value.trim());
  view.querySelector("#lookup-go").addEventListener("click", go);
  view.querySelectorAll("#lookup input").forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }));
  if (!live) { try { live = await api("/api/live"); } catch { live = null; } }
  refreshPlan();
}

function planStageLine(g) {
  if (live.stage === "champ_select") return "Champ select · enemy picks appear as they lock in";
  if (live.stage === "in_game") {
    if (!g.live) return "Loading into the game…";
    return `In game · ${clock((g.game_time || 0) * 1000)}${g.level ? ` · level ${g.level}` : ""}`;
  }
  const r = live.review;
  if (r?.status === "waiting") return `Game over · waiting for Riot's match data (usually 1–3 minutes; ${r.attempts} check${r.attempts === 1 ? "" : "s"} so far)${r.error ? ` · ${r.error}` : ""}`;
  if (r?.status === "gave_up") return `Game over · the match didn't show up in the Riot API yet${r.error ? ` (${r.error})` : ""}. Use "Check for new games" on the Session page.`;
  if (r?.status === "ready") return "Game over · imported";
  return "Game over";
}

function refreshPlan() {
  const root = view.querySelector("#plan-live");
  if (!root || !live) return;
  const g = live.game;

  if (!live.enabled || !g) {
    const msg = !live.enabled ? "Following the League client is switched off (WATCH_CLIENT in .env)."
      : live.client === "connected" ? "No champ select right now. This screen opens by itself when champ select starts, as long as this page is on the Session screen."
      : `League client not found: ${live.message || "is it running?"} This screen opens by itself once champ select starts.`;
    const sig = `none|${msg}`;
    if (planState.sig === sig) return;
    planState = { sig };
    root.innerHTML = `<section class="card"><h2>Pre-game</h2><p class="muted">${esc(msg)}</p></section>`;
    return;
  }

  const me = g.me;
  const opp = g.enemies.find((e) => e.champion && e.champion === g.opponent);
  const sig = JSON.stringify([g.key, live.stage, g.live, g.game_time && Math.floor(g.game_time / 5), g.level, me, g.enemies, g.allies,
    g.opponent, g.opponent_source, live.review?.status, live.review?.attempts, live.plan?.id]);
  if (planState.sig === sig) return;

  const version = ddragonVersion;
  const sourceText = { live: "from in-game positions", history: "best guess from your history, click to change", manual: "picked by you" }[g.opponent_source]
    || (g.enemies.some((e) => e.champion) ? "click your lane opponent" : "waiting for enemy picks");
  const enemyButtons = g.enemies.map((e) => e.champion
    ? `<button type="button" class="enemy ${e.champion === g.opponent ? "on" : ""}" data-opponent="${esc(e.champion)}" title="${esc(e.name || e.champion)}${e.position ? ` (${POSITION_TEXT[e.position] || e.position})` : ""}">
        ${champ(e.champion, "")}<span class="small">${esc(e.name || e.champion)}</span>
        ${e.position ? `<span class="small muted">${esc(POSITION_TEXT[e.position] || e.position)}</span>` : ""}
        <span class="spells">${spellIcons(e.spells, version)}</span></button>`
    : `<span class="enemy empty">${champ(null, "")}<span class="small muted">picking…</span></span>`).join("");

  const head = `<section class="card highlight stack">
      <div class="small muted">${esc(planStageLine(g))}</div>
      <div class="row">${champ(me.champion, "lg")}<span class="vs">vs</span>${champ(g.opponent, "lg")}
        <div><h1>${esc(me.name || me.champion || "Picking…")}${me.hovering ? ` <span class="small muted">(hovering)</span>` : ""} vs ${esc(opp?.name || g.opponent || "?")}</h1>
        <div class="row small muted">${me.position ? `${esc(POSITION_TEXT[me.position] || me.position)} · ` : ""}your spells ${spellIcons(me.spells, version) || "–"}
          ${opp && opp.spells.length ? ` · their spells ${spellIcons(opp.spells, version)}` : ""}</div></div></div>
      <div class="stack"><div class="small"><b>Enemy team</b> <span class="muted">· ${esc(sourceText)}</span>
        ${g.opponent_source === "manual" ? `<button class="link small" id="opponent-auto">use automatic</button>` : ""}</div>
        <div class="enemies">${enemyButtons || `<span class="muted small">No enemy picks yet.</span>`}</div></div>
    </section>`;

  const focus = status?.active_session?.session?.focus_label;
  const planId = live.plan?.id;
  const firstRender = !root.querySelector("#plan-head");
  if (firstRender) {
    root.innerHTML = `<div id="plan-head"></div><section class="card stack" id="plan-notes"></section><section class="card stack" id="plan-matchup"></section>`;
  }
  root.querySelector("#plan-head").innerHTML = head;

  // Notes: rendered once per plan so typing is never interrupted by a refresh.
  if (firstRender || planState.planId !== planId) {
    const notes = root.querySelector("#plan-notes");
    notes.innerHTML = `
      ${focus ? `<div class="focus-banner"><div><h3>Session focus</h3><div class="focus">${esc(focus)}</div></div></div>` : ""}
      <label class="q">Your plan for this game
        <textarea id="plan-note" placeholder="e.g. Level 1: respect his E range. Trade when W is down (check the cooldowns below). Ward river at 2:45 for the jungler."
          ${planId ? "" : "disabled"}>${esc(live.plan?.note)}</textarea></label>
      <div class="small muted" id="plan-saved">${planId ? "Saved automatically; it shows up on this game's review." : "You can write a plan once you've picked (or hovered) a champion."}</div>`;
    const area = notes.querySelector("#plan-note");
    let timer = null;
    const save = async () => {
      try { await api(`/api/plans/${planId}`, { method: "PUT", body: { note: area.value } }); notes.querySelector("#plan-saved").textContent = "Saved."; }
      catch (err) { notes.querySelector("#plan-saved").textContent = err.message; }
    };
    area.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(save, 700); });
    area.addEventListener("blur", () => { clearTimeout(timer); if (planId) save(); });
  }

  // Cooldowns + history: reload only when the matchup changes; ability haste follows the live game.
  const pair = `${me.champion || ""}|${g.opponent || ""}`;
  const matchupEl = root.querySelector("#plan-matchup");
  if (firstRender || planState.pair !== pair) {
    renderMatchupInto(matchupEl, me.champion, g.opponent, { hasteMe: g.ability_haste || 0 });
  } else if (g.ability_haste != null && planState.haste !== g.ability_haste) {
    const input = matchupEl.querySelector('[data-haste="me"]');
    if (input && document.activeElement !== input) { input.value = g.ability_haste; input.dispatchEvent(new Event("input")); }
  }
  matchupEl.hidden = !me.champion && !g.opponent;

  root.querySelectorAll("[data-opponent]").forEach((b) => b.addEventListener("click", () => setPlanOpponent(planId, b.dataset.opponent)));
  root.querySelector("#opponent-auto")?.addEventListener("click", () => setPlanOpponent(planId, null));
  planState = { sig, planId, pair, haste: g.ability_haste };
}

async function setPlanOpponent(planId, champion) {
  if (!planId) return toast("Pick a champion first", true);
  try {
    await api(`/api/plans/${planId}`, { method: "PUT", body: { opponent: champion } });
    live = await api("/api/live");
    refreshPlan();
  } catch (err) { toast(err.message, true); }
}

// ---------- router ----------
async function render() {
  const hash = location.hash || "#/";
  const section = hash.startsWith("#/games") ? "games" : hash.startsWith("#/plan") ? "plan" : "home";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  try {
    let m;
    if ((m = hash.match(/^#\/game\/(.+)$/))) await renderGame(decodeURIComponent(m[1]));
    else if ((m = hash.match(/^#\/wrap\/(\d+)$/))) await renderWrap(m[1]);
    else if (hash.startsWith("#/games")) await renderGames();
    else if (hash.startsWith("#/plan")) await renderPlan();
    else await renderHome();
  } catch (err) {
    view.innerHTML = `<section class="card warn"><h2>Something went wrong</h2><p>${esc(err.message)}</p><a class="btn" href="#/">Home</a></section>`;
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", render);
loadDdragonVersion().then(render).then(pollLive);
setInterval(poll, POLL_MS);
setInterval(pollLive, LIVE_POLL_MS);
