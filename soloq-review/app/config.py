"""Settings loaded from the .env file next to the app.

The file is re-read every time settings are requested, so you can paste a new
(24-hour) development key into .env without restarting the app.
"""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

VALID_REGIONS = {"americas", "europe", "asia", "sea"}


@dataclass
class Settings:
    api_key: str
    riot_id: str
    region: str
    port: int
    session_idle_hours: float
    db_path: Path

    @property
    def game_name(self) -> str:
        return self.riot_id.split("#", 1)[0].strip()

    @property
    def tag_line(self) -> str:
        parts = self.riot_id.split("#", 1)
        return parts[1].strip() if len(parts) == 2 else ""

    def problems(self) -> list[str]:
        """Human-readable list of setup problems (empty when ready)."""
        issues = []
        if not self.api_key or self.api_key.startswith("RGAPI-xxxx"):
            issues.append("RIOT_API_KEY is not set in .env")
        if not self.game_name or not self.tag_line or self.riot_id == "YourName#NA1":
            issues.append("RIOT_ID is not set in .env (format: GameName#TAG)")
        if self.region not in VALID_REGIONS:
            issues.append(f"REGION must be one of: {', '.join(sorted(VALID_REGIONS))}")
        return issues


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_demo() -> bool:
    return os.environ.get("SOLOQ_DEMO") == "1"


def load_settings() -> Settings:
    file_values = _read_env_file(ENV_PATH)

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_values.get(key) or default

    if is_demo():
        # Demo mode: fake Riot data, separate database, no key needed.
        return Settings(
            api_key="demo", riot_id="Demo#TOP", region="americas",
            port=int(get("PORT", "8000")), session_idle_hours=float(get("SESSION_IDLE_HOURS", "3")),
            db_path=Path(os.environ.get("SOLOQ_DB") or ROOT / "demo.db"),
        )

    return Settings(
        api_key=get("RIOT_API_KEY"),
        riot_id=get("RIOT_ID"),
        region=get("REGION", "americas").lower(),
        port=int(get("PORT", "8000")),
        session_idle_hours=float(get("SESSION_IDLE_HOURS", "3")),
        db_path=Path(get("SOLOQ_DB", str(ROOT / "soloq.db"))),
    )
