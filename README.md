# ScoreChaser
for Atgames Pinball

A desktop app for tracking your standings on [ATGames ArcadeNet](https://www.atgames.net/leaderboards/titles) pinball leaderboards. Fetches the top 100 scores for every game, shows your personal rankings (even beyond top 100), surfaces the targets you can chase, and tells you what has changed since your last session.

## Features

- **ArcadeNet login** via browser for full access to your personal data
- **Personal rankings beyond top 100** — scores and ranks for every game you've played
- **Overall + device ranks** — primary rank is across all devices; the bracketed one in parentheses is your rank on your own hardware, e.g. `#97 (#23 on Pinball 4K)`
- **Next-Target guidance** — shows what it takes to enter the Top 100 / 50 / 10 or become #1, with exact point gap
- **Tournaments view** with per-game leaderboards, boxart, and device-aware targets
- **Score-update popup** — after each refresh, highlights where you improved (score + places), where you were overtaken, and where you entered or fell out of the leaderboard — always paired with a motivational quote
- **Hide games** — right-click a card to hide, manage via the status bar

## Download

Pre-built standalone executables for Windows, Linux, and macOS are available on the [Releases](https://github.com/sstr-sbb/PinballScores/releases/latest) page. No Python installation required.

**Requirements:** Google Chrome must be installed for the ArcadeNet login feature.

**Note:** The macOS build is untested. If you run into issues, please try the source installation below.

## Installation (from source)

Requires Python 3.10+ and a working tkinter installation (included with most Python distributions).

```bash
git clone https://github.com/sstr-sbb/PinballScores.git
cd PinballScores
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
source .venv/bin/activate
python app.py
```

On first launch the app fetches all leaderboard data. Subsequent launches load cached data instantly and refresh in the background.

Click **LOGIN** to sign in with your ArcadeNet account. Your personal rankings across all games will be loaded automatically. The session token is stored locally and valid for 7 days.

Use the **MY GAMES / ALL GAMES / TOURNAMENTS** toggle to switch views. Click any card to see a detailed breakdown in the right panel: next targets, this-week / this-month leaders, and the full top-100 leaderboard with your own score pinned if you're outside the visible range.

## How it works

The scraper uses the ATGames ArcadeNet API:

1. Game titles are fetched in parallel by prefix letter (A–Z)
2. Top 100 scores per game are fetched concurrently (pipelined with step 1)
3. Personal rankings are fetched via authenticated API (paginated across all games)
4. Tournament metadata and per-tournament scores are fetched on demand
5. Everything is persisted as JSON under `data/`:
   - `scores.json` — top 100 per game
   - `personal_scores.json` — user's full ranking list
   - `tournaments.json` — tournament definitions and scores
   - `user_snapshot.json` — last-session snapshot for change detection
   - `settings.json` — hidden games, auth token

Login is handled via Selenium — a Chrome browser window opens for you to sign in on the ATGames website. The JWT token is extracted from `localStorage` after login.

The UI is built with CustomTkinter. The main game list uses a plain `tk.Canvas` with drawn items instead of widget cards, which keeps scrolling and hover interactions fluid even with hundreds of games.

## AI Disclosure

This project was built with the help of [Claude Code](https://claude.ai/claude-code), an AI coding assistant by Anthropic.

## License

This project is not affiliated with ATGames. All leaderboard data is publicly available on atgames.net.

Bundled fonts: [DSEG](https://github.com/keshikan/DSEG) (OFL), [Share Tech Mono](https://fonts.google.com/specimen/Share+Tech+Mono) (OFL).
