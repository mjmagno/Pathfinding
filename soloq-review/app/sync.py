"""Pull new matches from the Riot API into the database."""

import threading

from . import db
from .analysis import analyze
from .config import Settings
from .riot import RiotApiError, RiotClient

_sync_lock = threading.Lock()


class SyncError(Exception):
    pass


def get_puuid(conn, settings: Settings, client: RiotClient) -> str:
    cache_key = f"puuid:{settings.riot_id.lower()}"
    puuid = db.get_meta(conn, cache_key)
    if puuid:
        return puuid
    try:
        puuid = client.get_puuid(settings.game_name, settings.tag_line)
    except RiotApiError as err:
        if err.status == 404:
            raise SyncError(f"Riot ID '{settings.riot_id}' was not found. Check RIOT_ID and REGION in .env.") from err
        raise
    db.set_meta(conn, cache_key, puuid)
    conn.commit()
    return puuid


def sync(conn, settings: Settings, client: RiotClient | None = None, count: int = 20) -> dict:
    """Import any of the last `count` matches that aren't stored yet."""
    problems = settings.problems()
    if problems:
        raise SyncError("; ".join(problems))
    client = client or RiotClient(settings.api_key, settings.region)

    # Don't run two syncs at once (the page polls, and you might also click the button).
    if not _sync_lock.acquire(blocking=False):
        return {"imported": [], "busy": True, "errors": []}
    try:
        try:
            puuid = get_puuid(conn, settings, client)
            match_ids = client.get_match_ids(puuid, count=count)
        except RiotApiError as err:
            raise SyncError(str(err)) from err

        imported, errors = [], []
        # Oldest first, so sessions are filled in game order.
        for match_id in reversed(match_ids):
            if db.has_game(conn, match_id):
                continue
            try:
                match = client.get_match(match_id)
                timeline = client.get_timeline(match_id)
                db.save_game(conn, analyze(match, timeline, puuid), match, timeline)
                imported.append(match_id)
            except RiotApiError as err:
                if err.status in (401, 403):
                    raise SyncError(str(err)) from err
                errors.append(f"{match_id}: {err}")
            except (KeyError, ValueError, TypeError) as err:
                errors.append(f"{match_id}: could not read match data ({err})")
        db.set_meta(conn, "last_sync_ms", str(db.now_ms()))
        conn.commit()
        return {"imported": imported, "busy": False, "errors": errors}
    finally:
        _sync_lock.release()
