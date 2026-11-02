"""FastAPI app: JSON API under /api plus the static web page."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .analysis import queue_name
from .config import is_demo, load_settings
from .sync import SyncError, sync

STATIC = Path(__file__).resolve().parent.parent / "static"

# Returns the Riot client to use; None means the real API. Tests swap this out.
riot_client_factory = None
if is_demo():
    from .demo import FakeRiotClient

    _demo_client = FakeRiotClient()
    riot_client_factory = lambda: _demo_client  # noqa: E731


@asynccontextmanager
async def lifespan(_app):
    db.connect(load_settings().db_path).close()  # create / migrate the database up front
    yield


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


# --- page --------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
