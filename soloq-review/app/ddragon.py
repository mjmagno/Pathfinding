"""Data Dragon: champion abilities (cooldowns), summoner spells and champion ids.

Riot's static data CDN. Each file is downloaded once per patch and cached on
disk under cache/ddragon/<patch>/, so lookups are instant after the first one
and keep working offline for patches already seen.
"""

import json
import re
import threading
import time
import urllib.request
from pathlib import Path

BASE = "https://ddragon.leagueoflegends.com"
USER_AGENT = "SoloQReview/1.0 (personal match review app)"
VERSION_TTL_S = 6 * 3600
ABILITY_KEYS = "QWER"

_TAG = re.compile(r"<[^>]+>")


def plain(text: str | None) -> str:
    """Data Dragon descriptions contain markup like <physicalDamage>; keep the words."""
    return " ".join(_TAG.sub(" ", text or "").split())


def _http_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _version_key(v: str):
    return [int(p) if p.isdigit() else 0 for p in v.split(".")]


class DataDragonError(Exception):
    pass


class DataDragon:
    def __init__(self, cache_dir: Path, fetch=_http_json):
        self.cache_dir = Path(cache_dir)
        self.fetch = fetch
        self._lock = threading.Lock()
        self._version: str | None = None
        self._version_at = 0.0
        self._mem: dict = {}

    # --- raw files -----------------------------------------------------------

    def version(self) -> str:
        with self._lock:
            if self._version and time.time() - self._version_at < VERSION_TTL_S:
                return self._version
        try:
            version = self.fetch(f"{BASE}/api/versions.json")[0]
        except Exception as err:
            # Offline: keep the last known patch, or the newest one cached on disk.
            version = self._version
            if not version and self.cache_dir.exists():
                cached = sorted((p.name for p in self.cache_dir.iterdir() if p.is_dir()), key=_version_key)
                version = cached[-1] if cached else None
            if not version:
                raise DataDragonError(f"Could not reach Data Dragon: {err}") from err
        with self._lock:
            self._version, self._version_at = version, time.time()
        return version

    def _file(self, rel: str):
        version = self.version()
        key = (version, rel)
        if key in self._mem:
            return self._mem[key]
        path = self.cache_dir / version / rel
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
        else:
            try:
                data = self.fetch(f"{BASE}/cdn/{version}/data/en_US/{rel}")
            except Exception as err:
                raise DataDragonError(f"Could not download {rel} from Data Dragon: {err}") from err
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(path)
        self._mem[key] = data
        return data

    # --- champions -----------------------------------------------------------

    def champion_index(self) -> dict:
        """{"by_key": {"58": "Renekton"}, "names": {"Renekton": "Renekton", ...}}

        Ids ("MonkeyKing", "KSante") are what Match-V5 and the image URLs use;
        names ("Wukong", "K'Sante") are what players see.
        """
        key = ("index", self.version())
        if key not in self._mem:
            data = self._file("champion.json")["data"]
            self._mem[key] = {
                "by_key": {c["key"]: c["id"] for c in data.values()},
                "names": {c["id"]: c["name"] for c in data.values()},
            }
        return self._mem[key]

    def resolve(self, value) -> str | None:
        """Champion id from a numeric key, an id or a display name (any case)."""
        if value in (None, "", 0, "0"):
            return None
        index = self.champion_index()
        text = str(value).strip()
        if text in index["by_key"]:
            return index["by_key"][text]
        if text in index["names"]:
            return text
        folded = re.sub(r"[^a-z]", "", text.lower())
        for cid, name in index["names"].items():
            if folded in (re.sub(r"[^a-z]", "", cid.lower()), re.sub(r"[^a-z]", "", name.lower())):
                return cid
        return None

    def champions(self) -> list[dict]:
        names = self.champion_index()["names"]
        return sorted(({"id": cid, "name": name} for cid, name in names.items()), key=lambda c: c["name"])

    def abilities(self, champion_id: str) -> dict:
        data = self._file(f"champion/{champion_id}.json")["data"][champion_id]
        spells = []
        for key, s in zip(ABILITY_KEYS, data["spells"]):
            try:
                ammo = int(s.get("maxammo") or -1)
            except ValueError:
                ammo = -1
            spells.append({
                "key": key,
                "name": s["name"],
                "cooldowns": s.get("cooldown") or [],
                "max_rank": s.get("maxrank", len(s.get("cooldown") or [])),
                "charges": ammo if ammo > 0 else None,
                "description": plain(s.get("description")),
                "image": f"spell/{s['image']['full']}",
            })
        passive = data.get("passive") or {}
        return {
            "id": champion_id,
            "name": data["name"],
            "title": data.get("title", ""),
            "passive": {
                "key": "P",
                "name": passive.get("name", ""),
                "description": plain(passive.get("description")),
                "image": f"passive/{passive['image']['full']}" if passive.get("image") else None,
            },
            "spells": spells,
        }

    # --- summoner spells ---------------------------------------------------

    def summoners(self) -> dict:
        """Summoner's Rift spells, by numeric key ("4") and by id ("SummonerFlash")."""
        key = ("summoners", self.version())
        if key not in self._mem:
            out = {}
            for spell in self._file("summoner.json")["data"].values():
                if "CLASSIC" not in spell.get("modes", []):
                    continue
                info = {
                    "id": spell["id"], "name": spell["name"],
                    "cooldown": (spell.get("cooldown") or [None])[0],
                    "image": f"spell/{spell['image']['full']}",
                }
                out[spell["key"]] = info
                out[spell["id"]] = info
            self._mem[key] = out
        return self._mem[key]

    def summoner(self, value) -> dict | None:
        if value in (None, "", 0, "0"):
            return None
        return self.summoners().get(str(value))
