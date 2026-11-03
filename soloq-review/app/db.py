"""SQLite storage. Schema changes are applied as numbered migrations."""

import json
import sqlite3
import time
from pathlib import Path

MIGRATIONS = [
    # 1: initial schema
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at INTEGER NOT NULL,          -- epoch ms
        ended_at INTEGER,                     -- NULL while active
        auto_ended INTEGER NOT NULL DEFAULT 0,
        focus_key TEXT NOT NULL,
        focus_label TEXT NOT NULL,
        target_games INTEGER NOT NULL DEFAULT 3,
        wrap_note TEXT,
        wrap_rating INTEGER,                  -- 1-5 how the session went overall
        keep_focus INTEGER,                   -- 1 / 0 / NULL (not answered)
        wrapped_at INTEGER
    );

    CREATE TABLE games (
        match_id TEXT PRIMARY KEY,
        session_id INTEGER REFERENCES sessions(id),
        queue_id INTEGER, is_solo INTEGER, game_start INTEGER, game_end INTEGER,
        duration_s INTEGER, patch TEXT, champion TEXT, role TEXT,
        off_role INTEGER, remake INTEGER, win INTEGER,
        kills INTEGER, deaths INTEGER, assists INTEGER, cs INTEGER,
        opponent TEXT, opponent_riot TEXT, opponent_manual INTEGER DEFAULT 0,
        my_jungler TEXT, enemy_jungler TEXT,
        cs10 INTEGER, csd10 INTEGER, gd10 INTEGER, xpd10 INTEGER,
        cs14 INTEGER, csd14 INTEGER, gd14 INTEGER, xpd14 INTEGER,
        deaths_pre14 INTEGER, solo_deaths_pre14 INTEGER,
        deaths_enemy_jg INTEGER, deaths_enemy_jg_pre14 INTEGER,
        kills_with_my_jg INTEGER, kills_with_my_jg_pre14 INTEGER,
        participants_json TEXT,
        imported_at INTEGER
    );
    CREATE INDEX games_start ON games(game_start);

    CREATE TABLE raw_matches (
        match_id TEXT PRIMARY KEY,
        match_json TEXT NOT NULL,
        timeline_json TEXT NOT NULL
    );

    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL REFERENCES games(match_id),
        ts_ms INTEGER NOT NULL,
        kind TEXT NOT NULL,                   -- 'kill' | 'death'
        killer TEXT, victim TEXT, assists_json TEXT,
        x INTEGER, y INTEGER,
        enemy_jg_involved INTEGER, my_jg_involved INTEGER, solo INTEGER,
        note TEXT
    );
    CREATE INDEX events_match ON events(match_id);

    CREATE TABLE reviews (
        match_id TEXT PRIMARY KEY REFERENCES games(match_id),
        lane_result TEXT,                     -- won | even | lost
        lane_reason TEXT,                     -- matchup | jungler | mistakes | team
        waves_plan TEXT,                      -- yes | partly | no
        focus_rating INTEGER,                 -- 1-5 attention given to the focus
        focus_note TEXT,
        note TEXT,
        skipped INTEGER NOT NULL DEFAULT 0,
        reviewed_at INTEGER
    );
    """,
    # 2: pre-game plans, written by the League client watcher
    """
    CREATE TABLE plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT UNIQUE,                  -- Riot gameId; the match id is <PLATFORM>_<gameId>
        session_id INTEGER REFERENCES sessions(id),
        created_at INTEGER NOT NULL,
        queue_id INTEGER,
        champion TEXT, position TEXT,
        opponent TEXT, opponent_source TEXT,  -- live | history | manual
        allies_json TEXT, enemies_json TEXT,
        note TEXT
    );
    """,
]

# Preset focuses. "metric" names a games column shown next to adherence.
FOCUS_PRESETS = [
    {"key": "cs", "label": "CSing", "metric": "cs10", "metric_label": "CS @ 10"},
    {"key": "jungle_tracking", "label": "Jungle tracking", "metric": "deaths_enemy_jg_pre14",
     "metric_label": "Deaths to enemy jungler before 14:00"},
    {"key": "trading", "label": "Trading around cooldowns", "metric": None, "metric_label": None},
    {"key": "waves", "label": "Wave management", "metric": None, "metric_label": None},
    {"key": "backs", "label": "Back timing", "metric": None, "metric_label": None},
    {"key": "no_early_deaths", "label": "Not dying early", "metric": "deaths_pre14",
     "metric_label": "Deaths before 14:00"},
    {"key": "mental", "label": "Mental / tilt", "metric": None, "metric_label": None},
]
PRESETS_BY_KEY = {p["key"]: p for p in FOCUS_PRESETS}

GAME_COLUMNS = [
    "match_id", "queue_id", "is_solo", "game_start", "game_end", "duration_s", "patch",
    "champion", "role", "off_role", "remake", "win", "kills", "deaths", "assists", "cs",
    "my_jungler", "enemy_jungler", "cs10", "csd10", "gd10", "xpd10", "cs14", "csd14",
    "gd14", "xpd14", "deaths_pre14", "solo_deaths_pre14", "deaths_enemy_jg",
    "deaths_enemy_jg_pre14", "kills_with_my_jg", "kills_with_my_jg_pre14",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {index}")
    if version == 0:
        # Games that finished before the tool was first used are history, not "needs review".
        set_meta(conn, "first_run_ms", str(now_ms()))
    conn.commit()


def get_meta(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- sessions ----------------------------------------------------------------

def _session_dict(conn, row) -> dict | None:
    if row is None:
        return None
    s = dict(row)
    s["preset"] = PRESETS_BY_KEY.get(s["focus_key"])
    return s


def close_stale_sessions(conn, idle_hours: float) -> None:
    """Auto-end an active session after `idle_hours` without a new game."""
    row = conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return
    last = conn.execute("SELECT MAX(game_end) FROM games WHERE session_id = ?", (row["id"],)).fetchone()[0]
    last_activity = max(row["started_at"], last or 0)
    if now_ms() - last_activity > idle_hours * 3600 * 1000:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, auto_ended = 1 WHERE id = ?",
            (last_activity, row["id"]),
        )
        conn.commit()


def active_session(conn) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return _session_dict(conn, row)


def last_session(conn) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return _session_dict(conn, row)


def get_session(conn, session_id: int) -> dict | None:
    return _session_dict(conn, conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())


def start_session(conn, focus_key: str, focus_label: str, target_games: int) -> dict:
    # Only one active session at a time.
    conn.execute("UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL", (now_ms(),))
    cur = conn.execute(
        "INSERT INTO sessions(started_at, focus_key, focus_label, target_games) VALUES(?, ?, ?, ?)",
        (now_ms(), focus_key, focus_label, target_games),
    )
    conn.commit()
    return get_session(conn, cur.lastrowid)


def wrap_session(conn, session_id: int, note: str | None, rating: int | None, keep_focus: bool | None) -> dict:
    conn.execute(
        """UPDATE sessions SET ended_at = COALESCE(ended_at, ?), wrap_note = ?, wrap_rating = ?,
           keep_focus = ?, wrapped_at = ? WHERE id = ?""",
        (now_ms(), note, rating, None if keep_focus is None else int(keep_focus), now_ms(), session_id),
    )
    conn.commit()
    return get_session(conn, session_id)


def session_summary(conn, session_id: int) -> dict:
    session = get_session(conn, session_id)
    games = session_games(conn, session_id)
    counted = [g for g in games if g["is_solo"] and not g["remake"]]
    ratings = [g["review"]["focus_rating"] for g in games
               if g.get("review") and g["review"].get("focus_rating")]
    metric = (session.get("preset") or {}).get("metric")
    metric_values = [g[metric] for g in counted if metric and g.get(metric) is not None]
    return {
        "session": session,
        "games": games,
        "solo_games": len(counted),
        "wins": sum(g["win"] for g in counted),
        "losses": sum(1 - g["win"] for g in counted),
        "avg_focus_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "metric": metric,
        "metric_label": (session.get("preset") or {}).get("metric_label"),
        "avg_metric": round(sum(metric_values) / len(metric_values), 1) if metric_values else None,
        "reviewed": sum(1 for g in games if g.get("review")),
    }


def session_for_time(conn, ts_ms: int) -> int | None:
    """The session that was running when a game ended (if any)."""
    row = conn.execute(
        """SELECT id FROM sessions WHERE started_at <= ?
           AND (ended_at IS NULL OR ? <= ended_at + 600000)
           ORDER BY id DESC LIMIT 1""",
        (ts_ms, ts_ms),
    ).fetchone()
    return row["id"] if row else None


# --- games -------------------------------------------------------------------

def has_game(conn, match_id: str) -> bool:
    return conn.execute("SELECT 1 FROM games WHERE match_id = ?", (match_id,)).fetchone() is not None


def save_game(conn, analyzed: dict, match: dict, timeline: dict) -> None:
    game = analyzed["game"]
    values = {col: game.get(col) for col in GAME_COLUMNS}
    values["opponent"] = game.get("opponent")
    values["opponent_riot"] = game.get("opponent")
    values["participants_json"] = json.dumps(analyzed["participants"])
    values["imported_at"] = now_ms()
    values["session_id"] = session_for_time(conn, game["game_end"])

    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    # Games are imported once; never overwrite (that would drop reviews and notes).
    cur = conn.execute(f"INSERT OR IGNORE INTO games({cols}) VALUES({marks})", list(values.values()))
    if cur.rowcount == 0:
        return
    conn.execute(
        "INSERT OR REPLACE INTO raw_matches(match_id, match_json, timeline_json) VALUES(?, ?, ?)",
        (game["match_id"], json.dumps(match), json.dumps(timeline)),
    )
    for ev in analyzed["events"]:
        conn.execute(
            """INSERT INTO events(match_id, ts_ms, kind, killer, victim, assists_json, x, y,
               enemy_jg_involved, my_jg_involved, solo) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (game["match_id"], ev["ts_ms"], ev["kind"], ev["killer"], ev["victim"],
             json.dumps(ev["assists"]), ev["x"], ev["y"], int(ev["enemy_jg_involved"]),
             int(ev["my_jg_involved"]), int(ev["solo"])),
        )
    conn.commit()


