"""Minimal Riot API client (Account-V1 and Match-V5).

Uses only the standard library. Handles rate limiting (HTTP 429) by waiting
for the Retry-After period, and turns auth failures into a clear message.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class RiotApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# Account-V1 has no "sea" cluster; accounts are global, so any cluster works.
ACCOUNT_REGION = {"americas": "americas", "europe": "europe", "asia": "asia", "sea": "asia"}


class RiotClient:
    def __init__(self, api_key: str, region: str, max_retries: int = 3):
        self.api_key = api_key
        self.region = region
        self.max_retries = max_retries

    def _get(self, host_region: str, path: str, params: dict | None = None):
        url = f"https://{host_region}.api.riotgames.com{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"X-Riot-Token": self.api_key})

        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt < self.max_retries:
                    wait = float(err.headers.get("Retry-After") or 5)
                    time.sleep(min(wait, 120))
                    continue
                if err.code in (401, 403):
                    raise RiotApiError(
                        "Riot API rejected the key (expired or invalid). Development keys "
                        "expire every 24 hours: regenerate it at developer.riotgames.com "
                        "and paste it into .env (no restart needed).",
                        err.code,
                    ) from err
                if err.code == 404:
                    raise RiotApiError(f"Not found: {path}", 404) from err
                if err.code >= 500 and attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RiotApiError(f"Riot API error {err.code} for {path}", err.code) from err
            except urllib.error.URLError as err:
                if attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RiotApiError(f"Could not reach the Riot API: {err.reason}") from err
        raise RiotApiError("Riot API request failed after retries")

    def get_puuid(self, game_name: str, tag_line: str) -> str:
        account = self._get(
            ACCOUNT_REGION[self.region],
            "/riot/account/v1/accounts/by-riot-id/"
            f"{urllib.parse.quote(game_name)}/{urllib.parse.quote(tag_line)}",
        )
        return account["puuid"]

    def get_match_ids(self, puuid: str, count: int = 20, start_time: int | None = None) -> list[str]:
        params: dict = {"start": 0, "count": count}
        if start_time:
            params["startTime"] = start_time  # epoch seconds
        return self._get(self.region, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params)

    def get_match(self, match_id: str) -> dict:
        return self._get(self.region, f"/lol/match/v5/matches/{match_id}")

    def get_timeline(self, match_id: str) -> dict:
        return self._get(self.region, f"/lol/match/v5/matches/{match_id}/timeline")
