"""FastAPI app: JSON API under /api plus the static web page."""

import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .analysis import queue_name
from .config import ROOT, is_demo, load_settings
from .ddragon import DataDragon, DataDragonError
from .league import LeagueClient, LiveClient, find_install
from .sync import SyncError, sync
from .watcher import Watcher

STATIC = ROOT / "static"

# Returns the Riot client to use; None means the real API. Tests swap this out.
riot_client_factory = None
_demo_league = None
if is_demo():
    from .demo import FakeLeague, FakeRiotClient

    _demo_client = FakeRiotClient()
    riot_client_factory = lambda: _demo_client  # noqa: E731
    _demo_league = FakeLeague(_demo_client)

ddragon = DataDragon(ROOT / "cache" / "ddragon")
watcher: Watcher | None = None
run_watcher_thread = True  # tests drive the watcher by hand


# --- League client watcher callbacks (run on the watcher thread) -------------------

def _with_conn(fn):
    conn = db.connect(load_settings().db_path)
    try:
        return fn(conn)
    finally:
        conn.close()


def _sync_for_game(game_id: str) -> str | None:
    """Import new matches; return the match id of `game_id` once it has arrived."""
    def run(conn):
        if not db.match_for_game_id(conn, game_id):
            client = riot_client_factory() if riot_client_factory else None
            try:
                sync(conn, load_settings(), client)
            except SyncError as err:
                raise RuntimeError(f"Could not import the game: {err}") from err
        return db.match_for_game_id(conn, game_id)
    return _with_conn(run)


def make_watcher(league=None, live=None, clock=time.time) -> Watcher:
    if league is None and _demo_league is not None:
        league = live = _demo_league
    if league is None:
        league = LeagueClient(lambda: find_install(load_settings().league_path))
        live = LiveClient()
    w = Watcher(
        league, live, ddragon,
        on_game=lambda game: _with_conn(lambda c: db.save_watched_game(c, game)),
        on_game_end=_sync_for_game,
        guess_opponent=lambda champ, enemies: _with_conn(lambda c: db.guess_opponent(c, champ, enemies)),
        clock=clock,
    )
    if league is _demo_league:
        w.first_sync_delay_s, w.retry_sync_s = 3, 5
    return w


@asynccontextmanager
async def lifespan(_app):
    global watcher
    settings = load_settings()
    db.connect(settings.db_path).close()  # create / migrate the database up front
    watcher = make_watcher() if settings.watch_client else None
    if watcher and run_watcher_thread:
        watcher.start()
    yield
    if watcher:
        watcher.stop()


app = FastAPI(title="SoloQ Review", lifespan=lifespan)


def get_conn():
    conn = db.connect(load_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


def _decorate(game: dict) -> dict:
    game["queue_name"] = queue_name(game["queue_id"])
    game["counts"] = bool(game["is_solo"] and not game["remake"])
    return game


# --- status / sessions ---------------------------------------------------------

@app.get("/api/status")
def status(conn=Depends(get_conn)):
    settings = load_settings()
    db.close_stale_sessions(conn, settings.session_idle_hours)
    active = db.active_session(conn)
    last = db.last_session(conn)
    needs_review = [_decorate(g) for g in db.list_games(conn, 30) if g["needs_review"]]
    return {
        "setup_problems": settings.problems(),
        "riot_id": settings.riot_id,
        "demo": is_demo(),
        "focus_presets": db.FOCUS_PRESETS,
        "active_session": db.session_summary(conn, active["id"]) if active else None,
        # A session that ended on its own (idle) without a wrap-up can still be wrapped.
        "unwrapped_session": db.session_summary(conn, last["id"]) if last and not last["wrapped_at"] else None,
        "last_session": last,
        "needs_review": needs_review,
        "last_sync_ms": int(db.get_meta(conn, "last_sync_ms", "0")),
    }


class StartSession(BaseModel):
    focus_key: str
    focus_label: str | None = None
    target_games: int = Field(3, ge=1, le=10)


@app.post("/api/sessions")
def start_session(body: StartSession, conn=Depends(get_conn)):
    preset = db.PRESETS_BY_KEY.get(body.focus_key)
    if body.focus_key == "custom":
        label = (body.focus_label or "").strip()
        if not label:
            raise HTTPException(400, "Type a focus for the custom option")
    elif preset:
        label = preset["label"]
    else:
        raise HTTPException(400, "Unknown focus")
    session = db.start_session(conn, body.focus_key, label, body.target_games)
    return db.session_summary(conn, session["id"])


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int, conn=Depends(get_conn)):
    if not db.get_session(conn, session_id):
        raise HTTPException(404, "Session not found")
    summary = db.session_summary(conn, session_id)
    summary["games"] = [_decorate(g) for g in summary["games"]]
    return summary


class WrapSession(BaseModel):
    note: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    keep_focus: bool | None = None


@app.post("/api/sessions/{session_id}/wrap")
def wrap_session(session_id: int, body: WrapSession, conn=Depends(get_conn)):
    if not db.get_session(conn, session_id):
        raise HTTPException(404, "Session not found")
    db.wrap_session(conn, session_id, body.note, body.rating, body.keep_focus)
    return db.session_summary(conn, session_id)


# --- games -------------------------------------------------------------------

