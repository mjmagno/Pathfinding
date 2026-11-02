"""Turn raw Match-V5 match + timeline JSON into the numbers the tool cares about.

Everything here is a pure function of the JSON, so stats can be recomputed later
from the raw data stored in the database.
"""

SOLO_QUEUE_ID = 420
QUEUE_NAMES = {
    420: "Ranked Solo/Duo",
    440: "Ranked Flex",
    400: "Normal Draft",
    430: "Normal Blind",
    490: "Quickplay",
    450: "ARAM",
    1700: "Arena",
}

LANE_PHASE_END_MS = 14 * 60 * 1000


def queue_name(queue_id: int) -> str:
    return QUEUE_NAMES.get(queue_id, f"Queue {queue_id}")


def _frame_at_minute(frames: list[dict], minute: int) -> dict | None:
    """Timeline frames are roughly one per minute; frame N is at ~N:00."""
    target = minute * 60 * 1000
    best = None
    for frame in frames:
        if frame["timestamp"] <= target + 5000:
            best = frame
        else:
            break
    # Only trust it if the game actually reached that minute.
    if best is None or best["timestamp"] < target - 5000:
        return None
    return best


def _cs(pframe: dict) -> int:
    return pframe.get("minionsKilled", 0) + pframe.get("jungleMinionsKilled", 0)


def _role(p: dict) -> str:
    return p.get("teamPosition") or p.get("individualPosition") or ""


def analyze(match: dict, timeline: dict, puuid: str) -> dict:
    """Return {"game": {...}, "events": [...], "participants": [...]} for player `puuid`."""
    info = match["info"]
    participants = info["participants"]
    me = next((p for p in participants if p["puuid"] == puuid), None)
    if me is None:
        raise ValueError("Player is not in this match")

    my_team = me["teamId"]
    my_role = _role(me)
    allies = [p for p in participants if p["teamId"] == my_team]
    enemies = [p for p in participants if p["teamId"] != my_team]

    def by_role(team: list[dict], role: str) -> dict | None:
        if not role:
            return None
        return next((p for p in team if _role(p) == role), None)

    opponent = by_role([p for p in enemies], my_role)
    my_jungler = by_role([p for p in allies if p is not me], "JUNGLE")
    enemy_jungler = by_role(enemies, "JUNGLE")

    me_id = me["participantId"]
    opp_id = opponent["participantId"] if opponent else None
    my_jg_id = my_jungler["participantId"] if my_jungler else None
    enemy_jg_id = enemy_jungler["participantId"] if enemy_jungler else None
    champ_by_id = {p["participantId"]: p["championName"] for p in participants}

    duration_s = info.get("gameDuration", 0)
    # Very old matches reported gameDuration in milliseconds.
    if "gameEndTimestamp" not in info and duration_s > 100000:
        duration_s //= 1000
    start_ms = info.get("gameStartTimestamp") or info.get("gameCreation")
    end_ms = info.get("gameEndTimestamp") or (start_ms + duration_s * 1000)

    frames = timeline.get("info", {}).get("frames", [])

    def lane_numbers(minute: int) -> dict:
        frame = _frame_at_minute(frames, minute)
        if frame is None:
            return {}
        pf = frame["participantFrames"]
        mine = pf.get(str(me_id))
        if not mine:
            return {}
        out = {f"cs{minute}": _cs(mine), f"gold{minute}": mine.get("totalGold")}
        theirs = pf.get(str(opp_id)) if opp_id else None
        if theirs:
            out[f"csd{minute}"] = _cs(mine) - _cs(theirs)
            out[f"gd{minute}"] = mine.get("totalGold", 0) - theirs.get("totalGold", 0)
            out[f"xpd{minute}"] = mine.get("xp", 0) - theirs.get("xp", 0)
        return out

    # --- kills / deaths ---------------------------------------------------
    events = []
    counters = {
        "deaths_pre14": 0,
        "deaths_enemy_jg": 0,
        "deaths_enemy_jg_pre14": 0,
        "solo_deaths_pre14": 0,
        "kills_with_my_jg": 0,
        "kills_with_my_jg_pre14": 0,
    }
    for frame in frames:
        for ev in frame.get("events", []):
            if ev.get("type") != "CHAMPION_KILL":
                continue
            killer = ev.get("killerId", 0)
            victim = ev.get("victimId")
            assists = ev.get("assistingParticipantIds") or []
            involved = {killer, *assists} - {0}
            ts = ev.get("timestamp", frame["timestamp"])
            pre14 = ts < LANE_PHASE_END_MS

            if victim == me_id:
                kind = "death"
            elif killer == me_id:
                kind = "kill"
            elif me_id in assists:
                kind = "assist"
            else:
                continue

            enemy_jg_involved = enemy_jg_id in involved if kind == "death" else False
            my_jg_involved = my_jg_id in involved if kind != "death" else False

            if kind == "death":
                counters["deaths_pre14"] += pre14
                counters["deaths_enemy_jg"] += enemy_jg_involved
                counters["deaths_enemy_jg_pre14"] += enemy_jg_involved and pre14
                if opp_id and involved == {opp_id}:
                    counters["solo_deaths_pre14"] += pre14
            else:
                counters["kills_with_my_jg"] += my_jg_involved
                counters["kills_with_my_jg_pre14"] += my_jg_involved and pre14

            # The review list shows kills and deaths; assists only feed the stats.
            if kind == "assist":
                continue
            pos = ev.get("position") or {}
            events.append({
                "ts_ms": ts,
                "kind": kind,
                "killer": champ_by_id.get(killer) if killer else "Executed",
                "victim": champ_by_id.get(victim),
                "assists": [champ_by_id.get(a) for a in assists if a in champ_by_id],
                "x": pos.get("x"),
                "y": pos.get("y"),
                "enemy_jg_involved": bool(enemy_jg_involved),
                "my_jg_involved": bool(my_jg_involved),
                "solo": bool(kind == "death" and opp_id and involved == {opp_id})
                or bool(kind == "kill" and not assists and victim == opp_id),
            })

    remake = bool(me.get("gameEndedInEarlySurrender")) or duration_s < 300
    queue_id = info.get("queueId", 0)

    game = {
        "match_id": match["metadata"]["matchId"],
        "queue_id": queue_id,
        "is_solo": int(queue_id == SOLO_QUEUE_ID),
        "game_start": start_ms,
        "game_end": end_ms,
        "duration_s": duration_s,
        "patch": ".".join(str(info.get("gameVersion", "")).split(".")[:2]),
        "champion": me["championName"],
        "role": my_role,
        "off_role": int(my_role != "TOP"),
        "remake": int(remake),
        "win": int(bool(me.get("win"))),
        "kills": me.get("kills", 0),
        "deaths": me.get("deaths", 0),
        "assists": me.get("assists", 0),
        "cs": me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0),
        "opponent": opponent["championName"] if opponent else None,
        "my_jungler": my_jungler["championName"] if my_jungler else None,
        "enemy_jungler": enemy_jungler["championName"] if enemy_jungler else None,
        **counters,
    }
    for minute in (10, 14):
        numbers = lane_numbers(minute)
        for key in (f"cs{minute}", f"csd{minute}", f"gd{minute}", f"xpd{minute}"):
            game[key] = numbers.get(key)

    roster = [
        {
            "champion": p["championName"],
            "team": "ally" if p["teamId"] == my_team else "enemy",
            "role": _role(p),
            "is_me": p is me,
        }
        for p in participants
    ]
    return {"game": game, "events": events, "participants": roster}
