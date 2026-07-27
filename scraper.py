"""ScoreChaser - Scraper for ATGames ArcadeNet leaderboards.

The leaderboards site is a Next.js app. Game lists come from the JSON API
under /api/leaderboards; per-game and tournament scores are embedded in the
React Server Component (RSC) flight payload of their pages, requested with
an "RSC: 1" header and parsed as JSON.
"""

import codecs
import json
import re
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.atgames.net/leaderboards"
GAMES_API_URL = "https://www.atgames.net/api/leaderboards/games"
GAME_TOP50_URL = f"{BASE_URL}/device/top50"
SCHEDULE_URL = f"{BASE_URL}/schedule"
TOURNAMENT_SCORES_URL = f"{BASE_URL}/highscore/top50"

ARCADENET_BACKEND = "https://www.atgames.net/api/arcadenet"
PERSONAL_SCORES_URL = f"{ARCADENET_BACKEND}/d2d/arcade/v2/leaderboards/personal"
ARCADENET_LOGIN_URL = "https://www.atgames.net/arcadenet/auth/login"

# When packaged with PyInstaller, store data next to the executable
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).parent
else:
    _APP_DIR = Path(__file__).parent

DATA_DIR = _APP_DIR / "data"
SCORES_FILE = DATA_DIR / "scores.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
SNAPSHOT_FILE = DATA_DIR / "user_snapshot.json"
PERSONAL_SCORES_FILE = DATA_DIR / "personal_scores.json"
TOURNAMENTS_FILE = DATA_DIR / "tournaments.json"

MAX_WORKERS_GAMES = 5
MAX_WORKERS_SCORES = 10

_thread_local = threading.local()


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "ScoreChaser/1.0",
        "Accept": "application/json",
    })
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    return s


SESSION = _new_session()


def _fetch_rsc_payload(url: str, session: requests.Session,
                       params: dict | None = None) -> str:
    """Fetch a page's RSC flight payload (plain text with embedded JSON).

    Falls back to extracting the payload from the full HTML page if the
    server ignores the RSC header.
    """
    resp = session.get(url, params=params, headers={"RSC": "1", "Accept": "*/*"})
    resp.raise_for_status()
    # RSC responses carry no charset header; requests would fall back to latin-1
    resp.encoding = "utf-8"
    text = resp.text

    if "<html" in text[:300].lower():
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', text, re.DOTALL)
        parts = []
        for c in chunks:
            try:
                parts.append(json.loads('"' + c + '"'))
            except ValueError:
                parts.append(codecs.decode(c, "unicode_escape"))
        text = "".join(parts)

    return text


def _extract_json_after(payload: str, key: str):
    """Parse the JSON value following `key` (e.g. '"rankings":') in payload."""
    idx = payload.find(key)
    if idx < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(payload, idx + len(key))
        return value
    except ValueError:
        return None


def _fetch_games_for_prefix(prefix: str, session: requests.Session) -> list[dict]:
    """Fetch all games for a single prefix letter, handling pagination."""
    games = []
    after = ""

    while True:
        params = {
            "after": after,
            "prefix": prefix,
            "limit": "8",
        }

        resp = session.get(GAMES_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("games", []) if isinstance(data, dict) else data

        if not batch:
            break

        for game in batch:
            games.append({
                "game_id": game["game_id"],
                "name": game["name"],
                "internal_number": game["internal_number"],
                "boxart": game.get("boxart_480w") or game.get("boxart", ""),
            })

        if len(batch) < 8:
            break

        after = str(batch[-1]["game_id"])
        time.sleep(0.1)

    return games


def fetch_all_games(progress_callback=None) -> list[dict]:
    """Fetch all game titles in parallel by prefix letter.

    Args:
        progress_callback: Optional callable(completed_prefixes, total_prefixes, total_games_so_far)
    """
    prefixes = list(string.ascii_lowercase)
    all_games: list[dict] = []
    seen_ids: set[int] = set()
    lock = threading.Lock()
    completed = 0

    def _do_prefix(prefix: str) -> list[dict]:
        session = _new_session()
        return _fetch_games_for_prefix(prefix, session)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_GAMES) as pool:
        futures = {pool.submit(_do_prefix, p): p for p in prefixes}

        for future in as_completed(futures):
            games = future.result()
            with lock:
                for g in games:
                    if g["game_id"] not in seen_ids:
                        seen_ids.add(g["game_id"])
                        all_games.append(g)
                completed += 1

                if progress_callback:
                    progress_callback(completed, len(prefixes), len(all_games))

    all_games.sort(key=lambda g: g["name"].lower())
    return all_games


