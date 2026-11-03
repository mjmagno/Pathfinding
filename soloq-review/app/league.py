"""Read-only access to the League of Legends client running on this PC.

Two local APIs, both on 127.0.0.1 only:

- The client API ("LCU"). While the client runs it writes a `lockfile` into its
  install folder holding a port and a password for that launch. It gives the
  game phase (lobby, champ select, in game, ...) and the champ select picks.
- The Live Client Data API (port 2999), Riot's official in-game API. Available
  once a game has loaded. It gives every player's champion, lane position and
  summoner spells, and your own stats (ability haste).

Only GET requests are sent; the tool never changes anything in the client.
Both APIs use a self-signed certificate, so certificate checks are switched off
for these 127.0.0.1 connections only.
"""

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_INSTALLS = [Path(r"C:\Riot Games\League of Legends"), Path(r"D:\Riot Games\League of Legends")]
PRODUCT_SETTINGS = (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Riot Games" / "Metadata"
                    / "league_of_legends.live" / "league_of_legends.live.product_settings.yaml")
LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

_LOCAL_TLS = ssl.create_default_context()
_LOCAL_TLS.check_hostname = False
_LOCAL_TLS.verify_mode = ssl.CERT_NONE


class ClientUnavailable(Exception):
    pass


def find_install(override: str = "", product_settings: Path = PRODUCT_SETTINGS) -> Path | None:
    """League's install folder: LEAGUE_PATH from .env, else what the Riot Client
    recorded at install time, else the usual default folders."""
    candidates = []
    if override:
        candidates.append(Path(override))
    try:
        text = product_settings.read_text("utf-8", errors="replace")
        match = re.search(r'product_install_full_path:\s*"?([^"\r\n]+?)"?\s*$', text, re.MULTILINE)
        if match:
            candidates.append(Path(match.group(1)))
    except OSError:
        pass
    candidates += DEFAULT_INSTALLS
    for path in candidates:
        if (path / "LeagueClient.exe").exists() or (path / "lockfile").exists():
            return path
    return None


def read_lockfile(path: Path) -> tuple[int, str]:
    """lockfile format: name:pid:port:password:protocol"""
    try:
        parts = path.read_text("utf-8").strip().split(":")
    except OSError as err:
        raise ClientUnavailable("League client is not running") from err
    if len(parts) < 5 or not parts[2].isdigit():
        raise ClientUnavailable("League client lockfile could not be read")
    return int(parts[2]), parts[3]


class LeagueClient:
    """The client API. `install_dir` is called on every request (the port and
    password change each time the client starts)."""

    def __init__(self, install_dir):
        self.install_dir = install_dir

    def get(self, path: str):
        install = self.install_dir()
        if install is None:
            raise ClientUnavailable("League install folder not found (set LEAGUE_PATH in .env)")
        port, password = read_lockfile(install / "lockfile")
        token = base64.b64encode(f"riot:{password}".encode()).decode()
        request = urllib.request.Request(
            f"https://127.0.0.1:{port}{path}",
            headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, context=_LOCAL_TLS, timeout=3) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as err:
            if err.code == 404:  # e.g. no champ select session right now
                return None
            raise ClientUnavailable(f"League client API error {err.code} for {path}") from err
        except (urllib.error.URLError, OSError, ValueError) as err:
            raise ClientUnavailable("League client is not responding") from err


class LiveClient:
    """The in-game Live Client Data API. Returns None when no game is loaded."""

    def __init__(self, url: str = LIVE_URL):
        self.url = url

    def all_game_data(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.url, context=_LOCAL_TLS, timeout=2) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None
        # While loading it answers with an error object instead of game data.
        return data if isinstance(data, dict) and "allPlayers" in data else None
