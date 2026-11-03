"""Watches the League client and keeps a picture of the current game.

A background thread calls `tick()` every couple of seconds:

- champ select: reads the picks (yours, your team's, the enemy's as they lock in)
- in game: reads the Live Client Data API (lane positions, summoner spells,
  your ability haste), which pins down your lane opponent
- game over: imports matches until that game shows up in the Riot API (usually
  1-3 minutes), so its review can open automatically

The web page reads `snapshot()` through /api/live. Anything the watcher learns
about a game is also saved as a "plan" row (see db.py), so your plan note and
lane opponent survive a restart and appear on the post-game review.
"""

import copy
import re
import threading
import time

from .ddragon import DataDragonError
from .league import ClientUnavailable

# LCU gameflow phase -> what the page shows. Anything else (None, Lobby,
# Matchmaking, ReadyCheck, ...) is "idle".
STAGES = {
    "ChampSelect": "champ_select",
    "GameStart": "in_game", "InProgress": "in_game", "Reconnect": "in_game",
    "WaitingForStats": "ended", "PreEndOfGame": "ended", "EndOfGame": "ended",
}
POSITIONS = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "mid": "MIDDLE",
             "bottom": "BOTTOM", "bot": "BOTTOM", "utility": "UTILITY", "support": "UTILITY"}

FIRST_SYNC_DELAY_S = 60   # match data reaches the Riot API 1-3 minutes after the game
RETRY_SYNC_S = 45
GIVE_UP_AFTER_S = 15 * 60

_SPELL_ID = re.compile(r"SummonerSpell_(\w+?)_DisplayName")


def _position(value) -> str:
    return POSITIONS.get(str(value or "").strip().lower(), "")


def _new_game(now: float) -> dict:
    return {
        "key": str(int(now * 1000)),   # identifies this game to the page before Riot's gameId is known
        "plan_id": None, "game_id": None, "queue_id": None,
        "stage": "idle", "ended": False,
        "me": {"champion": None, "name": None, "position": "", "spells": [], "hovering": False},
        "allies": [], "enemies": [],
        "opponent": None, "opponent_source": None,   # live | history | manual
        "live": False, "ability_haste": None, "level": None, "game_time": None,
    }