def _game_dict(conn, row, with_events: bool = False) -> dict:
    g = dict(row)
    g["participants"] = json.loads(g.pop("participants_json") or "[]")
    review = conn.execute("SELECT * FROM reviews WHERE match_id = ?", (g["match_id"],)).fetchone()
    g["review"] = dict(review) if review else None
    first_run = int(get_meta(conn, "first_run_ms", "0"))
    g["needs_review"] = bool(not review and not g["remake"] and g["game_end"] >= first_run)
    if with_events:
        rows = conn.execute("SELECT * FROM events WHERE match_id = ? ORDER BY ts_ms", (g["match_id"],))
        g["events"] = []
        for e in rows:
            e = dict(e)
            e["assists"] = json.loads(e.pop("assists_json") or "[]")
            g["events"].append(e)
    return g


def get_game(conn, match_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM games WHERE match_id = ?", (match_id,)).fetchone()
    return _game_dict(conn, row, with_events=True) if row else None


def list_games(conn, limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT * FROM games ORDER BY game_start DESC LIMIT ?", (limit,)).fetchall()
    return [_game_dict(conn, r) for r in rows]


def session_games(conn, session_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM games WHERE session_id = ? ORDER BY game_start", (session_id,)).fetchall()
    return [_game_dict(conn, r) for r in rows]


def set_opponent(conn, match_id: str, champion: str | None) -> None:
    """Manual override; passing None restores what Riot's data says."""
    if champion:
        conn.execute("UPDATE games SET opponent = ?, opponent_manual = 1 WHERE match_id = ?", (champion, match_id))
    else:
        conn.execute("UPDATE games SET opponent = opponent_riot, opponent_manual = 0 WHERE match_id = ?", (match_id,))
    conn.commit()


def save_review(conn, match_id: str, review: dict) -> None:
    fields = ["lane_result", "lane_reason", "waves_plan", "focus_rating", "focus_note", "note", "skipped"]
    values = [review.get(f) for f in fields]
    values[fields.index("skipped")] = int(bool(review.get("skipped")))
    conn.execute(
        f"""INSERT INTO reviews(match_id, {', '.join(fields)}, reviewed_at)
            VALUES(?, {', '.join('?' for _ in fields)}, ?)
            ON CONFLICT(match_id) DO UPDATE SET
            {', '.join(f'{f} = excluded.{f}' for f in fields)}, reviewed_at = excluded.reviewed_at""",
        [match_id, *values, now_ms()],
    )
    conn.commit()


def set_event_note(conn, event_id: int, note: str | None) -> bool:
    cur = conn.execute("UPDATE events SET note = ? WHERE id = ?", (note, event_id))
    conn.commit()
    return cur.rowcount > 0


def game_id_of(match_id: str) -> str:
    """NA1_5672582141 -> 5672582141 (the gameId the League client uses)."""
    return match_id.rsplit("_", 1)[-1]


def match_for_game_id(conn, game_id: str) -> str | None:
    rows = conn.execute("SELECT match_id FROM games WHERE match_id LIKE ?", (f"%{game_id}",)).fetchall()
    return next((r["match_id"] for r in rows if game_id_of(r["match_id"]) == str(game_id)), None)


# --- pre-game plans --------------------------------------------------------------

def _plan_dict(row) -> dict | None:
    if row is None:
        return None
    p = dict(row)
    p["allies"] = json.loads(p.pop("allies_json") or "[]")
    p["enemies"] = json.loads(p.pop("enemies_json") or "[]")
    return p


def get_plan(conn, plan_id: int) -> dict | None:
    return _plan_dict(conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone())


def plan_for_game_id(conn, game_id: str) -> dict | None:
    return _plan_dict(conn.execute("SELECT * FROM plans WHERE game_id = ?", (str(game_id),)).fetchone())


def save_watched_game(conn, game: dict) -> dict:
    """Create or update the plan row for a game the watcher is following.

    The note and a hand-picked opponent are never overwritten from here."""
    plan = None
    if game.get("plan_id"):
        plan = get_plan(conn, game["plan_id"])
    if plan is None and game.get("game_id"):
        plan = plan_for_game_id(conn, game["game_id"])   # e.g. after a restart mid-game
    champs = lambda players: json.dumps([{"champion": p["champion"], "position": p["position"]} for p in players])  # noqa: E731
    if plan is None:
        active = active_session(conn)
        cur = conn.execute(
            "INSERT INTO plans(created_at, session_id) VALUES(?, ?)",
            (now_ms(), active["id"] if active else None),
        )
        plan = {"id": cur.lastrowid, "opponent_source": None}
    if plan.get("opponent_source") == "manual":
        opponent, source = plan["opponent"], "manual"
    else:
        opponent, source = game.get("opponent"), game.get("opponent_source")
    conn.execute(
        """UPDATE plans SET game_id = COALESCE(?, game_id), queue_id = COALESCE(?, queue_id),
           champion = ?, position = ?, opponent = ?, opponent_source = ?,
           allies_json = ?, enemies_json = ? WHERE id = ?""",
        (game.get("game_id"), game.get("queue_id"), game["me"]["champion"], game["me"]["position"],
         opponent, source, champs(game["allies"]), champs(game["enemies"]), plan["id"]),
    )
    conn.commit()
    return get_plan(conn, plan["id"])


def update_plan(conn, plan_id: int, fields: dict) -> dict | None:
    """Fields from the page: note, and/or opponent (None = back to automatic)."""
    if "note" in fields:
        conn.execute("UPDATE plans SET note = ? WHERE id = ?", (fields["note"], plan_id))
    if "opponent" in fields:
        if fields["opponent"]:
            conn.execute("UPDATE plans SET opponent = ?, opponent_source = 'manual' WHERE id = ?",
                         (fields["opponent"], plan_id))
        else:
            conn.execute("UPDATE plans SET opponent = NULL, opponent_source = NULL WHERE id = ?", (plan_id,))
    conn.commit()
    return get_plan(conn, plan_id)


def guess_opponent(conn, champion: str | None, enemies: list[str]) -> str | None:
    """Before positions are known: the enemy you've laned against most often."""
    if not enemies:
        return None
    marks = ", ".join("?" for _ in enemies)
    row = conn.execute(
        f"""SELECT opponent, COUNT(*) AS n FROM games WHERE opponent IN ({marks}) AND remake = 0
            GROUP BY opponent ORDER BY n DESC, MAX(game_start) DESC LIMIT 1""",
        enemies,
    ).fetchone()
    return row["opponent"] if row else None


def matchup_history(conn, opponent: str, champion: str | None = None, limit: int = 5) -> dict:
    """Your ranked solo games against `opponent` (any champion, and on `champion`)."""
    rows = conn.execute(
        """SELECT * FROM games WHERE opponent = ? AND is_solo = 1 AND remake = 0
           ORDER BY game_start DESC""", (opponent,),
    ).fetchall()
    games = [_game_dict(conn, r) for r in rows]

    def summary(gs):
        csd = [g["csd10"] for g in gs if g["csd10"] is not None]
        gd = [g["gd10"] for g in gs if g["gd10"] is not None]
        return {
            "games": len(gs), "wins": sum(g["win"] for g in gs),
            "avg_csd10": round(sum(csd) / len(csd), 1) if csd else None,
            "avg_gd10": round(sum(gd) / len(gd)) if gd else None,
        }

    recent = []
    for g in games[:limit]:
        plan = plan_for_game_id(conn, game_id_of(g["match_id"]))
        recent.append({
            "match_id": g["match_id"], "champion": g["champion"], "win": g["win"], "game_start": g["game_start"],
            "csd10": g["csd10"], "deaths_pre14": g["deaths_pre14"],
            "lane_result": (g["review"] or {}).get("lane_result"),
            "lane_reason": (g["review"] or {}).get("lane_reason"),
            "note": (g["review"] or {}).get("note"),
            "plan_note": plan["note"] if plan else None,
        })
    return {
        "opponent": opponent,
        "all": summary(games),
        "on_champion": summary([g for g in games if g["champion"] == champion]) if champion else None,
        "recent": recent,
    }