@app.post("/api/sync")
def run_sync(conn=Depends(get_conn)):
    try:
        client = riot_client_factory() if riot_client_factory else None
        return sync(conn, load_settings(), client)
    except SyncError as err:
        raise HTTPException(400, str(err)) from err


@app.get("/api/games")
def games(conn=Depends(get_conn)):
    return [_decorate(g) for g in db.list_games(conn, 200)]


@app.get("/api/games/{match_id}")
def game(match_id: str, conn=Depends(get_conn)):
    g = db.get_game(conn, match_id)
    if not g:
        raise HTTPException(404, "Game not found")
    g = _decorate(g)
    g["session"] = db.get_session(conn, g["session_id"]) if g["session_id"] else None
    g["plan"] = db.plan_for_game_id(conn, db.game_id_of(match_id))
    return g


class Review(BaseModel):
    lane_result: str | None = Field(None, pattern="^(won|even|lost)$")
    lane_reason: str | None = Field(None, pattern="^(matchup|jungler|mistakes|team)$")
    waves_plan: str | None = Field(None, pattern="^(yes|partly|no)$")
    focus_rating: int | None = Field(None, ge=1, le=5)
    focus_note: str | None = None
    note: str | None = None
    skipped: bool = False


@app.put("/api/games/{match_id}/review")
def save_review(match_id: str, body: Review, conn=Depends(get_conn)):
    if not db.has_game(conn, match_id):
        raise HTTPException(404, "Game not found")
    db.save_review(conn, match_id, body.model_dump())
    return game(match_id, conn)


class Opponent(BaseModel):
    champion: str | None = None


@app.put("/api/games/{match_id}/opponent")
def set_opponent(match_id: str, body: Opponent, conn=Depends(get_conn)):
    g = db.get_game(conn, match_id)
    if not g:
        raise HTTPException(404, "Game not found")
    enemies = {p["champion"] for p in g["participants"] if p["team"] == "enemy"}
    if body.champion and body.champion not in enemies:
        raise HTTPException(400, "That champion wasn't on the enemy team")
    db.set_opponent(conn, match_id, body.champion)
    return game(match_id, conn)


class EventNote(BaseModel):
    note: str | None = None


@app.put("/api/events/{event_id}/note")
def event_note(event_id: int, body: EventNote, conn=Depends(get_conn)):
    if not db.set_event_note(conn, event_id, (body.note or "").strip() or None):
        raise HTTPException(404, "Event not found")
    return {"ok": True}


# --- pre-game: League client, plans, matchups -------------------------------------

@app.get("/api/live")
def live(conn=Depends(get_conn)):
    if watcher is None:
        return {"enabled": False, "client": "disabled", "message": "WATCH_CLIENT is off in .env",
                "stage": "idle", "game": None, "plan": None, "review": None, "error": None}
    snap = watcher.snapshot()
    game = snap["game"]
    plan = db.get_plan(conn, game["plan_id"]) if game and game["plan_id"] else None
    review = snap["review"]
    if review:
        review = {k: review[k] for k in ("game_id", "status", "match_id", "attempts", "error", "opened")}
    return {"enabled": True, "client": snap["client"], "message": snap["message"], "phase": snap["phase"],
            "stage": snap["stage"], "game": game, "plan": plan, "review": review, "error": snap["error"]}


class ReviewOpened(BaseModel):
    match_id: str


@app.post("/api/live/review-opened")
def review_opened(body: ReviewOpened):
    if watcher:
        watcher.mark_review_opened(body.match_id)
    return {"ok": True}


class PlanUpdate(BaseModel):
    note: str | None = None
    opponent: str | None = None


@app.put("/api/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanUpdate, conn=Depends(get_conn)):
    plan = db.get_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    fields = body.model_dump(exclude_unset=True)
    if "note" in fields:
        fields["note"] = (fields["note"] or "").strip() or None
    if "opponent" in fields and fields["opponent"]:
        enemies = {e["champion"] for e in plan["enemies"]}
        if fields["opponent"] not in enemies:
            raise HTTPException(400, "That champion isn't on the enemy team")
    plan = db.update_plan(conn, plan_id, fields)
    if watcher and "opponent" in fields:
        watcher.set_opponent(plan_id, fields["opponent"])
    return plan


def _champion_or_404(value: str | None) -> str | None:
    if not value:
        return None
    cid = ddragon.resolve(value)
    if not cid:
        raise HTTPException(404, f"Unknown champion: {value}")
    return cid


@app.get("/api/champions")
def champions():
    try:
        return {"version": ddragon.version(), "champions": ddragon.champions()}
    except DataDragonError as err:
        raise HTTPException(502, str(err)) from err


@app.get("/api/matchup")
def matchup(me: str | None = None, vs: str | None = None, conn=Depends(get_conn)):
    """Cooldowns for both champions, and your history against `vs`."""
    try:
        me_id, vs_id = _champion_or_404(me), _champion_or_404(vs)
        return {
            "version": ddragon.version(),
            "me": ddragon.abilities(me_id) if me_id else None,
            "vs": ddragon.abilities(vs_id) if vs_id else None,
            "history": db.matchup_history(conn, vs_id, me_id) if vs_id else None,
        }
    except DataDragonError as err:
        raise HTTPException(502, str(err)) from err


# --- page --------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