def fetch_scores(game_id: int, session: requests.Session | None = None,
                  time_range: str | None = None) -> list[dict]:
    """Fetch top 50 scores for a game (keyed by game_id).

    time_range: None for all-time, or 'weekly', 'monthly'.
    """
    s = session or SESSION
    url = f"{GAME_TOP50_URL}/{game_id}"
    params = {}
    if time_range:
        params["timeRange"] = time_range
    payload = _fetch_rsc_payload(url, s, params=params)
    rankings = _extract_json_after(payload, '"rankings":')
    if rankings is None:
        raise ValueError(f"No rankings found for game {game_id}")

    return [
        {
            "rank": entry.get("rank"),
            "userName": entry.get("user_name", ""),
            "signature": entry.get("signature") or "",
            "score": entry.get("score", "0"),
            "hardware": entry.get("hardware", ""),
            "createdAt": entry.get("created_at", ""),
        }
        for entry in rankings
    ]


def scrape_all(progress_callback=None) -> dict:
    """Scrape all games and their top 50 scores using a pipeline.

    Games and scores are fetched concurrently: as soon as a prefix batch of
    games is discovered, their scores are submitted for fetching immediately.

    Args:
        progress_callback: Optional callable(scores_done, games_discovered,
            games_done, game_name) — called on every score completion.
            `games_done` is True once all prefixes have been fetched.
    """
    prefixes = list(string.ascii_lowercase)
    all_data: dict[str, dict] = {}
    seen_ids: set[int] = set()
    lock = threading.Lock()
    scores_completed = 0
    games_discovered = 0
    prefixes_done = 0

    score_futures: dict = {}

    def _get_thread_session() -> requests.Session:
        if not hasattr(_thread_local, "session"):
            _thread_local.session = _new_session()
        return _thread_local.session

    def _fetch_scores_task(game: dict) -> tuple[dict, list[dict] | None, str | None]:
        try:
            scores = fetch_scores(game["game_id"], session=_get_thread_session())
            return game, scores, None
        except Exception as e:
            return game, None, str(e)

    def _collect_score(future):
        nonlocal scores_completed
        game, scores, error = future.result()
        with lock:
            scores_completed += 1
            if progress_callback:
                progress_callback(
                    scores_completed, games_discovered,
                    prefixes_done == len(prefixes), game["name"],
                )
            if error:
                return
            all_data[str(game["game_id"])] = {
                "name": game["name"],
                "game_id": game["game_id"],
                "internal_number": game["internal_number"],
                "boxart": game.get("boxart", ""),
                "scores": scores,
            }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_GAMES) as games_pool, \
         ThreadPoolExecutor(max_workers=MAX_WORKERS_SCORES) as scores_pool:

        prefix_futures = {games_pool.submit(
            _fetch_games_for_prefix, p, _new_session()
        ): p for p in prefixes}

        for pf in as_completed(prefix_futures):
            games = pf.result()
            with lock:
                prefixes_done += 1
                new_games = []
                for g in games:
                    if g["game_id"] not in seen_ids:
                        seen_ids.add(g["game_id"])
                        new_games.append(g)
                games_discovered += len(new_games)

            for g in new_games:
                fut = scores_pool.submit(_fetch_scores_task, g)
                fut.add_done_callback(_collect_score)
                score_futures[fut] = g

        # Wait for remaining score fetches
        for sf in as_completed(score_futures):
            pass  # results already collected via callback

    return all_data


_TOURNAMENT_LINK_RE = re.compile(
    r'"href":"/leaderboards/highscore/(?:result|top50)/(\d+)"'
    r'[^{}]*?"children":"((?:[^"\\]|\\.)*)"'
)
_MONTHS = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TOURNAMENT_DATE_RE = re.compile(
    rf'"({_MONTHS} \d{{1,2}}, \d{{4}}) - ({_MONTHS} \d{{1,2}}, \d{{4}})"'
)


