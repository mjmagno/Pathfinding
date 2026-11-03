"""Shared test setup: no network, and never the real League client."""

import pytest

from app import main
from app.ddragon import DataDragon
from app.league import ClientUnavailable

VERSION = "16.21.1"
CHAMPIONS = {  # id: (numeric key, display name)
    "Renekton": ("58", "Renekton"), "Darius": ("122", "Darius"), "Garen": ("86", "Garen"),
    "Fiora": ("114", "Fiora"), "Aatrox": ("266", "Aatrox"), "Riven": ("92", "Riven"),
    "Ornn": ("516", "Ornn"), "Malphite": ("54", "Malphite"), "Gwen": ("887", "Gwen"),
    "KSante": ("897", "K'Sante"), "Camille": ("164", "Camille"), "Jax": ("24", "Jax"), "Sion": ("14", "Sion"),
    "LeeSin": ("64", "Lee Sin"), "Viego": ("234", "Viego"), "Graves": ("104", "Graves"),
    "Kindred": ("203", "Kindred"), "Elise": ("60", "Elise"), "Nocturne": ("56", "Nocturne"),
    "Vi": ("254", "Vi"), "Hecarim": ("120", "Hecarim"), "Ahri": ("103", "Ahri"), "Syndra": ("134", "Syndra"),
    "Yone": ("777", "Yone"), "Orianna": ("61", "Orianna"), "Sylas": ("517", "Sylas"), "Viktor": ("112", "Viktor"),
    "Jinx": ("222", "Jinx"), "Kaisa": ("145", "Kai'Sa"), "Ezreal": ("81", "Ezreal"), "Caitlyn": ("51", "Caitlyn"),
    "Jhin": ("202", "Jhin"), "Thresh": ("412", "Thresh"), "Nautilus": ("111", "Nautilus"), "Lulu": ("117", "Lulu"),
    "Leona": ("89", "Leona"), "Rakan": ("497", "Rakan"), "MonkeyKing": ("62", "Wukong"),
}
SUMMONERS = {
    "SummonerFlash": ("4", "Flash", 300, ["CLASSIC", "ARAM"]),
    "SummonerTeleport": ("12", "Teleport", 300, ["CLASSIC"]),
    "SummonerSmite": ("11", "Smite", 15, ["CLASSIC"]),
    "SummonerDot": ("14", "Ignite", 180, ["CLASSIC", "ARAM"]),
    "SummonerHeal": ("7", "Heal", 240, ["CLASSIC", "ARAM"]),
    "SummonerSnowball": ("32", "Mark", 80, ["ARAM"]),
}


def champion_file(cid: str) -> dict:
    key, name = CHAMPIONS[cid]
    spells = [
        {"name": f"{name} Q", "cooldown": [8, 7, 6, 5, 4], "maxrank": 5, "maxammo": "-1",
         "description": "Deals <physicalDamage>damage</physicalDamage>.", "image": {"full": f"{cid}Q.png"}},
        {"name": f"{name} W", "cooldown": [16, 14, 12, 10, 8], "maxrank": 5, "maxammo": "-1",
         "description": "Stuns.", "image": {"full": f"{cid}W.png"}},
        {"name": f"{name} E", "cooldown": [20, 18, 16, 14, 12], "maxrank": 5, "maxammo": "2",
         "description": "Dashes.", "image": {"full": f"{cid}E.png"}},
        {"name": f"{name} R", "cooldown": [120, 100, 80], "maxrank": 3, "maxammo": "-1",
         "description": "Ultimate.", "image": {"full": f"{cid}R.png"}},
    ]
    return {"data": {cid: {"id": cid, "key": key, "name": name, "title": "the test",
                           "spells": spells,
                           "passive": {"name": f"{name} passive", "description": "<b>Passive</b> text",
                                       "image": {"full": f"{cid}_P.png"}}}}}


def fake_fetch(url: str):
    if url.endswith("/api/versions.json"):
        return [VERSION, "16.20.1"]
    if url.endswith("/champion.json"):
        return {"data": {cid: {"id": cid, "key": k, "name": n} for cid, (k, n) in CHAMPIONS.items()}}
    if "/champion/" in url:
        return champion_file(url.rsplit("/", 1)[-1].removesuffix(".json"))
    if url.endswith("/summoner.json"):
        return {"data": {sid: {"id": sid, "key": k, "name": n, "cooldown": [cd], "modes": modes,
                               "image": {"full": f"{sid}.png"}}
                         for sid, (k, n, cd, modes) in SUMMONERS.items()}}
    raise AssertionError(f"unexpected fetch {url}")


class OfflineLeague:
    """No League client running."""

    def get(self, path):
        raise ClientUnavailable("League client is not running")

    def all_game_data(self):
        return None


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ddragon", DataDragon(tmp_path / "ddragon", fetch=fake_fetch))
    monkeypatch.setattr(main, "run_watcher_thread", False)
    real_make = main.make_watcher
    offline = OfflineLeague()
    monkeypatch.setattr(main, "make_watcher", lambda: real_make(league=offline, live=offline))
    monkeypatch.setattr(main, "_real_make_watcher", real_make, raising=False)
