import time

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.analysis import analyze
from app.demo import DEMO_PUUID, FakeRiotClient, make_match


def _kill(ts, killer, victim, assists=()):
    return {"type": "CHAMPION_KILL", "timestamp": ts, "killerId": killer, "victimId": victim,
            "assistingParticipantIds": list(assists), "position": {"x": 2000, "y": 12000}}


def _controlled_match():
    """A demo match with hand-placed kills so the expected counts are known."""
    end = int(time.time() * 1000)
    match, timeline = make_match("TEST_1", end, seed=7, duration_s=30 * 60)
    parts = match["info"]["participants"]
    me = next(p for p in parts if p["puuid"] == DEMO_PUUID)
    team = me["teamId"]
    pid = {(p["teamId"], p["teamPosition"]): p["participantId"] for p in parts}
    my_jg, enemy_jg, opp = pid[(team, "JUNGLE")], pid[(300 - team, "JUNGLE")], pid[(300 - team, "TOP")]
    me_id = me["participantId"]
    events = [
        _kill(4 * 60_000, opp, me_id),                      # solo death, lane phase
        _kill(7 * 60_000, enemy_jg, me_id, [opp]),           # gank, lane phase
        _kill(9 * 60_000, me_id, opp, [my_jg]),              # kill with jungler, lane phase
        _kill(11 * 60_000, my_jg, opp, [me_id]),             # assist with jungler (stats only)
        _kill(20 * 60_000, me_id, enemy_jg),                 # late kill, no jungler
        _kill(25 * 60_000, enemy_jg, me_id),                 # late death to jungler
    ]
    for f in timeline["info"]["frames"]:
        f["events"] = [e for e in events if f["timestamp"] // 60_000 == e["timestamp"] // 60_000]
    return match, timeline, me


def test_analyze_counts():
    match, timeline, me = _controlled_match()
    result = analyze(match, timeline, DEMO_PUUID)
    g = result["game"]
    assert g["champion"] == me["championName"]
    assert g["role"] == "TOP" and g["off_role"] == 0
    assert g["deaths_pre14"] == 2
    assert g["solo_deaths_pre14"] == 1
    assert g["deaths_enemy_jg"] == 2
    assert g["deaths_enemy_jg_pre14"] == 1
    assert g["kills_with_my_jg"] == 2          # the 9:00 kill and the 11:00 assist
    assert g["kills_with_my_jg_pre14"] == 2
    # Assists only feed stats; the review list has kills and deaths.
    assert [e["kind"] for e in result["events"]] == ["death", "death", "kill", "kill", "death"]
    assert result["events"][0]["solo"] is True
    assert result["events"][1]["enemy_jg_involved"] is True
    assert result["events"][2]["my_jg_involved"] is True
    # Lane numbers are computed against the enemy top laner.
    frame10 = timeline["info"]["frames"][10]["participantFrames"]
    mine, theirs = frame10[str(me["participantId"])], frame10[str(
        next(p for p in match["info"]["participants"] if p["teamId"] != me["teamId"] and p["teamPosition"] == "TOP")["participantId"])]
    cs = lambda f: f["minionsKilled"] + f["jungleMinionsKilled"]  # noqa: E731
    assert g["cs10"] == cs(mine)
    assert g["csd10"] == cs(mine) - cs(theirs)
    assert g["gd10"] == mine["totalGold"] - theirs["totalGold"]


def test_queue_remake_and_off_role_flags():
    end = int(time.time() * 1000)
    g = analyze(*make_match("T2", end, seed=1, queue_id=440), DEMO_PUUID)["game"]
    assert g["is_solo"] == 0
    g = analyze(*make_match("T3", end, seed=2, duration_s=200), DEMO_PUUID)["game"]
    assert g["remake"] == 1
    assert g["cs14"] is None  # game never reached 14:00
    g = analyze(*make_match("T4", end, seed=3, my_role="MIDDLE"), DEMO_PUUID)["game"]
    assert g["off_role"] == 1 and g["role"] == "MIDDLE"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOQ_DEMO", "1")
    monkeypatch.setenv("SOLOQ_DB", str(tmp_path / "test.db"))
    fake = FakeRiotClient(history=5, new_games=3, new_game_interval_s=0)
    monkeypatch.setattr(main, "riot_client_factory", lambda: fake)
    with TestClient(main.app) as c:
        yield c


def test_session_flow(client):
    status = client.get("/api/status").json()
    assert status["active_session"] is None
    assert status["setup_problems"] == []

    # Old games imported before any session are history, not "needs review".
    r = client.post("/api/sessions", json={"focus_key": "cs", "target_games": 3})
    assert r.status_code == 200
    sync = client.post("/api/sync").json()
    assert len(sync["imported"]) == 6  # 5 history + 1 new game

    status = client.get("/api/status").json()
    active = status["active_session"]
    assert active["session"]["focus_label"] == "CSing"
    assert len(active["games"]) == 1
    new_game = active["games"][0]
    assert new_game["needs_review"] is True
    assert [g["match_id"] for g in status["needs_review"]] == [new_game["match_id"]]

    # Review it, with a focus rating.
    mid = new_game["match_id"]
    r = client.put(f"/api/games/{mid}/review", json={
        "lane_result": "won", "lane_reason": "mistakes", "waves_plan": "partly",
        "focus_rating": 4, "note": "good trades"})
    assert r.status_code == 200
    assert r.json()["review"]["focus_rating"] == 4
    assert client.put(f"/api/games/{mid}/review", json={"lane_result": "bogus"}).status_code == 422

    # Event notes.
    game = client.get(f"/api/games/{mid}").json()
    if game["events"]:
        eid = game["events"][0]["id"]
        assert client.put(f"/api/events/{eid}/note", json={"note": "respect E"}).status_code == 200
        assert client.get(f"/api/games/{mid}").json()["events"][0]["note"] == "respect E"

    # Wrap-up ends the session and records the answers.
    summary = client.post(f"/api/sessions/{active['session']['id']}/wrap",
                          json={"note": "solid", "rating": 4, "keep_focus": True}).json()
    assert summary["avg_focus_rating"] == 4
    status = client.get("/api/status").json()
    assert status["active_session"] is None
    assert status["unwrapped_session"] is None
    assert status["last_session"]["keep_focus"] == 1


def test_opponent_override(client):
    client.post("/api/sessions", json={"focus_key": "custom", "focus_label": "Track enemy JG"})
    client.post("/api/sync")
    game = client.get("/api/games").json()[0]
    mid = game["match_id"]
    enemies = [p["champion"] for p in game["participants"] if p["team"] == "enemy"]
    other = next(c for c in enemies if c != game["opponent"])

    g = client.put(f"/api/games/{mid}/opponent", json={"champion": other}).json()
    assert g["opponent"] == other and g["opponent_manual"] == 1
    assert client.put(f"/api/games/{mid}/opponent", json={"champion": "Teemo"}).status_code == 400
    g = client.put(f"/api/games/{mid}/opponent", json={"champion": None}).json()
    assert g["opponent"] == g["opponent_riot"] and g["opponent_manual"] == 0


def test_custom_focus_requires_text(client):
    assert client.post("/api/sessions", json={"focus_key": "custom", "focus_label": " "}).status_code == 400
    assert client.post("/api/sessions", json={"focus_key": "nope"}).status_code == 400


def test_stale_session_auto_ends(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    s = db.start_session(conn, "cs", "CSing", 3)
    conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (db.now_ms() - 4 * 3600_000, s["id"]))
    db.close_stale_sessions(conn, idle_hours=3)
    assert db.active_session(conn) is None
    ended = db.get_session(conn, s["id"])
    assert ended["auto_ended"] == 1 and ended["wrapped_at"] is None