def _to_iso_date(text: str) -> str:
    """Convert 'Jul 24, 2026' to '2026-07-24'; pass through on failure."""
    try:
        return datetime.strptime(text, "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return text


def _parse_schedule_tournaments(payload: str, status: str) -> list[dict]:
    """Parse tournament cards from a schedule page's RSC payload."""
    links = []
    seen = set()
    for m in _TOURNAMENT_LINK_RE.finditer(payload):
        tid = int(m.group(1))
        if tid in seen:
            continue
        seen.add(tid)
        try:
            name = json.loads('"' + m.group(2) + '"')
        except ValueError:
            name = m.group(2)
        links.append({"id": tid, "name": name, "pos": m.start()})

    dates = [
        {"start": _to_iso_date(m.group(1)), "end": _to_iso_date(m.group(2)),
         "pos": m.start(), "used": False}
        for m in _TOURNAMENT_DATE_RE.finditer(payload)
    ]

    tournaments = []
    for link in links:
        # Pair each card's title with the nearest unused date range
        best = None
        for d in dates:
            if d["used"]:
                continue
            dist = abs(d["pos"] - link["pos"])
            if best is None or dist < abs(best["pos"] - link["pos"]):
                best = d
        start = end = ""
        if best:
            best["used"] = True
            start, end = best["start"], best["end"]
        tournaments.append({
            "id": link["id"],
            "name": link["name"],
            "status": status,
            "start": start,
            "end": end,
        })

    return tournaments


def fetch_tournaments() -> list[dict]:
    """Fetch tournaments from the schedule page (active, upcoming, expired)."""
    tournaments: list[dict] = []
    seen: set[int] = set()

    for status_param, status in [("upcoming", "Upcoming"), ("active", "Active"),
                                  ("expired", "Expired")]:
        payload = _fetch_rsc_payload(SCHEDULE_URL, SESSION,
                                     params={"status": status_param})
        for t in _parse_schedule_tournaments(payload, status):
            if t["id"] not in seen:
                seen.add(t["id"])
                tournaments.append(t)

    # Keep all upcoming + active, plus the 5 most-recent expired
    upcoming = [t for t in tournaments if t["status"] == "Upcoming"]
    active = [t for t in tournaments if t["status"] == "Active"]
    expired = [t for t in tournaments if t["status"] == "Expired"]
    expired.sort(key=lambda t: (t.get("end") or ""), reverse=True)
    return upcoming + active + expired[:5]


def fetch_tournament_scores(tournament_id: int,
                            session: requests.Session | None = None) -> list[dict]:
    """Fetch and parse top50 scores for a tournament."""
    s = session or SESSION
    url = f"{TOURNAMENT_SCORES_URL}/{tournament_id}"
    payload = _fetch_rsc_payload(url, s)
    raw_games = _extract_json_after(payload, '"games":')
    if not isinstance(raw_games, list):
        return []

    games = []
    for g in raw_games:
        if not isinstance(g, dict) or "rankings" not in g:
            continue
        scores = [
            {
                "rank": entry.get("rank"),
                "userName": entry.get("user_name", ""),
                "signature": entry.get("signature") or "",
                "score": entry.get("score", "0"),
                "hardware": entry.get("hardware", ""),
                "createdAt": entry.get("created_at", ""),
            }
            for entry in (g.get("rankings") or [])
        ]
        games.append({
            "name": g.get("name", "Unknown"),
            "boxart": g.get("boxart_480w") or g.get("boxart", ""),
            "scores": scores,
        })

    return games


def save_data(data: dict) -> None:
    """Save scraped data to disk."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_data() -> dict | None:
    """Load previously scraped data from disk."""
    if not SCORES_FILE.exists():
        return None
    try:
        with open(SCORES_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if data else None
    except (json.JSONDecodeError, ValueError):
        return None


def load_settings() -> dict:
    """Load settings from disk."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    """Save settings to disk."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def load_snapshot() -> dict:
    """Load the user state snapshot from the last session."""
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_snapshot(snapshot: dict) -> None:
    """Save the current user state snapshot for next-session comparison."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_personal_scores() -> list[dict]:
    """Load previously fetched personal scores from disk."""
    if not PERSONAL_SCORES_FILE.exists():
        return []
    try:
        with open(PERSONAL_SCORES_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def save_personal_scores(scores: list[dict]) -> None:
    """Save personal scores so they're available at next startup."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(PERSONAL_SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def load_tournaments_cache() -> dict:
    """Load cached tournaments + score data.
    Returns {"tournaments": [...], "scores": {tid: [game_scores...]}}."""
    empty = {"tournaments": [], "scores": {}}
    if not TOURNAMENTS_FILE.exists():
        return empty
    try:
        with open(TOURNAMENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return empty
        return {
            "tournaments": data.get("tournaments", []) or [],
            "scores": data.get("scores", {}) or {},
        }
    except (json.JSONDecodeError, ValueError):
        return empty


def save_tournaments_cache(tournaments: list[dict],
                            scores_by_tid: dict) -> None:
    """Persist tournaments and their scores for offline use."""
    DATA_DIR.mkdir(exist_ok=True)
    scores_str = {str(k): v for k, v in scores_by_tid.items()}
    with open(TOURNAMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"tournaments": tournaments, "scores": scores_str},
            f, ensure_ascii=False, indent=2,
        )


def login_via_browser() -> tuple[str | None, str | None]:
    """Open a browser window for ATGames login and return the JWT token.

    Returns (token, error) — token string on success, or (None, error_message)
    on failure.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError:
        return None, "selenium is not installed. Run: pip install selenium"

    options = Options()
    options.add_argument("--window-size=500,700")

    # Try multiple ways to get a working Chrome driver
    driver = None
    last_error = ""

    # 1. Try webdriver-manager (most reliable, auto-downloads correct version)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        last_error = str(e)

    # 2. Fallback: let Selenium's built-in manager try
    if driver is None:
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as e:
            last_error = str(e)

    if driver is None:
        return None, f"Could not start Chrome browser:\n{last_error}"

    driver.get(ARCADENET_LOGIN_URL)

    token = None
    try:
        # Poll localStorage for the token (set after successful login)
        while True:
            try:
                _ = driver.window_handles
            except Exception:
                break

            try:
                t = driver.execute_script("return localStorage.getItem('token');")
                if t:
                    token = t
                    break
            except Exception:
                break

            time.sleep(0.5)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if token:
        return token, None
    return None, None  # User closed the window without logging in


def get_token_expiry(token: str) -> float | None:
    """Extract expiry timestamp from JWT token. Returns None if invalid."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.b64decode(payload))
        return decoded.get("exp")
    except Exception:
        return None


def get_token_username(token: str) -> str | None:
    """Extract user_name from JWT token."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.b64decode(payload))
        return decoded.get("user_name")
    except Exception:
        return None


def is_token_valid(token: str | None) -> bool:
    """Check if a JWT token exists and hasn't expired."""
    if not token:
        return False
    exp = get_token_expiry(token)
    if exp is None:
        return False
    return time.time() < exp


def fetch_personal_scores(token: str) -> list[dict]:
    """Fetch all personal high scores using an authenticated token.

    Paginates through all results (API limit is 5 per page).
    Fetches across all hardware models for overall rankings.
    Returns a list of dicts with keys: game_id, internal_number, name, boxart,
    rank, score, signature, hardware, created_at, etc.
    """
    headers = {"Authorization": f"Bearer {token}"}
    all_scores: list[dict] = []
    after = None

    while True:
        params: dict = {"limit": 5}
        if after:
            params["after"] = after

        resp = SESSION.get(PERSONAL_SCORES_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        all_scores.extend(data)

        if len(data) < 5:
            break

        after = data[-1]["game_id"]
        time.sleep(0.1)

    return all_scores


if __name__ == "__main__":
    def _progress(scores_done, games_found, games_done, name):
        total_str = str(games_found) if games_done else f"~{games_found}"
        if scores_done % 25 == 0 or scores_done == games_found:
            print(f"[{scores_done}/{total_str}] {name}")

    data = scrape_all(progress_callback=_progress)
    save_data(data)
    print(f"Fertig! {len(data)} Spiele gespeichert.")
