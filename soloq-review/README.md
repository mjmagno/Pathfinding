# SoloQ Review

A local web page for reviewing your top lane solo queue games. Keep it open on a
second monitor:

1. **Start a session** with one focus (CSing, jungle tracking, ...) and the number of games you plan to play.
2. **Play.** The page checks for new games every 90 seconds and opens the review when one arrives.
3. **Review** each game: lane stats against your opponent, every kill and death with a
   timestamp to find in the replay, a few quick questions, and how much attention you gave your focus.
4. **Wrap up** the session: record, average focus attention, what you learned, and whether to keep the focus next time.

All games are stored, but only **Ranked Solo/Duo** games (not remakes) count toward stats.
Other queues show up greyed out. Everything is saved locally in `soloq.db` (SQLite).

## Setup (Windows)

### 1. Install Python

Install Python 3.10 or newer from <https://www.python.org/downloads/>.
In the installer, tick **"Add python.exe to PATH"**.

### 2. Get the code and install dependencies

Open **PowerShell** in the `soloq-review` folder (in File Explorer, open the folder,
then type `powershell` in the address bar and press Enter), and run:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. Add your Riot API key

1. Sign in at <https://developer.riotgames.com> with your Riot account.
2. Copy the **Development API Key** from the dashboard.
   It **expires every 24 hours**: regenerate it and paste the new one in when it does.
   For something that lasts, use **Register Product → Personal API Key** and describe this
   tool (a personal match review app). Approval takes a while; use the development key until then.
3. Copy `.env.example` to a new file named `.env` in the same folder, and fill it in:

   ```
   RIOT_API_KEY=RGAPI-your-key-here
   RIOT_ID=YourName#NA1
   REGION=americas
   ```

   `RIOT_ID` is your name and tag exactly as shown in the client.
   `REGION` is `americas` (NA, BR, LAN, LAS), `europe` (EUW, EUNE, TR, RU, ME),
   `asia` (KR, JP), or `sea` (OCE, SG/PH/TH/TW/VN).

`.env` is excluded from git, so your key won't be committed. The file is re-read on every
request, so after replacing an expired key you only need to reload the page.

### 4. Run it

Double-click **`start.bat`**, or from PowerShell:

```powershell
.venv\Scripts\python run.py
```

Your browser opens <http://127.0.0.1:8000>. Leave the console window open while you play;
close it (or press Ctrl+C) to stop. The page only listens on your own machine.

The first check imports your last 20 games as history. Only games played after
that show up as "needs review".

### Try it without an API key

```powershell
.venv\Scripts\python run.py --demo
```

Demo mode uses made-up matches and a separate `demo.db`. A new fake game "finishes"
each time it checks (up to 3), so you can try the whole session flow.

## Notes on the stats

- **Lane opponent** is the enemy player Riot's data puts in the same position as you.
  You can change it on the review page (e.g. for lane swaps).
- **CS / gold / XP at 10 and 14** come from the match timeline, which has one snapshot per minute.
- **Deaths with the enemy jungler involved** count deaths where the enemy jungler got the kill or an assist.
  **Kills with my jungler** count kills where you and your jungler both got the kill or an assist.
- **Off-role** games (where Riot's data says you weren't top) are flagged.
- A session ends on its own after 3 hours without a game (`SESSION_IDLE_HOURS` in `.env`);
  you can still wrap it up afterwards.
- Match data usually reaches the Riot API 1–3 minutes after a game ends.

## Development

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Layout:

| Path | What it does |
|---|---|
| `app/riot.py` | Riot API client (Account-V1, Match-V5), with rate-limit retries |
| `app/analysis.py` | Turns match + timeline JSON into lane stats and the kill/death list |
| `app/db.py` | SQLite schema (numbered migrations), sessions, games, reviews |
| `app/sync.py` | Imports new matches |
| `app/main.py` | FastAPI JSON API and the page |
| `app/demo.py` | Fake match data for demo mode and tests |
| `static/` | The web page (plain HTML/CSS/JS, no build step) |

## Roadmap

- [x] Sessions with a focus, post-game review, kill/death timestamps, session wrap-up
- [ ] Dashboard: win rate and lane stats by champion and matchup, jungler interactions, focus attention over time
- [ ] League client watcher: pre-game plan screen when champ select ends, automatic review when the game ends
- [ ] Cooldown comparison for your matchup on the pre-game screen
- [ ] Open the replay and skip to a kill/death with one click
