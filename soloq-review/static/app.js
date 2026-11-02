// SoloQ Review front end: hash-routed single page, no build step.

const view = document.getElementById("view");
const POLL_MS = 90_000;
let status = null;
let ddragonVersion = null;

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

// ---------- router ----------
async function render() {
  const hash = location.hash || "#/";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active",
    (a.dataset.nav === "games" && hash.startsWith("#/games")) || (a.dataset.nav === "home" && !hash.startsWith("#/games"))));
  try {
    let m;
    if ((m = hash.match(/^#\/game\/(.+)$/))) await renderGame(decodeURIComponent(m[1]));
    else if ((m = hash.match(/^#\/wrap\/(\d+)$/))) await renderWrap(m[1]);
    else if (hash.startsWith("#/games")) await renderGames();
    else await renderHome();
  } catch (err) {
    view.innerHTML = `<section class="card warn"><h2>Something went wrong</h2><p>${esc(err.message)}</p><a class="btn" href="#/">Home</a></section>`;
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", render);
loadDdragonVersion().then(render);
setInterval(poll, POLL_MS);