class Watcher:
    def __init__(self, league, live, ddragon, on_game=None, on_game_end=None,
                 guess_opponent=None, clock=time.time):
        """
        on_game(game) -> plan dict: save what is known about the game; returns the stored plan.
        on_game_end(game_id) -> match_id | None: import new matches, return this game's match id if found.
        guess_opponent(champion, enemy_champions) -> champion | None: best guess before positions are known.
        """
        self.league = league
        self.live = live
        self.ddragon = ddragon
        self.on_game = on_game
        self.on_game_end = on_game_end
        self.guess_opponent = guess_opponent
        self.clock = clock
        self.first_sync_delay_s = FIRST_SYNC_DELAY_S
        self.retry_sync_s = RETRY_SYNC_S
        self.give_up_after_s = GIVE_UP_AFTER_S
        self._lock = threading.Lock()
        self._state = {"client": "offline", "message": "", "phase": None, "stage": "idle",
                       "game": None, "review": None, "error": None}
        self._saved_sig = None
        self._guess_sig = None
        self._stop = threading.Event()
        self._thread = None

    # --- thread ---------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="league-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                error = None
            except Exception as err:  # keep watching whatever happens
                error = f"{type(err).__name__}: {err}"
            with self._lock:
                self._state["error"] = error
                connected = self._state["client"] == "connected"
            self._stop.wait(2 if connected else 5)

    # --- reading state (API threads) -----------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def mark_review_opened(self, match_id: str) -> None:
        with self._lock:
            review = self._state["review"]
            if review and review["match_id"] == match_id:
                review["opened"] = True

    def set_opponent(self, plan_id: int, champion: str | None) -> None:
        """Manual lane opponent from the page; None goes back to automatic."""
        with self._lock:
            game = self._state["game"]
            if not game or game["plan_id"] != plan_id:
                return
            if champion:
                game["opponent"], game["opponent_source"] = champion, "manual"
            else:
                game["opponent"], game["opponent_source"] = None, None
                self._guess_sig = None
                self._pick_live_opponent(game)

    # --- one poll ----------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        reads = self._read_client()
        with self._lock:
            self._apply(now, reads)
            game = copy.deepcopy(self._state["game"])
            review = self._state["review"]
            due = (review["game_id"] if review and review["status"] == "waiting"
                   and now >= review["next_try"] else None)

        # Slow work (database, Riot API) happens outside the lock.
        if game and not game["ended"]:
            self._guess(game)
            self._save(game)
        if due:
            self._try_review(due, now)

    def _read_client(self) -> dict:
        reads = {"client": "connected", "message": "", "phase": None,
                 "champ_select": None, "gameflow": None, "live": None}
        try:
            reads["phase"] = self.league.get("/lol-gameflow/v1/gameflow-phase")
            stage = STAGES.get(reads["phase"], "idle")
            if stage == "champ_select":
                reads["champ_select"] = self.league.get("/lol-champ-select/v1/session")
            if stage in ("champ_select", "in_game"):
                reads["gameflow"] = self.league.get("/lol-gameflow/v1/session")
        except ClientUnavailable as err:
            reads["client"], reads["message"] = "offline", str(err)
        stage = STAGES.get(reads["phase"], "idle")
        if stage == "in_game" or reads["client"] == "offline":
            reads["live"] = self.live.all_game_data()
        return reads

    def _apply(self, now: float, reads: dict) -> None:
        state = self._state
        stage = STAGES.get(reads["phase"], "idle")
        if reads["live"] is not None:
            stage = "in_game"   # the in-game API answers even if the client API doesn't
        prev = state["stage"]
        game = state["game"]

        if stage in ("champ_select", "in_game"):
            if game is None or game["ended"] or (stage == "champ_select" and prev != "champ_select"):
                game = _new_game(now)
                self._saved_sig = self._guess_sig = None
            if reads["champ_select"]:
                self._apply_champ_select(game, reads["champ_select"])
            if reads["gameflow"]:
                self._apply_gameflow(game, reads["gameflow"])
            if reads["live"]:
                self._apply_live(game, reads["live"])
            self._pick_live_opponent(game)
            game["stage"] = stage
        elif game is not None and not game["ended"]:
            if prev == "in_game":
                # Game over (or the client closed): wait for the match to reach the Riot API.
                game["ended"], game["stage"] = True, "ended"
                if game["game_id"]:
                    state["review"] = {"game_id": game["game_id"], "status": "waiting", "match_id": None,
                                       "attempts": 0, "error": None, "opened": False,
                                       "next_try": now + self.first_sync_delay_s,
                                       "deadline": now + self.give_up_after_s}
            elif prev == "champ_select":
                game = None   # dodge, or left champ select

        state.update(client=reads["client"], message=reads["message"], phase=reads["phase"],
                     stage=stage, game=game)

    # --- champ select / game data --------------------------------------------------

    def _champion(self, value) -> tuple[str | None, str | None]:
        try:
            cid = self.ddragon.resolve(value)
            return cid, self.ddragon.champion_index()["names"].get(cid) if cid else None
        except DataDragonError:
            return None, None

    def _summoner(self, value) -> dict | None:
        try:
            return self.ddragon.summoner(value)
        except DataDragonError:
            return None

    def _apply_champ_select(self, game: dict, session: dict) -> None:
        my_cell = session.get("localPlayerCellId")

        def player(p, own_team):
            cid, hovering = p.get("championId") or 0, False
            if not cid and own_team and p.get("championPickIntent"):
                cid, hovering = p["championPickIntent"], True
            champion, name = self._champion(cid)
            spells = [s for s in (self._summoner(p.get("spell1Id")), self._summoner(p.get("spell2Id"))) if s] if own_team else []
            return {"champion": champion, "name": name, "position": _position(p.get("assignedPosition")),
                    "spells": spells, "hovering": hovering, "cell": p.get("cellId")}

        allies = [player(p, True) for p in session.get("myTeam") or []]
        me = next((a for a in allies if a["cell"] == my_cell), None)
        if me:
            game["me"] = me
        game["allies"] = [a for a in allies if a is not me]
        game["enemies"] = [player(p, False) for p in session.get("theirTeam") or []]

    def _apply_gameflow(self, game: dict, flow: dict) -> None:
        data = flow.get("gameData") or {}
        if data.get("gameId"):
            game["game_id"] = str(data["gameId"])
        queue = (data.get("queue") or {}).get("id")
        if queue is not None:
            game["queue_id"] = queue

    def _apply_live(self, game: dict, data: dict) -> None:
        players = data.get("allPlayers") or []
        active = data.get("activePlayer") or {}
        my_name = active.get("riotId") or active.get("summonerName")
        me = next((p for p in players if my_name and my_name in (p.get("riotId"), p.get("summonerName"))), None)
        if me is None:
            return

        def player(p):
            # rawChampionName is "game_character_displayname_<id>"; championName is the display name.
            raw = (p.get("rawChampionName") or "").rsplit("_", 1)[-1]
            champion, name = self._champion(raw)
            if not champion:
                champion, name = self._champion(p.get("championName"))
            spells = []
            for slot in ("summonerSpellOne", "summonerSpellTwo"):
                s = (p.get("summonerSpells") or {}).get(slot) or {}
                found = _SPELL_ID.search(s.get("rawDisplayName") or "")
                info = self._summoner(found.group(1)) if found else None
                spells.append(info or {"id": None, "name": s.get("displayName"), "cooldown": None, "image": None})
            return {"champion": champion or p.get("championName"), "name": name or p.get("championName"),
                    "position": _position(p.get("position")), "spells": spells, "hovering": False}

        old_position = game["me"].get("position")
        game["me"] = player(me)
        game["me"]["position"] = game["me"]["position"] or old_position or ""
        game["allies"] = [player(p) for p in players if p is not me and p.get("team") == me.get("team")]
        game["enemies"] = [player(p) for p in players if p.get("team") != me.get("team")]
        game["ability_haste"] = (active.get("championStats") or {}).get("abilityHaste")
        game["level"] = active.get("level")
        game["game_time"] = (data.get("gameData") or {}).get("gameTime")
        game["live"] = True

    # --- lane opponent ----------------------------------------------------------------

    @staticmethod
    def _pick_live_opponent(game: dict) -> None:
        """Enemy in your lane, once the in-game API gives positions."""
        if game["opponent_source"] == "manual":
            return
        position = game["me"]["position"]
        match = next((e for e in game["enemies"] if position and e["position"] == position and e["champion"]), None)
        if match:
            game["opponent"], game["opponent_source"] = match["champion"], "live"
        elif game["opponent"] not in [e["champion"] for e in game["enemies"]]:
            game["opponent"], game["opponent_source"] = None, None

    def _guess(self, game: dict) -> None:
        """Champ select doesn't say which enemy is your laner: guess from your history."""
        if not self.guess_opponent or game["opponent_source"] in ("live", "manual"):
            return
        enemies = [e["champion"] for e in game["enemies"] if e["champion"]]
        sig = (game["me"]["champion"], tuple(enemies))
        if not enemies or sig == self._guess_sig:
            return
        self._guess_sig = sig
        guess = self.guess_opponent(game["me"]["champion"], enemies)
        with self._lock:
            current = self._state["game"]
            if current and current["key"] == game["key"] and current["opponent_source"] in (None, "history"):
                current["opponent"], current["opponent_source"] = (guess, "history") if guess else (None, None)
                game.update(opponent=current["opponent"], opponent_source=current["opponent_source"])

    def _save(self, game: dict) -> None:
        if not self.on_game or not game["me"]["champion"]:
            return
        sig = (game["key"], game["game_id"], game["queue_id"], game["me"]["champion"], game["me"]["position"],
               game["opponent"], game["opponent_source"],
               tuple(e["champion"] for e in game["enemies"]), tuple(a["champion"] for a in game["allies"]))
        if sig == self._saved_sig:
            return
        plan = self.on_game(game)
        self._saved_sig = sig
        with self._lock:
            current = self._state["game"]
            if not current or current["key"] != game["key"]:
                return
            current["plan_id"] = plan["id"]
            # A lane opponent you picked by hand wins over automatic ones (also after a restart).
            if plan.get("opponent_source") == "manual" and current["opponent_source"] != "manual":
                current["opponent"], current["opponent_source"] = plan["opponent"], "manual"

    # --- automatic review ---------------------------------------------------------------

    def _try_review(self, game_id: str, now: float) -> None:
        error = None
        try:
            match_id = self.on_game_end(game_id) if self.on_game_end else None
        except Exception as err:
            match_id, error = None, str(err)
        with self._lock:
            review = self._state["review"]
            if not review or review["game_id"] != game_id:
                return
            review["attempts"] += 1
            review["error"] = error
            if match_id:
                review["status"], review["match_id"] = "ready", match_id
            elif now >= review["deadline"]:
                review["status"] = "gave_up"
            else:
                review["next_try"] = now + self.retry_sync_s
