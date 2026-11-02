"""Synthetic Match-V5 data for demo mode and tests.

The JSON has the same shape as the real Riot API responses (only the fields the
tool reads), so the whole pipeline can run without an API key.
"""

import random
import time

DEMO_PUUID = "demo-puuid-0000"

MY_POOL = ["Renekton", "Camille", "Jax", "Sion"]
POOLS = {
    "TOP": ["Darius", "Garen", "Fiora", "Aatrox", "Riven", "Ornn", "Malphite", "Gwen", "KSante"],
    "JUNGLE": ["LeeSin", "Viego", "Graves", "Kindred", "Elise", "Nocturne", "Vi", "Hecarim"],
    "MIDDLE": ["Ahri", "Syndra", "Yone", "Orianna", "Sylas", "Viktor"],
    "BOTTOM": ["Jinx", "Kaisa", "Ezreal", "Caitlyn", "Jhin"],
    "UTILITY": ["Thresh", "Nautilus", "Lulu", "Leona", "Rakan"],
}
ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
TOP_SIDE = {"x": (1200, 5500), "y": (9500, 14000)}


def make_match(match_id: str, end_ms: int, seed: int, queue_id: int = 420,
               my_champion: str | None = None, my_role: str = "TOP",
               duration_s: int | None = None) -> tuple[dict, dict]:
    rng = random.Random(seed)
    duration_s = duration_s or rng.randint(22 * 60, 38 * 60)
    start_ms = end_ms - duration_s * 1000
    my_team = rng.choice([100, 200])
    my_win = rng.random() < 0.52
    minutes = duration_s // 60

    participants = []
    used = set()
    for team in (100, 200):
        for role in ROLES:
            pid = len(participants) + 1
            is_me = team == my_team and role == my_role
            if is_me:
                champ = my_champion or rng.choice(MY_POOL)
            else:
                champ = rng.choice([c for c in POOLS[role] if c not in used])
            used.add(champ)
            participants.append({
                "participantId": pid,
                "puuid": DEMO_PUUID if is_me else f"puuid-{seed}-{pid}",
                "riotIdGameName": "Demo" if is_me else f"Player{pid}",
                "championName": champ,
                "teamId": team,
                "teamPosition": role,
                "individualPosition": role,
                "win": (team == my_team) == my_win,
                "gameEndedInEarlySurrender": duration_s < 300,
                "kills": 0, "deaths": 0, "assists": 0,
                "totalMinionsKilled": 0, "neutralMinionsKilled": 0,
            })

    by = {(p["teamId"], p["teamPosition"]): p["participantId"] for p in participants}
    me = by[(my_team, my_role)]
    enemy_team = 300 - my_team
    my_jg, enemy_jg, opp = by[(my_team, "JUNGLE")], by[(enemy_team, "JUNGLE")], by[(enemy_team, my_role)]

    # --- kills ---
    kill_events = []

    def add_kill(ts, killer, victim, assists, top_side=False):
        area = TOP_SIDE if top_side else {"x": (1000, 14000), "y": (1000, 14000)}
        kill_events.append({
            "type": "CHAMPION_KILL", "timestamp": ts, "killerId": killer, "victimId": victim,
            "assistingParticipantIds": assists,
            "position": {"x": rng.randint(*area["x"]), "y": rng.randint(*area["y"])},
        })

    # Lane phase: a few top-side fights involving me (none in a remake).
    lane_fights = rng.randint(1, 4) if duration_s >= 15 * 60 else 0
    for _ in range(lane_fights):
        ts = rng.randint(3 * 60_000, 14 * 60_000)
        roll = rng.random()
        if roll < 0.3:
            add_kill(ts, opp, me, [], True)                       # solo death
        elif roll < 0.55:
            killer = rng.choice([opp, enemy_jg])
            helper = enemy_jg if killer == opp else opp
            add_kill(ts, killer, me, [helper], True)              # gank
        elif roll < 0.8:
            add_kill(ts, me, opp, [], True)                       # solo kill
        else:
            add_kill(ts, me, opp, [my_jg], True)                  # 2v1 with jungler

    # Rest of the game: random skirmishes, slightly favouring the winning team.
    win_team = my_team if my_win else enemy_team
    skirmishes = rng.randint(15, 35) if duration_s >= 15 * 60 else 0
    for _ in range(skirmishes):
        ts = rng.randint(4 * 60_000, duration_s * 1000 - 5000)
        team = win_team if rng.random() < 0.58 else 300 - win_team
        mates = [by[(team, r)] for r in ROLES]
        foes = [by[(300 - team, r)] for r in ROLES]
        killer = rng.choice(mates)
        victim = rng.choice(foes)
        assists = [m for m in mates if m != killer and rng.random() < 0.35]
        add_kill(ts, killer, victim, assists)
    kill_events.sort(key=lambda e: e["timestamp"])

    for ev in kill_events:
        participants[ev["killerId"] - 1]["kills"] += 1
        participants[ev["victimId"] - 1]["deaths"] += 1
        for a in ev["assistingParticipantIds"]:
            participants[a - 1]["assists"] += 1

    # --- per-minute frames ---
    rates = {}
    for p in participants:
        role = p["teamPosition"]
        base = {"JUNGLE": 0.4, "UTILITY": 0.9}.get(role, 7.2)
        if p["participantId"] == me:
            base = rng.uniform(6.2, 8.4)
        rates[p["participantId"]] = (base, 5.8 if role == "JUNGLE" else 0.1)

    frames, cs_state, gold_state, xp_state = [], {}, {}, {}
    for minute in range(minutes + 1):
        ts = minute * 60_000 + (rng.randint(0, 40) if minute else 0)
        pframes = {}
        for p in participants:
            pid = p["participantId"]
            lane_cs, jg_cs = cs_state.get(pid, (0, 0))
            if minute >= 2:
                lane_cs += max(0, round(rates[pid][0] + rng.uniform(-1.5, 1.5)))
                jg_cs += max(0, round(rates[pid][1] + rng.uniform(-1, 1)))
            cs_state[pid] = (lane_cs, jg_cs)
            gold_state[pid] = 500 + minute * 122 + (lane_cs + jg_cs) * 21 + rng.randint(0, 150)
            xp_state[pid] = minute * 360 + rng.randint(0, 200)
            pframes[str(pid)] = {
                "participantId": pid, "minionsKilled": lane_cs, "jungleMinionsKilled": jg_cs,
                "totalGold": gold_state[pid], "xp": xp_state[pid],
                "level": min(18, 1 + xp_state[pid] // 700),
            }
        frame_events = [e for e in kill_events if minute * 60_000 <= e["timestamp"] < (minute + 1) * 60_000]
        frames.append({"timestamp": ts, "participantFrames": pframes, "events": frame_events})

    for p in participants:
        lane_cs, jg_cs = cs_state[p["participantId"]]
        p["totalMinionsKilled"], p["neutralMinionsKilled"] = lane_cs, jg_cs

    match = {
        "metadata": {"matchId": match_id, "participants": [p["puuid"] for p in participants]},
        "info": {
            "gameCreation": start_ms - 90_000, "gameStartTimestamp": start_ms, "gameEndTimestamp": end_ms,
            "gameDuration": duration_s, "queueId": queue_id, "gameVersion": "16.21.712.4321",
            "participants": participants,
        },
    }
    timeline = {"metadata": {"matchId": match_id}, "info": {"frameInterval": 60000, "frames": frames}}
    return match, timeline


class FakeRiotClient:
    """Stands in for RiotClient. Holds some older games, and 'plays' a new game
    (ending now) when asked for match ids, at most once a minute and 3 times in total."""

    def __init__(self, history: int = 12, new_games: int = 3, new_game_interval_s: int = 60):
        self.matches: dict[str, tuple[dict, dict]] = {}
        self.order: list[str] = []  # newest first
        self.new_games_left = new_games
        self.interval_ms = new_game_interval_s * 1000
        self.last_new_ms = 0
        now = int(time.time() * 1000)
        for i in range(history):
            end = now - (2 + i * 0.6) * 86_400_000
            queue = 400 if i % 5 == 4 else 420
            self._add(f"DEMO_{1000 + i}", int(end), seed=i, queue_id=queue)

    def _add(self, match_id, end_ms, seed, **kwargs):
        self.matches[match_id] = make_match(match_id, end_ms, seed, **kwargs)
        self.order.insert(0, match_id)
        self.order.sort(key=lambda m: -self.matches[m][0]["info"]["gameEndTimestamp"])

    def get_puuid(self, game_name, tag_line):
        return DEMO_PUUID

    def get_match_ids(self, puuid, count=20, start_time=None):
        now = int(time.time() * 1000)
        if self.new_games_left and now - self.last_new_ms >= self.interval_ms:
            self.new_games_left -= 1
            self.last_new_ms = now
            n = len(self.matches)
            self._add(f"DEMO_{2000 + n}", now, seed=100 + n)
        return self.order[:count]

    def get_match(self, match_id):
        return self.matches[match_id][0]

    def get_timeline(self, match_id):
        return self.matches[match_id][1]
