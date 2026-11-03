"""League client watcher, pre-game plans, cooldowns and the automatic review."""

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.demo import DEMO_LINEUP, FakeLeague, FakeRiotClient
from app.league import ClientUnavailable, find_install, read_lockfile
from app.watcher import Watcher


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOQ_DEMO", "1")
    monkeypatch.setenv("SOLOQ_DB", str(tmp_path / "pregame.db"))
    clock = Clock()
    riot = FakeRiotClient(history=4, new_games=0)
    league = FakeLeague(riot, clock=clock)
    monkeypatch.setattr(main, "riot_client_factory", lambda: riot)
    monkeypatch.setattr(main, "make_watcher",
                        lambda: main._real_make_watcher(league=league, live=league, clock=clock))
    with TestClient(main.app) as client:
        start = clock.t

        def at(seconds):
            clock.t = start + seconds
            main.watcher.tick()
            return client.get("/api/live").json()

        yield client, riot, at


def test_full_pregame_flow(env):
    client, riot, at = env
    # One earlier game against Darius, so champ select can guess the lane opponent.
    riot.add_played_game("DEMO_10", int((1_800_000_000 - 86_400) * 1000), DEMO_LINEUP)
    client.post("/api/sync")
    client.post("/api/sessions", json={"focus_key": "trading"})

    live = at(2)
    assert live["client"] == "connected" and live["stage"] == "idle" and live["game"] is None

    # Champ select: hovering my champion, no enemy picks yet.
    live = at(12)
    g = live["game"]
    assert live["stage"] == "champ_select"
    assert g["me"]["champion"] == "Renekton" and g["me"]["hovering"] is True
    assert g["me"]["position"] == "TOP"
    assert all(e["champion"] is None for e in g["enemies"])
    assert g["opponent"] is None

    # Locked in, three enemy picks: lane opponent guessed from history.
    live = at(30)
    g = live["game"]
    assert g["me"]["hovering"] is False
    assert [s["name"] for s in g["me"]["spells"]] == ["Flash", "Teleport"]
    assert [e["champion"] for e in g["enemies"] if e["champion"]] == ["Syndra", "Darius", "LeeSin"]
    assert (g["opponent"], g["opponent_source"]) == ("Darius", "history")
    assert g["game_id"] == "7000" and g["queue_id"] == 420
    plan = live["plan"]
    assert plan["champion"] == "Renekton" and plan["session_id"] is not None

    r = client.put(f"/api/plans/{plan['id']}", json={"note": "  Trade when his W is down  "})
    assert r.json()["note"] == "Trade when his W is down"

    # Loading screen: in game, but the in-game API isn't up yet.
    live = at(46)
    assert live["stage"] == "in_game" and live["game"]["live"] is False

    # In game: positions confirm the opponent; enemy spells and my haste are known.
    live = at(60)
    g = live["game"]
    assert g["live"] is True and g["key"] == live["game"]["key"]
    assert (g["opponent"], g["opponent_source"]) == ("Darius", "live")
    darius = next(e for e in g["enemies"] if e["champion"] == "Darius")
    assert darius["position"] == "TOP" and [s["cooldown"] for s in darius["spells"]] == [300, 300]
    assert g["ability_haste"] == 0
    assert live["plan"]["id"] == plan["id"] and live["plan"]["note"] == "Trade when his W is down"

    # A hand-picked opponent wins over positions, until set back to automatic.
    assert client.put(f"/api/plans/{plan['id']}", json={"opponent": "Teemo"}).status_code == 400
    client.put(f"/api/plans/{plan['id']}", json={"opponent": "Syndra"})
    live = at(64)
    assert (live["game"]["opponent"], live["game"]["opponent_source"]) == ("Syndra", "manual")
    client.put(f"/api/plans/{plan['id']}", json={"opponent": None})
    live = at(66)
    assert (live["game"]["opponent"], live["game"]["opponent_source"]) == ("Darius", "live")
    assert at(90)["game"]["ability_haste"] == 15

    # Game over: the review is queued, then imported once the match reaches the (fake) Riot API.
    live = at(112)
    assert live["stage"] == "ended" and live["game"]["ended"] is True
    assert live["review"]["status"] == "waiting" and live["review"]["game_id"] == "7000"
    live = at(112 + 61)
    assert live["review"]["status"] == "ready" and live["review"]["match_id"] == "DEMO_7000"
    assert live["review"]["opened"] is False
    client.post("/api/live/review-opened", json={"match_id": "DEMO_7000"})
    assert client.get("/api/live").json()["review"]["opened"] is True

    # The review page shows the plan; the game's lane opponent matches.
    game = client.get("/api/games/DEMO_7000").json()
    assert game["opponent"] == "Darius"
    assert game["plan"]["note"] == "Trade when his W is down"
    assert game["needs_review"] is True

    # Matchup history now includes this game and its plan note.
    m = client.get("/api/matchup", params={"me": "Renekton", "vs": "Darius"}).json()
    assert m["history"]["all"]["games"] == 2
    assert m["history"]["recent"][0]["plan_note"] == "Trade when his W is down"

    # Next cycle starts a fresh game.
    live = at(150 + 12)
    assert live["game"]["game_id"] == "7001" and live["plan"]["id"] != plan["id"]


class Scripted:
    """A League client whose answers the test sets directly."""

    def __init__(self):
        self.phase = "Lobby"
        self.session = None
        self.live = None
        self.offline = False

    def get(self, path):
        if self.offline:
            raise ClientUnavailable("League client is not running")
        return {"/lol-gameflow/v1/gameflow-phase": self.phase,
                "/lol-champ-select/v1/session": self.session}.get(path)

    def all_game_data(self):
        return self.live


