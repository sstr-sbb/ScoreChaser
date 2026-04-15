"""Scraper for ATGames ArcadeNet leaderboards."""

import json
import re
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.atgames.net/leaderboards"
TITLES_AFTER_URL = f"{BASE_URL}/titles/after"
SCORES_JSON_URL = f"{BASE_URL}/scores-json"
TOURNAMENT_LIST_URL = "https://acnet-lb.atgames.net/tournament/list"
TOURNAMENT_SCORES_URL = f"{BASE_URL}/highscore/top50"

ARCADENET_BACKEND = "https://www.atgames.net/arcadenet/backend"
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

MAX_WORKERS_GAMES = 5
MAX_WORKERS_SCORES = 10

_thread_local = threading.local()


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "PinballScores/1.0",
        "Accept": "application/json",
    })
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    return s


SESSION = _new_session()


def _fetch_games_for_prefix(prefix: str, session: requests.Session) -> list[dict]:
    """Fetch all games for a single prefix letter, handling pagination."""
    games = []
    after = ""

    while True:
        params = {
            "after": after,
            "rule": "AND",
            "prefix": prefix,
            "order": "",
            "friends": "",
            "table": "",
            "table_rule": "",
            "keyword": "",
        }

        resp = session.get(TITLES_AFTER_URL, params=params)
        resp.raise_for_status()
        batch = resp.json()

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


def fetch_scores(internal_number: int, session: requests.Session | None = None) -> list[dict]:
    """Fetch top 100 scores for a game."""
    s = session or SESSION
    url = f"{SCORES_JSON_URL}/{internal_number}"
    resp = s.get(url)
    resp.raise_for_status()
    data = resp.json()

    return [
        {
            "rank": entry.get("rank"),
            "userName": entry.get("userName", ""),
            "signature": entry.get("signature", ""),
            "score": entry.get("score", "0"),
            "hardware": entry.get("hardware", ""),
            "createdAt": entry.get("createdAt", ""),
        }
        for entry in data
    ]


def scrape_all(progress_callback=None) -> dict:
    """Scrape all games and their top 100 scores using a pipeline.

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
            scores = fetch_scores(game["internal_number"], session=_get_thread_session())
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


def fetch_tournaments() -> list[dict]:
    """Fetch tournament list from the API."""
    resp = SESSION.get(TOURNAMENT_LIST_URL)
    resp.raise_for_status()
    data = resp.json()
    tournaments = data.get("tournaments", [])
    # Return only active and recent expired (last 5)
    active = [t for t in tournaments if t.get("status") == "Active"]
    expired = [t for t in tournaments if t.get("status") == "Expired"]
    return active + expired[:5]


def _parse_tournament_scores_html(html: str) -> list[dict]:
    """Parse tournament top50 HTML page into structured data per game."""
    games = []
    # Split by game items
    items = re.split(r'<div class="item">', html)

    for item in items[1:]:  # skip content before first item
        # Extract game name from title div
        name_match = re.search(r'<div class="title"><span>\d+</span>(.*?)</div>', item)
        game_name = name_match.group(1).strip() if name_match else "Unknown"

        # Extract boxart
        boxart_match = re.search(r'<img src="(https://assets\.atgames\.net[^"]*)"', item)
        boxart = boxart_match.group(1) if boxart_match else ""

        # Extract score rows from tbody
        scores = []
        rows = re.findall(r'<tr>\s*<th scope="row">(\d+)</th>(.*?)</tr>', item, re.DOTALL)
        for rank_str, row_html in rows:
            # Username
            name_m = re.search(r'<td class="td-02"[^>]*>(.*?)</td>', row_html)
            username = name_m.group(1).strip() if name_m else ""

            # Hardware - check title attribute first, then hidden <b>, then raw text
            hw = ""
            hw_m = re.search(r'title="([^"]*)"', row_html)
            if hw_m:
                hw = hw_m.group(1)
            elif re.search(r'<b hidden>(.*?)</b>', row_html):
                hw = re.search(r'<b hidden>(.*?)</b>', row_html).group(1)
            else:
                hw_raw = re.search(r'<td class="td-03"[^>]*>(.*?)</td>', row_html)
                if hw_raw:
                    hw = re.sub(r'<[^>]+>', '', hw_raw.group(1)).strip()

            # Initials
            ini_m = re.search(r'<td class="td-04"[^>]*>(.*?)</td>', row_html)
            initials = ini_m.group(1).strip() if ini_m else ""

            # Score - last td, remove commas
            score_m = re.search(
                r'<td[^>]*>([0-9][0-9,]*)</td>\s*$', row_html, re.DOTALL
            )
            score_val = score_m.group(1).replace(",", "") if score_m else "0"

            scores.append({
                "rank": int(rank_str),
                "userName": username,
                "signature": initials,
                "score": score_val,
                "hardware": hw,
            })

        games.append({
            "name": game_name,
            "boxart": boxart,
            "scores": scores,
        })

    return games


def fetch_tournament_scores(tournament_id: int,
                            session: requests.Session | None = None) -> list[dict]:
    """Fetch and parse top50 scores for a tournament."""
    s = session or SESSION
    url = f"{TOURNAMENT_SCORES_URL}/{tournament_id}"
    resp = s.get(url)
    resp.raise_for_status()
    return _parse_tournament_scores_html(resp.text)


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


def login_via_browser() -> str | None:
    """Open a browser window for ATGames login and return the JWT token.

    Returns the token string on success, or None if the user closed the window
    without logging in.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError:
        return None

    options = Options()
    options.add_argument("--window-size=500,700")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        # Try without specifying service (uses PATH)
        try:
            driver = webdriver.Chrome(options=options)
        except Exception:
            return None

    driver.get(ARCADENET_LOGIN_URL)

    token = None
    try:
        # Poll localStorage for the token (set after successful login)
        while True:
            try:
                # Check if window was closed
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

    return token


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


def fetch_personal_scores(token: str, model: str = "RK9920") -> list[dict]:
    """Fetch all personal high scores using an authenticated token.

    Paginates through all results (API limit is 5 per page).
    Returns a list of dicts with keys: game_id, internal_number, name, boxart,
    rank, score, signature, hardware, created_at, etc.
    """
    headers = {"Authorization": f"Bearer {token}"}
    all_scores: list[dict] = []
    after = None

    while True:
        params: dict = {"limit": 5, "model": model}
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