def test_dodge_and_numeric_ids():
    client = Scripted()
    w = Watcher(client, client, main.ddragon)
    client.phase = "ChampSelect"
    client.session = {"localPlayerCellId": 2, "myTeam": [
        {"cellId": 2, "championId": 58, "assignedPosition": "top", "spell1Id": 4, "spell2Id": 14},
        {"cellId": 3, "championId": 0, "championPickIntent": 62, "assignedPosition": "jungle"}],
        "theirTeam": [{"cellId": 7, "championId": 145}]}
    w.tick()
    g = w.snapshot()["game"]
    assert g["me"]["champion"] == "Renekton"
    assert [s["name"] for s in g["me"]["spells"]] == ["Flash", "Ignite"]
    assert g["allies"][0]["champion"] == "MonkeyKing" and g["allies"][0]["name"] == "Wukong"
    assert g["enemies"][0]["name"] == "Kai'Sa"

    client.phase = "Lobby"   # someone dodged
    w.tick()
    snap = w.snapshot()
    assert snap["stage"] == "idle" and snap["game"] is None and snap["review"] is None


def test_in_game_without_client_api():
    """If only the in-game API answers (client API unreachable), it still counts as in game."""
    client = Scripted()
    client.offline = True
    client.live = {"activePlayer": {"riotId": "Me#1", "championStats": {"abilityHaste": 20}},
                   "allPlayers": [
                       {"riotId": "Me#1", "championName": "Wukong", "rawChampionName": "game_character_displayname_MonkeyKing",
                        "team": "ORDER", "position": "TOP", "summonerSpells": {}},
                       {"riotId": "Them#2", "championName": "K'Sante", "rawChampionName": "",
                        "team": "CHAOS", "position": "TOP", "summonerSpells": {
                            "summonerSpellOne": {"displayName": "Flash",
                                                 "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName"}}}],
                   "gameData": {"gameTime": 95.0}}
    w = Watcher(client, client, main.ddragon)
    w.tick()
    snap = w.snapshot()
    assert snap["client"] == "offline" and snap["stage"] == "in_game"
    g = snap["game"]
    assert g["me"]["champion"] == "MonkeyKing" and g["opponent"] == "KSante" and g["opponent_source"] == "live"
    assert g["enemies"][0]["spells"][0]["cooldown"] == 300 and g["ability_haste"] == 20

    client.live = None   # game closed with no client API: game over, but no gameId so no review
    w.tick()
    snap = w.snapshot()
    assert snap["game"]["ended"] is True and snap["review"] is None


def test_matchup_endpoint(env):
    client, _, _ = env
    m = client.get("/api/matchup", params={"me": "renekton", "vs": "Wukong"}).json()
    assert m["version"] == "16.21.1"
    assert m["me"]["id"] == "Renekton" and m["vs"]["id"] == "MonkeyKing"
    q, w, e, r = m["me"]["spells"]
    assert w["key"] == "W" and w["cooldowns"] == [16, 14, 12, 10, 8]
    assert e["charges"] == 2 and q["charges"] is None and r["max_rank"] == 3
    assert q["description"] == "Deals damage ."
    assert m["me"]["passive"]["description"] == "Passive text"
    assert m["history"]["all"]["games"] == 0
    assert client.get("/api/matchup", params={"me": "Teemoo"}).status_code == 404
    champs = client.get("/api/champions").json()["champions"]
    assert {"id": "MonkeyKing", "name": "Wukong"} in champs


def test_watch_client_setting(monkeypatch):
    from app import config
    monkeypatch.delenv("SOLOQ_DEMO", raising=False)
    monkeypatch.setenv("WATCH_CLIENT", "0")
    assert config.load_settings().watch_client is False
    monkeypatch.setenv("WATCH_CLIENT", "1")
    assert config.load_settings().watch_client is True


def test_find_install_and_lockfile(tmp_path):
    league = tmp_path / "Games" / "League of Legends"
    league.mkdir(parents=True)
    (league / "LeagueClient.exe").write_text("")
    settings = tmp_path / "product_settings.yaml"
    settings.write_text(f'product_install_full_path: "{league.as_posix()}"\nother: 1\n')
    assert find_install("", product_settings=settings) == league
    # A wrong LEAGUE_PATH falls back to what the Riot Client recorded.
    assert find_install(str(tmp_path / "nope"), product_settings=settings) == league
    # LEAGUE_PATH wins when it is a real install.
    other = tmp_path / "Other"
    other.mkdir()
    (other / "LeagueClient.exe").write_text("")
    assert find_install(str(other), product_settings=settings) == other

    (league / "lockfile").write_text("LeagueClient:1234:50123:s3cret:https")
    assert read_lockfile(league / "lockfile") == (50123, "s3cret")
    (league / "lockfile").write_text("garbage")
    with pytest.raises(ClientUnavailable):
        read_lockfile(league / "lockfile")
    with pytest.raises(ClientUnavailable):
        read_lockfile(league / "missing")


def test_guess_opponent(tmp_path):
    conn = db.connect(tmp_path / "g.db")
    assert db.guess_opponent(conn, "Renekton", []) is None
    assert db.guess_opponent(conn, "Renekton", ["Darius"]) is None
    assert db.game_id_of("NA1_5672582141") == "5672582141"


def test_ddragon_summoners_and_resolve():
    dd = main.ddragon
    assert dd.summoner("4")["name"] == "Flash"
    assert dd.summoner("SummonerTeleport")["cooldown"] == 300
    assert dd.summoner("32") is None          # ARAM-only spell
    assert dd.summoner(0) is None
    assert dd.resolve("Kai'Sa") == "Kaisa"
    assert dd.resolve("62") == "MonkeyKing"
    assert dd.resolve("lee sin") == "LeeSin"
    assert dd.resolve("Nobody") is None
