"""ScoreChaser - ATGames Leaderboard Viewer."""

import ctypes
import io
import random
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import customtkinter as ctk
import requests
from PIL import Image, ImageTk

from scraper import (
    load_data, scrape_all, save_data, _APP_DIR,
    fetch_tournaments, fetch_tournament_scores,
    load_settings, save_settings, login_via_browser,
    is_token_valid, get_token_username,
    fetch_personal_scores, fetch_scores,
    load_snapshot, save_snapshot,
    load_personal_scores, save_personal_scores,
    load_tournaments_cache, save_tournaments_cache,
)

# -- Asset paths (PyInstaller extracts data to sys._MEIPASS) --
if getattr(sys, "frozen", False):
    _ASSET_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _ASSET_DIR = Path(__file__).parent
_FONT_DIR = _ASSET_DIR / "fonts"

VERSION = "0.6.1"

# -- Color Scheme (Amber main + vivid colorful accents) --
BG_DARK = "#080600"
BG_PANEL = "#100e06"
BG_CARD = "#161208"
BG_CARD_HOVER = "#1e1a0c"
BG_CARD_SELECTED = "#2a2210"
BG_HEADER = "#1e1a08"
FG_DEFAULT = "#dda840"
FG_DIM = "#806020"
AMBER = "#ffb800"
AMBER_BRIGHT = "#ffdd50"
AMBER_DIM = "#aa7000"
NEON_PINK = "#ff4060"
NEON_CYAN = "#00e8ff"
NEON_GREEN = "#00ff60"
NEON_YELLOW = "#ffee00"
NEON_ORANGE = "#ff6a00"
GOLD = "#ffd000"
HIGHLIGHT_BG = "#2a1800"

# -- Fonts --
# Platform-appropriate monospace fallbacks in case the bundled TTFs
# can't be loaded (Tk falls back silently otherwise, giving odd defaults).
if sys.platform == "win32":
    FONT_FAMILY = "Consolas"
    TITLE_FONT_FAMILY = "Consolas"
elif sys.platform == "darwin":
    FONT_FAMILY = "Menlo"
    TITLE_FONT_FAMILY = "Menlo"
else:
    FONT_FAMILY = "Ubuntu Sans Mono"
    TITLE_FONT_FAMILY = "Ubuntu Sans Mono"


# -- DPI / runtime UI scaling --
# Set by _apply_dpi_scaling() once Tk is initialized. CustomTkinter scales
# its own widgets via per-window DPI detection; the helpers below cover the
# raw tk.Canvas/Text/Menu pieces CTk doesn't touch.
UI_SCALE = 1.0    # multiplier for raw-tk pixel dimensions
FONT_SCALE = 1.0  # multiplier for raw-tk font point sizes (often 1.0 — see below)


def _enable_dpi_awareness():
    """Tell Windows we'll handle DPI ourselves so it stops bitmap-scaling
    the app on hi-DPI displays. Must run before any window is created."""
    if sys.platform != "win32":
        return
    for setter in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),  # Per-Monitor v2
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),  # System DPI
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            setter()
            return
        except (OSError, AttributeError):
            continue


def _apply_dpi_scaling(root):
    """Detect screen DPI and set UI_SCALE / FONT_SCALE for raw-tk widgets.

    We deliberately do NOT call ctk.set_widget_scaling(): CustomTkinter
    auto-detects per-window DPI via GetDpiForMonitor() and scales its widgets
    on its own.

    For raw tk widgets (Canvas/Text/Menu) we have to handle scaling ourselves
    — but only for *pixel* values. Modern Tk on a DPI-aware Windows process
    auto-adjusts its internal pt-to-px factor ('tk scaling'), so positive-pt
    font sizes already render at the right physical size; multiplying again
    would double-scale. We compare Tk's reported DPI against the physical
    monitor DPI to decide whether to also scale font sizes."""
    global UI_SCALE, FONT_SCALE

    try:
        tk_dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        tk_dpi = 96.0

    physical_dpi = tk_dpi
    if sys.platform == "win32":
        try:
            hwnd = root.winfo_id()
            d = float(ctypes.windll.user32.GetDpiForWindow(hwnd))
            if d > 0:
                physical_dpi = d
        except (OSError, AttributeError):
            pass

    UI_SCALE = max(physical_dpi / 96.0, 1.0)

    # If Tk's pt-to-px conversion already reflects physical DPI, fonts will
    # auto-scale via positive-pt sizes — don't multiply ourselves.
    if tk_dpi >= physical_dpi * 0.95:
        FONT_SCALE = 1.0
    else:
        FONT_SCALE = UI_SCALE


def _sf(value: float) -> int:
    """Scale a raw-tk pixel dimension by UI_SCALE."""
    return max(int(round(value * UI_SCALE)), 1)


def _sfont(size: int) -> int:
    """Scale a font point size for raw-tk widgets (Canvas/Text/Menu) using
    FONT_SCALE — usually 1.0 because Tk auto-handles points on hi-DPI."""
    return max(int(round(size * FONT_SCALE)), 1)

# -- Motivational quotes shown after refresh popup --
MOTIVATING_QUOTES = [
    "Every flip brings you closer to glory.",
    "One more ball, one more chance!",
    "The leaderboard waits for no one.",
    "Nudge harder. Play smarter.",
    "Greatness is just one multiball away.",
    "Don't tilt — triumph!",
    "Legends are forged at the flippers.",
    "Progress is the highest score.",
    "The silver ball favors the bold.",
    "Your name belongs at the top.",
    "Every game is a chance to rise.",
    "Skill pays the bills — on the leaderboard.",
    "Ramps were made to be combo'd.",
    "Keep your eye on the ball.",
    "Champions chase — and conquer.",
    "Drain today, dominate tomorrow.",
    "The next jackpot has your name on it.",
    "Play like the ball is on fire.",
    "A true player never stops chasing.",
    "There's always one more point to earn.",
]

# -- Hardware mapping --
HARDWARE_NAMES = {
    "HA8800": "Ultimate", "HA8801": "Ultimate", "HA8802": "Ultimate",
    "HA8810": "Ultimate", "HA8811": "Ultimate",
    "HA2812": "Gamer", "HA2810": "Gamer", "HA2802": "Gamer",
    "AR3060": "Flashback", "AR3060S": "Flashback", "FB8660": "Flashback",
    "FB8660S": "Flashback", "FB8650": "Flashback", "AR3050": "Flashback",
    "AR3650": "Flashback", "AR3080": "Flashback", "AR3080B": "Flashback",
    "HA8819": "Pinball", "HA8819C": "Pinball", "HA8818": "Pinball",
    "HA8820": "Pinball",
    "HAB801": "Connect", "HAB800": "Connect",
    "HA2811": "Core", "HA2819": "Core",
    "HA9920": "Pinball 4K", "RK9920": "Pinball 4K",
    "HA9920D": "Ultimate 4K", "RK9900": "Ultimate 4K",
}

_HW_GROUPS: dict[str, set[str]] = {}
for _code, _name in HARDWARE_NAMES.items():
    _HW_GROUPS.setdefault(_name, set()).add(_code)


def _hw_name(code: str) -> str:
    return HARDWARE_NAMES.get(code, code)


def _format_score(score_str: str) -> str:
    try:
        return f"{int(float(score_str)):,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(score_str) if score_str else ""


def _compact_score(score_str: str) -> str:
    """Format score compactly: 107.8M, 12.4K, 890"""
    try:
        val = int(float(score_str))
    except (ValueError, TypeError):
        return str(score_str) if score_str else ""
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 10_000:
        return f"{val / 1_000:.1f}K"
    return f"{val:,}".replace(",", ".")


def _format_rank_display(overall: int | None, device: int | None,
                          device_name: str) -> str:
    """Format rank: '#97 (#23 on Pinball 4K)', '#54' if equal, or '> 100 (...)'."""
    if overall is not None and device is not None:
        if overall == device or not device_name:
            return f"#{overall}"
        return f"#{overall} (#{device} on {device_name})"
    if overall is not None:
        return f"#{overall}"
    if device is not None and device_name:
        return f"> 100 (#{device} on {device_name})"
    return "> 100"


def _get_thresholds(scores: list[dict]) -> dict:
    by_rank = {s["rank"]: s["score"] for s in scores if s.get("rank")}
    return {
        "top100": by_rank.get(100, ""),
        "top50": by_rank.get(50, ""),
        "top10": by_rank.get(10, ""),
        "high": by_rank.get(1, ""),
    }


def _install_font(font_path: Path) -> bool:
    """Install a font file into the current process. Returns True on success."""
    if not font_path.exists():
        return False
    path_str = str(font_path)
    if sys.platform == "win32":
        try:
            import ctypes
            # FR_PRIVATE = 0x10 — process-local, no registry writes needed
            count = ctypes.windll.gdi32.AddFontResourceExW(path_str, 0x10, 0)
            return count > 0
        except Exception:
            return False
    else:
        try:
            user_fonts = Path.home() / ".local" / "share" / "fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            dest = user_fonts / font_path.name
            if not dest.exists():
                import shutil
                shutil.copy2(path_str, dest)
            return True
        except Exception:
            return False


def _install_all_fonts() -> dict:
    """Register the bundled TTFs with the OS. Must run BEFORE Tk root exists.
    Returns {"dseg": bool, "share_tech": bool} indicating install success."""
    dseg_ok = _install_font(_FONT_DIR / "DSEG14Classic-Regular.ttf")
    dseg_ok = _install_font(_FONT_DIR / "DSEG14Classic-Bold.ttf") or dseg_ok
    stm_ok = _install_font(_FONT_DIR / "ShareTechMono-Regular.ttf")

    # On Windows, broadcast WM_FONTCHANGE so Tk (when it initializes) picks
    # up the freshly registered fonts.
    if sys.platform == "win32":
        try:
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_FONTCHANGE = 0x001D
            ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0)
        except Exception:
            pass

    return {"dseg": dseg_ok, "share_tech": stm_ok}


def _detect_installed_fonts(root):
    """Verify the bundled fonts are visible to Tk and update the globals.
    Must be called AFTER the Tk root has been created."""
    global TITLE_FONT_FAMILY, FONT_FAMILY
    try:
        families = {f.lower(): f for f in tkfont.families(root=root)}
        if "dseg14 classic" in families:
            TITLE_FONT_FAMILY = families["dseg14 classic"]
        if "share tech mono" in families:
            FONT_FAMILY = families["share tech mono"]
    except Exception:
        pass


# ─── Canvas Game List (high-performance) ──────────────────────────

class CanvasGameList:
    """Renders the game list as lightweight canvas items instead of widgets."""

    def __init__(self, parent, on_select, on_right_click, http_session, thumb_pool):
        # Pixel dimensions scale with UI_SCALE — raw tk.Canvas isn't
        # scaled by CustomTkinter's set_widget_scaling.
        self.THUMB_MAX_W = _sf(68)
        self.THUMB_MAX_H = _sf(68)
        self.CARD_H = _sf(86)
        self.CARD_PAD_Y = _sf(4)
        self.CARD_PAD_X = _sf(6)
        self.CARD_TOTAL = self.CARD_H + self.CARD_PAD_Y * 2
        self.TEXT_X_NO_IMG = _sf(12)   # text x-offset when no image
        self.TEXT_X_WITH_IMG = _sf(86)  # text x-offset when image present (8 + 68 + 10)

        # Reusable font handle for measuring titles when truncating with "…"
        # so long names don't wrap onto row 2 / row 3.
        self._title_font_obj = tkfont.Font(
            family=FONT_FAMILY, size=_sfont(13), weight="bold")
        self._truncate_cache: dict[tuple[str, int], str] = {}

        self._on_select = on_select
        self._on_right_click = on_right_click
        self._http = http_session
        self._thumb_pool = thumb_pool
        self._items: list[dict] = []
        self._selected_id: str | None = None
        self._hover_idx: int = -1
        self._card_rects: dict[int, int] = {}  # idx -> canvas rect id

        # Image caches — keep references to prevent GC
        self._photo_cache: dict[str, ImageTk.PhotoImage] = {}  # url -> PhotoImage
        self._img_canvas_ids: dict[int, int] = {}  # idx -> canvas image id
        self._loading_urls: set[str] = set()  # urls currently being fetched

        self._frame = tk.Frame(parent, bg=BG_PANEL, bd=0, highlightthickness=0)

        self._canvas = tk.Canvas(
            self._frame, bg=BG_PANEL, highlightthickness=0, bd=0,
        )
        self._scrollbar = ctk.CTkScrollbar(
            self._frame, command=self._canvas.yview,
            button_color=AMBER_DIM, button_hover_color=AMBER,
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Button-3>", self._on_rclick)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Configure>", lambda e: self._redraw())

        self._last_width: int = 0

    def pack(self, **kwargs):
        self._frame.pack(**kwargs)

    def pack_forget(self):
        self._frame.pack_forget()

    def set_items(self, items: list[dict]):
        """Set items and redraw. Each item dict has keys:
        gid, name, rank_str, score_str, target, gap_str, progress, accent,
        boxart_url (optional)"""
        self._items = items
        self._hover_idx = -1
        self._redraw()
        self._load_visible_thumbs()

    def set_selected(self, game_id: str | None):
        old = self._selected_id
        self._selected_id = game_id
        old_idx = self._idx_for_gid(old) if old else -1
        new_idx = self._idx_for_gid(game_id) if game_id else -1
        if old_idx >= 0:
            self._update_card_bg(old_idx)
        if new_idx >= 0:
            self._update_card_bg(new_idx)

    def _idx_for_gid(self, gid: str) -> int:
        for i, item in enumerate(self._items):
            if item["gid"] == gid:
                return i
        return -1

    def _text_x(self, item: dict, x0: int) -> int:
        """Return the x offset for text, depending on whether boxart url exists."""
        if item.get("boxart_url", ""):
            return x0 + self.TEXT_X_WITH_IMG
        return x0 + self.TEXT_X_NO_IMG

    def _truncate_title(self, text: str, max_width: int) -> str:
        """Trim text with an ellipsis until it fits in max_width pixels at
        the title font. Cached because _redraw runs on every resize/scroll."""
        if not text:
            return ""
        key = (text, max_width)
        cached = self._truncate_cache.get(key)
        if cached is not None:
            return cached
        font = self._title_font_obj
        if font.measure(text) <= max_width:
            self._truncate_cache[key] = text
            return text
        ellipsis = "…"
        n = len(text) - 1
        while n > 0 and font.measure(text[:n].rstrip() + ellipsis) > max_width:
            n -= 1
        result = (text[:n].rstrip() + ellipsis) if n > 0 else ellipsis
        self._truncate_cache[key] = result
        return result

    def _redraw(self):
        w = self._canvas.winfo_width()
        if w < 10:
            w = 350
        self._last_width = w
        self._canvas.delete("all")
        self._card_rects.clear()
        self._img_canvas_ids.clear()

        for i, item in enumerate(self._items):
            y = i * self.CARD_TOTAL + self.CARD_PAD_Y
            x0 = self.CARD_PAD_X
            x1 = w - self.CARD_PAD_X
            is_sel = item["gid"] == self._selected_id
            is_hover = i == self._hover_idx
            accent = item.get("accent", AMBER)

            bg = BG_CARD_SELECTED if is_sel else (BG_CARD_HOVER if is_hover else BG_CARD)
            border = accent if is_sel else (BG_HEADER if is_hover else BG_CARD)

            rect = self._canvas.create_rectangle(
                x0, y, x1, y + self.CARD_H,
                fill=bg, outline=border, width=2,
            )
            self._card_rects[i] = rect

            # Boxart thumbnail (if cached)
            url = item.get("boxart_url", "")
            if url and url in self._photo_cache:
                img_id = self._canvas.create_image(
                    x0 + _sf(8), y + _sf(8), anchor="nw",
                    image=self._photo_cache[url],
                )
                self._img_canvas_ids[i] = img_id

            tx = self._text_x(item, x0)
            kind = item.get("kind", "game")

            row1_y = y + _sf(10)
            row2_y = y + _sf(36)
            row3_y = y + _sf(62)
            right_pad = _sf(10)

            # Title (row 1) — truncate with "…" so it never wraps onto row 2.
            title_max = max(x1 - tx - _sf(12), _sf(40))
            title_text = self._truncate_title(item["name"], title_max)
            self._canvas.create_text(
                tx, row1_y, text=title_text, anchor="nw",
                fill=accent, font=(FONT_FAMILY, _sfont(13), "bold"),
            )

            if kind == "tournament":
                # Row 2: Status (colored) + Dates
                status_text = item.get("status", "")
                status_color = item.get("status_color", NEON_CYAN)
                self._canvas.create_text(
                    tx, row2_y, text=status_text, anchor="nw",
                    fill=status_color, font=(FONT_FAMILY, _sfont(12), "bold"),
                )
                if item.get("dates"):
                    self._canvas.create_text(
                        tx + _sf(84), row2_y, text=item["dates"], anchor="nw",
                        fill=FG_DEFAULT, font=(FONT_FAMILY, _sfont(12)),
                    )
                # User's best rank (right-aligned)
                if item.get("user_rank_str"):
                    self._canvas.create_text(
                        x1 - right_pad, row2_y, text=item["user_rank_str"], anchor="ne",
                        fill=NEON_YELLOW, font=(FONT_FAMILY, _sfont(12), "bold"),
                    )
                # Row 3: subtitle (e.g. "5 games")
                if item.get("subtitle"):
                    self._canvas.create_text(
                        tx, row3_y, text=item["subtitle"], anchor="nw",
                        fill=AMBER_DIM, font=(FONT_FAMILY, _sfont(12)),
                    )
            else:
                # Row 2: Rank (full format) + Gap (right-aligned)
                self._canvas.create_text(
                    tx, row2_y, text=item['rank_str'], anchor="nw",
                    fill=NEON_CYAN, font=(FONT_FAMILY, _sfont(12), "bold"),
                )
                if item["gap_str"]:
                    self._canvas.create_text(
                        x1 - right_pad, row2_y, text=item["gap_str"], anchor="ne",
                        fill=NEON_ORANGE, font=(FONT_FAMILY, _sfont(12), "bold"),
                    )

                # Row 3: Score (left) + Next Target (right, dim)
                if item["score_str"]:
                    self._canvas.create_text(
                        tx, row3_y, text=item["score_str"], anchor="nw",
                        fill=NEON_YELLOW, font=(FONT_FAMILY, _sfont(12)),
                    )
                if item["target"]:
                    target_text = f"→ {item['target']}"
                    if item.get("target_score"):
                        target_text += f"  [{item['target_score']}]"
                    self._canvas.create_text(
                        x1 - right_pad, row3_y, text=target_text, anchor="ne",
                        fill=AMBER_DIM, font=(FONT_FAMILY, _sfont(12)),
                    )

        total_h = max(len(self._items) * self.CARD_TOTAL + self.CARD_PAD_Y, 1)
        self._canvas.configure(scrollregion=(0, 0, w, total_h))

    # ── Thumbnail loading ──────────────────────────────────────

    def _load_visible_thumbs(self):
        """Kick off async loads for all items that have a boxart_url."""
        for item in self._items:
            url = item.get("boxart_url", "")
            if not url or url in self._photo_cache or url in self._loading_urls:
                continue
            self._loading_urls.add(url)
            self._thumb_pool.submit(self._fetch_thumb, url)

    def _fetch_thumb(self, url: str):
        """Download and resize a thumbnail (runs in thread pool)."""
        try:
            resp = self._http.get(url, timeout=8)
            resp.raise_for_status()
            pil = Image.open(io.BytesIO(resp.content))
            # Fit into bounding box, preserving aspect ratio
            max_w, max_h = self.THUMB_MAX_W, self.THUMB_MAX_H
            scale = min(max_w / pil.width, max_h / pil.height)
            w = int(pil.width * scale)
            h = int(pil.height * scale)
            pil = pil.resize((w, h), Image.LANCZOS)
            self._canvas.after(0, lambda: self._on_thumb_loaded(url, pil))
        except Exception:
            self._loading_urls.discard(url)

    def _on_thumb_loaded(self, url: str, pil_img):
        """Called on main thread when a thumbnail is ready."""
        self._loading_urls.discard(url)
        try:
            photo = ImageTk.PhotoImage(pil_img)
            self._photo_cache[url] = photo
        except Exception:
            return

        # Schedule a single coalesced redraw instead of one per thumbnail
        if not hasattr(self, "_redraw_pending") or self._redraw_pending is None:
            self._redraw_pending = self._canvas.after(100, self._coalesced_redraw)

    def _coalesced_redraw(self):
        """Batch redraw after thumbnails have loaded."""
        self._redraw_pending = None
        self._redraw()

    # ── Card background updates ────────────────────────────────

    def _update_card_bg(self, idx: int):
        """Update only the background rect of one card (no full redraw)."""
        rect_id = self._card_rects.get(idx)
        if rect_id is None:
            return
        item = self._items[idx]
        accent = item.get("accent", AMBER)
        is_sel = item["gid"] == self._selected_id
        is_hover = idx == self._hover_idx

        bg = BG_CARD_SELECTED if is_sel else (BG_CARD_HOVER if is_hover else BG_CARD)
        border = accent if is_sel else (BG_HEADER if is_hover else BG_CARD)
        self._canvas.itemconfigure(rect_id, fill=bg, outline=border)

    def _idx_at_y(self, y: int) -> int:
        canvas_y = self._canvas.canvasy(y)
        idx = int(canvas_y // self.CARD_TOTAL)
        if 0 <= idx < len(self._items):
            return idx
        return -1

    def _on_click(self, event):
        idx = self._idx_at_y(event.y)
        if idx >= 0:
            self.set_selected(self._items[idx]["gid"])
            self._on_select(self._items[idx]["gid"])

    def _on_rclick(self, event):
        idx = self._idx_at_y(event.y)
        if idx >= 0:
            self._on_right_click(event, self._items[idx]["gid"])

    def _on_motion(self, event):
        idx = self._idx_at_y(event.y)
        if idx != self._hover_idx:
            old = self._hover_idx
            self._hover_idx = idx
            if old >= 0:
                self._update_card_bg(old)
            if idx >= 0:
                self._update_card_bg(idx)

    def _on_leave(self, event):
        if self._hover_idx >= 0:
            old = self._hover_idx
            self._hover_idx = -1
            self._update_card_bg(old)

    def _on_mousewheel(self, event):
        self._canvas.yview_scroll(-1 * (event.delta // 120), "units")


# ─── Main Application ──────────────────────────────────────────────


class ScoreChaserApp:
    def __init__(self):
        # Opt into Windows DPI awareness BEFORE creating Tk so that
        # winfo_fpixels() reports physical DPI and the OS doesn't bitmap-scale.
        _enable_dpi_awareness()

        # Register TTF files with the OS before Tk initializes — Tk reads
        # the Windows font table once, so fonts added afterwards are often
        # invisible to it.
        _install_all_fonts()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        # Now that Tk exists, resolve the actual family names it can see
        # and update the FONT_FAMILY / TITLE_FONT_FAMILY globals before
        # any widget is built.
        _detect_installed_fonts(self.root)
        _apply_dpi_scaling(self.root)

        self.root.title("ScoreChaser - ATGames Leaderboards")
        self.root.geometry("1400x800")
        self.root.minsize(1000, 600)
        self.root.configure(fg_color=BG_DARK)

        # App icon
        icon_path = _ASSET_DIR / "icon.png"
        if icon_path.exists():
            try:
                icon_img = ImageTk.PhotoImage(Image.open(icon_path))
                self.root.iconphoto(True, icon_img)
                self._app_icon = icon_img
            except Exception:
                pass

        self.data: dict = {}
        self._image_cache: dict[tuple, ctk.CTkImage] = {}
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "ScoreChaser/1.0"})

        self._token: str | None = None
        self._personal_scores: list[dict] = load_personal_scores()
        self._hidden_games: set[str] = set()
        self._load_hidden_games()

        self._selected_game_id: str | None = None
        self._current_view = "my"  # "my", "all", "players", or "tournaments"
        self._selected_player: str | None = None
        self._ranked_players: list[tuple[str, int]] = []

        # Tournament state (hydrate from disk cache)
        _tcache = load_tournaments_cache()
        self._tournaments: list[dict] = _tcache["tournaments"]
        self._tournament_scores_cache: dict[int, list[dict]] = {
            int(k): v for k, v in _tcache["scores"].items() if str(k).isdigit()
        }
        self._selected_tournament_id: int | None = None
        self._tournaments_loaded: bool = bool(self._tournaments)

        # Performance: debounce refresh, pool for async work
        self._refresh_pending: str | None = None
        self._thumb_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="thumb")

        # Snapshot-based change tracking (last session → current)
        self._prev_snapshot: dict = load_snapshot()
        self._pending_compare: bool = False

        self._build_ui()
        self._load_token()
        self._load_existing_data()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self):
        self.root.mainloop()

    # ── UI Building ─────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = ctk.CTkFrame(self.root, fg_color=BG_PANEL, height=56, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        title_box = ctk.CTkFrame(top, fg_color="transparent")
        title_box.pack(side="left", padx=(16, 24))
        ctk.CTkLabel(
            title_box, text="SCORE CHASER",
            font=(TITLE_FONT_FAMILY, 20), text_color=AMBER,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text=f"for ATGames Pinball (v{VERSION})",
            font=(FONT_FAMILY, 10), text_color=FG_DIM,
            anchor="w",
        ).pack(anchor="w", pady=(0, 0))

        # Login area (right side)
        self._login_btn = ctk.CTkButton(
            top, text="LOGIN", width=80, font=(FONT_FAMILY, 13, "bold"),
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=NEON_ORANGE, command=self._start_login,
        )
        self._login_btn.pack(side="right", padx=(8, 16))

        self._login_label = ctk.CTkLabel(
            top, text="", font=(FONT_FAMILY, 12), text_color=FG_DIM,
        )
        self._login_label.pack(side="right")

        # Main content (two panels)
        main = ctk.CTkFrame(self.root, fg_color=BG_DARK, corner_radius=0)
        main.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        main.grid_columnconfigure(0, weight=2, minsize=380)
        main.grid_columnconfigure(1, weight=3, minsize=400)
        main.grid_rowconfigure(0, weight=1)

        # Left panel
        left = ctk.CTkFrame(main, fg_color=BG_PANEL, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        # Left header: view toggle + sort
        left_header = ctk.CTkFrame(left, fg_color="transparent")
        left_header.pack(fill="x", padx=8, pady=(8, 4))

        self._view_toggle = ctk.CTkSegmentedButton(
            left_header,
            values=["MY GAMES", "ALL GAMES", "TOURNAMENTS", "TOP PLAYERS"],
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=BG_DARK, selected_color=AMBER_DIM,
            selected_hover_color=AMBER, unselected_color=BG_CARD,
            unselected_hover_color=BG_CARD_HOVER,
            text_color=FG_DEFAULT, text_color_disabled=FG_DIM,
            command=self._on_view_toggle,
        )
        self._view_toggle.set("MY GAMES")
        self._view_toggle.pack(side="left")

        self._sort_var = tk.StringVar(value="Rank")
        self._sort_combo = ctk.CTkComboBox(
            left_header, variable=self._sort_var,
            values=["Rank", "Name", "Score"],
            width=110, font=(FONT_FAMILY, 12), state="readonly",
            fg_color=BG_CARD, border_color=BG_HEADER,
            button_color=AMBER_DIM, button_hover_color=AMBER,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            command=lambda _: self._refresh_list(),
        )
        self._sort_combo.pack(side="right")
        self._sort_label = ctk.CTkLabel(left_header, text="Sort:",
                                         font=(FONT_FAMILY, 13),
                                         text_color=FG_DIM)
        self._sort_label.pack(side="right", padx=(0, 4))

        # Game count label
        self._count_label = ctk.CTkLabel(
            left, text="", font=(FONT_FAMILY, 13), text_color=FG_DIM,
        )
        self._count_label.pack(fill="x", padx=12, pady=(0, 4))

        # Scrollable game list (canvas-based for performance)
        self._game_list = CanvasGameList(
            left,
            on_select=self._select_game,
            on_right_click=self._show_card_menu,
            http_session=self._http,
            thumb_pool=self._thumb_pool,
        )
        self._game_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Top Players view (hidden unless active)
        self._players_frame = ctk.CTkFrame(left, fg_color="transparent")

        players_top = ctk.CTkFrame(self._players_frame, fg_color="transparent")
        players_top.pack(fill="x", padx=8, pady=(0, 6))
        self._players_desc = ctk.CTkLabel(
            players_top,
            text=("Each Top 100 entry gives (101 − rank) points "
                  "(#1 = 100, #100 = 1), summed across all games."),
            font=(FONT_FAMILY, 12), text_color=FG_DIM,
            wraplength=260, justify="left", anchor="w",
        )
        self._players_desc.pack(side="left", fill="x", expand=True)
        self._show_me_btn = ctk.CTkButton(
            players_top, text="▸ SHOW ME", width=90, height=26,
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=NEON_ORANGE, command=self._scroll_to_me,
        )
        self._show_me_btn.pack(side="right", padx=(8, 0))

        players_wrap = tk.Frame(self._players_frame, bg=BG_PANEL,
                                 bd=0, highlightthickness=0)
        players_wrap.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._players_text = tk.Text(
            players_wrap, bg=BG_DARK, fg=FG_DEFAULT,
            font=(FONT_FAMILY, _sfont(14)), bd=0, highlightthickness=0,
            padx=_sf(12), pady=_sf(6), wrap="none", cursor="arrow",
            spacing1=_sf(3), spacing3=_sf(3),
            tabs=f"{_sf(56)} {_sf(300)} right",
            tabstyle="tabular",
        )
        # Keep the right tab aligned to the current text widget width
        self._players_text.bind("<Configure>", self._on_players_resize)
        players_sb = ctk.CTkScrollbar(
            players_wrap, command=self._players_text.yview,
            button_color=AMBER_DIM, button_hover_color=AMBER,
        )
        self._players_text.configure(yscrollcommand=players_sb.set)
        players_sb.pack(side="right", fill="y")
        self._players_text.pack(side="left", fill="both", expand=True)

        # Card-style row background (like right-side detail rows);
        # zebra stripes for clearer row separation.
        self._players_text.tag_configure(
            "row_a", background=BG_CARD,
            lmargin1=_sf(12), lmargin2=_sf(12), rmargin=_sf(12),
            spacing1=_sf(8), spacing3=_sf(8))
        self._players_text.tag_configure(
            "row_b", background=BG_CARD_HOVER,
            lmargin1=_sf(12), lmargin2=_sf(12), rmargin=_sf(12),
            spacing1=_sf(8), spacing3=_sf(8))
        self._players_text.tag_configure(
            "top1", foreground=GOLD, font=(FONT_FAMILY, _sfont(18), "bold"))
        self._players_text.tag_configure(
            "top2", foreground=AMBER_BRIGHT, font=(FONT_FAMILY, _sfont(17), "bold"))
        self._players_text.tag_configure(
            "top3", foreground=NEON_ORANGE, font=(FONT_FAMILY, _sfont(16), "bold"))
        self._players_text.tag_configure(
            "top10", foreground=AMBER, font=(FONT_FAMILY, _sfont(14), "bold"))
        self._players_text.tag_configure(
            "me", foreground=AMBER_BRIGHT, background=BG_CARD_SELECTED,
            font=(FONT_FAMILY, _sfont(14), "bold"))
        self._players_text.tag_configure(
            "selected_row", background=BG_CARD_HOVER)
        self._players_text.bind("<Button-1>", self._on_player_click)

        # Right panel
        self._detail_panel = ctk.CTkScrollableFrame(
            main, fg_color=BG_PANEL, corner_radius=8,
            scrollbar_button_color=AMBER_DIM,
            scrollbar_button_hover_color=AMBER,
        )
        self._detail_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self._detail_placeholder = ctk.CTkLabel(
            self._detail_panel, text="Select a game",
            font=(TITLE_FONT_FAMILY, 16), text_color=FG_DIM,
        )
        self._detail_placeholder.pack(pady=40)

        # Status bar
        status = ctk.CTkFrame(self.root, fg_color=BG_PANEL, height=36, corner_radius=0)
        status.pack(fill="x", pady=(4, 0))
        status.pack_propagate(False)

        self._status_label = ctk.CTkLabel(
            status, text="No data loaded.", font=(FONT_FAMILY, 13),
            text_color=FG_DIM, anchor="w",
        )
        self._status_label.pack(side="left", padx=8)

        self._progress_bar = ctk.CTkProgressBar(
            status, height=10, corner_radius=4,
            fg_color=BG_DARK, progress_color=AMBER,
        )
        # Hidden initially

        self._refresh_btn = ctk.CTkButton(
            status, text="REFRESH", width=80, height=26,
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=NEON_ORANGE, command=self._start_scrape,
        )
        self._refresh_btn.pack(side="right", padx=8)

        self._hidden_btn = ctk.CTkButton(
            status, text="Hidden (0)", width=90, height=26,
            font=(FONT_FAMILY, 12), fg_color=BG_CARD,
            hover_color=BG_CARD_HOVER, text_color=FG_DIM,
            command=self._show_hidden_dialog,
        )

        # Context menu (tk.Menu works fine inside CTk)
        self._ctx_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=FG_DEFAULT,
                                  activebackground=AMBER_DIM,
                                  activeforeground=AMBER_BRIGHT,
                                  font=(FONT_FAMILY, _sfont(12)))
        self._ctx_menu.add_command(label="Hide game", command=self._ctx_hide_game)
        self._ctx_game_id: str | None = None

        # Detail leaderboard context menu
        self._lb_ctx_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=FG_DEFAULT,
                                     activebackground=AMBER_DIM,
                                     activeforeground=AMBER_BRIGHT,
                                     font=(FONT_FAMILY, _sfont(12)))
        self._lb_ctx_menu.add_command(label="Search this user",
                                       command=self._ctx_search_user)
        self._lb_ctx_username: str | None = None

    # ── Data Loading ────────────────────────────────────────────

    def _load_existing_data(self):
        data = load_data()
        if data:
            self.data = data
            self._status_label.configure(text=f"{len(data)} games loaded. Refreshing...")
            self._refresh_list()
        else:
            self._status_label.configure(text="No data. Loading...")
        self.root.after(100, self._start_scrape)
        # Always refresh tournaments in background — needed for popup diffs
        self.root.after(300, self._load_tournaments)

    def _load_tournaments(self):
        """Fetch tournaments + pre-cache scores in background."""
        def do_fetch():
            try:
                tournaments = fetch_tournaments()
                for t in tournaments:
                    tid = t.get("id")
                    if tid is None or tid in self._tournament_scores_cache:
                        continue
                    if t.get("status") == "Upcoming":
                        continue  # no leaderboard yet
                    try:
                        scores = fetch_tournament_scores(tid)
                        self._tournament_scores_cache[tid] = scores
                    except Exception:
                        pass
                self.root.after(0, lambda: self._on_tournaments_loaded(tournaments))
            except Exception:
                pass

        self._thumb_pool.submit(do_fetch)

    def _on_tournaments_loaded(self, tournaments: list[dict]):
        self._tournaments = tournaments
        self._tournaments_loaded = True
        try:
            save_tournaments_cache(self._tournaments,
                                    self._tournament_scores_cache)
        except Exception:
            pass
        if self._current_view == "tournaments":
            self._refresh_list()
        # Tournament data changed — re-check snapshot for new popup items
        if self._pending_compare or self.data:
            self._maybe_compare_snapshot()

    def _load_hidden_games(self):
        settings = load_settings()
        self._hidden_games = set(settings.get("hidden_games", []))

    def _save_hidden_games(self):
        settings = load_settings()
        settings["hidden_games"] = sorted(self._hidden_games)
        save_settings(settings)

    def _update_hidden_btn(self):
        n = len(self._hidden_games)
        if n > 0:
            self._hidden_btn.configure(text=f"Hidden ({n})")
            self._hidden_btn.pack(side="right", padx=(0, 8))
        else:
            self._hidden_btn.pack_forget()

    # ── Login ───────────────────────────────────────────────────

    def _load_token(self):
        settings = load_settings()
        token = settings.get("token")
        if is_token_valid(token):
            self._token = token
            username = get_token_username(token)
            self._login_btn.configure(text="LOGOUT")
            self._login_label.configure(text=f"✓ {username}", text_color=NEON_GREEN)
            self._current_view = "my"
            self._view_toggle.set("MY GAMES")
            self.root.after(200, self._fetch_personal_scores)
        else:
            self._token = None
            self._login_btn.configure(text="LOGIN")
            self._login_label.configure(text="", text_color=FG_DIM)
            self._current_view = "all"
            self._view_toggle.set("ALL GAMES")

    def _save_token(self, token: str | None):
        settings = load_settings()
        if token:
            settings["token"] = token
        else:
            settings.pop("token", None)
        save_settings(settings)

    def _start_login(self):
        if self._token:
            self._token = None
            self._personal_scores.clear()
            try:
                save_personal_scores([])
            except Exception:
                pass
            self._save_token(None)
            self._login_btn.configure(text="LOGIN")
            self._login_label.configure(text="", text_color=FG_DIM)
            self._current_view = "all"
            self._view_toggle.set("ALL GAMES")
            self._refresh_list()
            return

        self._login_btn.configure(state="disabled")
        self._login_label.configure(text="Logging in...", text_color=NEON_YELLOW)

        def do_login():
            token, error = login_via_browser()
            self.root.after(0, lambda: self._on_login_done(token, error))

        threading.Thread(target=do_login, daemon=True).start()

    def _on_login_done(self, token, error):
        self._login_btn.configure(state="normal")
        if token and is_token_valid(token):
            self._token = token
            self._save_token(token)
            username = get_token_username(token)
            self._login_btn.configure(text="LOGOUT")
            self._login_label.configure(text=f"✓ {username}", text_color=NEON_GREEN)
            self._current_view = "my"
            self._view_toggle.set("MY GAMES")
            self._fetch_personal_scores()
            self._start_scrape()
        elif error:
            self._login_label.configure(text="Login failed", text_color=NEON_PINK)
            from tkinter import messagebox
            messagebox.showerror("Login Error", error)
        else:
            self._login_label.configure(text="", text_color=FG_DIM)

    def _fetch_personal_scores(self):
        if not is_token_valid(self._token):
            return

        def do_fetch():
            try:
                scores = fetch_personal_scores(self._token)
                self.root.after(0, lambda: self._on_personal_scores(scores))
            except Exception:
                pass

        threading.Thread(target=do_fetch, daemon=True).start()

    def _on_personal_scores(self, scores):
        self._personal_scores = scores
        try:
            save_personal_scores(scores)
        except Exception:
            pass
        self._backfill_missing_games()
        self._refresh_list()
        if self._pending_compare:
            self._pending_compare = False
            self._do_compare_and_popup()

    def _backfill_missing_games(self):
        if not self._personal_scores:
            return
        missing = [s for s in self._personal_scores
                   if str(s.get("game_id", "")) not in self.data
                   and s.get("internal_number")]
        if not missing:
            return

        def do_fetch():
            added = 0
            for ps in missing:
                gid = str(ps["game_id"])
                if gid in self.data:
                    continue
                try:
                    top = fetch_scores(ps["internal_number"])
                    self.data[gid] = {
                        "name": ps.get("name", "Unknown"),
                        "game_id": ps["game_id"],
                        "internal_number": ps["internal_number"],
                        "boxart": ps.get("boxart_480w") or ps.get("boxart", ""),
                        "scores": top,
                    }
                    added += 1
                except Exception:
                    pass
            if added:
                self.root.after(0, self._refresh_list)

        threading.Thread(target=do_fetch, daemon=True).start()

    # ── Scraping ────────────────────────────────────────────────

    def _start_scrape(self):
        self._refresh_btn.configure(state="disabled")
        self._progress_bar.set(0)
        self._progress_bar.pack(side="left", fill="x", expand=True, padx=8)

        def do_scrape():
            estimated = len(self.data) if self.data else 0

            def on_progress(done, found, games_done, name):
                total = found if games_done else max(found, estimated)
                pct = done / total if total else 0
                self.root.after(0, lambda: self._progress_bar.set(pct))
                self.root.after(0, lambda: self._status_label.configure(
                    text=f"Loading [{done}/{total}] {name}"))

            try:
                data = scrape_all(progress_callback=on_progress)
                save_data(data)
                self.root.after(0, lambda: self._on_scrape_done(data))
            except Exception as e:
                self.root.after(0, lambda: self._on_scrape_error(str(e)))

        threading.Thread(target=do_scrape, daemon=True).start()

    def _on_scrape_done(self, data):
        self.data = data
        self._status_label.configure(text=f"{len(data)} games loaded.")
        self._progress_bar.pack_forget()
        self._refresh_btn.configure(state="normal")
        self._backfill_missing_games()
        self._refresh_list()
        self._maybe_compare_snapshot()

    def _on_scrape_error(self, error):
        self._progress_bar.pack_forget()
        self._refresh_btn.configure(state="normal")
        from tkinter import messagebox
        messagebox.showerror("Error", f"Scraping failed:\n{error}")

    # ── View Toggle ─────────────────────────────────────────────

    def _on_view_toggle(self, value):
        if value == "MY GAMES":
            self._current_view = "my"
        elif value == "ALL GAMES":
            self._current_view = "all"
        elif value == "TOP PLAYERS":
            self._current_view = "players"
        else:
            self._current_view = "tournaments"
            if not self._tournaments_loaded:
                self._load_tournaments()
        self._update_left_panel_widgets()
        self._refresh_list()

    def _update_left_panel_widgets(self):
        """Show/hide widgets in the left panel based on current view."""
        if self._current_view == "players":
            self._game_list.pack_forget()
            self._sort_combo.pack_forget()
            self._sort_label.pack_forget()
            self._players_frame.pack(fill="both", expand=True,
                                       padx=4, pady=(0, 4))
            # Reset selection and clear detail panel
            self._selected_player = None
            self._clear_detail_panel("Select a player")
        else:
            self._players_frame.pack_forget()
            self._sort_label.pack(side="right", padx=(0, 4))
            self._sort_combo.pack(side="right")
            self._game_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))
            # Clear any lingering player detail when leaving players view
            if self._selected_player is not None:
                self._selected_player = None
                self._clear_detail_panel("Select a game")

    def _clear_detail_panel(self, placeholder: str = ""):
        for w in self._detail_panel.winfo_children():
            w.destroy()
        if placeholder:
            ctk.CTkLabel(
                self._detail_panel, text=placeholder,
                font=(TITLE_FONT_FAMILY, 16), text_color=FG_DIM,
            ).pack(pady=40)

    # ── Game List ───────────────────────────────────────────────

    def _get_personal_map(self) -> dict[str, dict]:
        pmap = {}
        if not self._personal_scores or not self._token:
            return pmap
        for ps in self._personal_scores:
            gid = str(ps.get("game_id", ""))
            pmap[gid] = {
                "rank": ps.get("rank"),
                "userName": ps.get("user_name", ""),
                "signature": ps.get("signature", ""),
                "score": str(int(float(ps["score"]))) if ps.get("score") else "0",
                "hardware": ps.get("hardware", ""),
                "createdAt": ps.get("created_at", ""),
            }
        return pmap

    def _resolve_user_ranks(self, scores: list[dict], entry: dict | None,
                             in_top100: bool, search: str) -> tuple:
        """Compute (overall_rank, device_rank, device_name) for the user.

        overall_rank: position in the full top-100 leaderboard, or None if
            the user is beyond top 100.
        device_rank: position among entries sharing the user's hardware
            group, computed from top 100 when the user is in it, else from
            their personal-score rank (assumed device-specific).
        device_name: friendly hardware group name (e.g. "Pinball 4K").
        """
        if not entry:
            return None, None, ""

        hw_code = entry.get("hardware", "")
        device_name = _hw_name(hw_code) if hw_code else ""
        device_group = _HW_GROUPS.get(device_name) if device_name else None

        overall_rank = entry.get("rank") if in_top100 else None

        device_rank = None
        if in_top100 and device_group and search:
            same_device = [
                s for s in scores
                if s.get("hardware", "") in device_group
            ]
            same_device.sort(
                key=lambda s: int(float(s.get("score", "0") or "0")),
                reverse=True,
            )
            for i, s in enumerate(same_device):
                if s.get("userName", "").lower() == search:
                    device_rank = i + 1
                    break
        elif not in_top100:
            # Beyond top 100 — fall back to whatever rank the API gave us
            # via personal_scores (expected to be device-specific).
            r = entry.get("rank")
            if isinstance(r, int) and r > 0:
                device_rank = r

        return overall_rank, device_rank, device_name

    # ── Snapshot & Change Detection ─────────────────────────────

    def _compute_snapshot(self) -> dict:
        """Build a snapshot of the current user state (games + tournaments)."""
        snapshot: dict = {}
        search = get_token_username(self._token).lower() if self._token else ""
        if not search:
            return snapshot

        personal_map = self._get_personal_map()

        # Games
        for gid, game in self.data.items():
            entry = None
            in_top100 = False
            for s in game.get("scores", []):
                if search == s.get("userName", "").lower():
                    entry = s
                    in_top100 = True
                    break
            if entry is None and gid in personal_map:
                entry = personal_map[gid]
            if not entry:
                continue

            try:
                score = int(float(entry.get("score", "0")))
            except (ValueError, TypeError):
                score = 0
            snapshot[gid] = {
                "type": "game",
                "name": game.get("name", ""),
                "score": score,
                "rank": entry.get("rank") if in_top100 else None,
            }

        # Tournaments — one entry per (tournament, game) where user has a score
        for t in self._tournaments:
            tid = t.get("id")
            if tid is None:
                continue
            games = self._tournament_scores_cache.get(tid, [])
            for g in games:
                game_name = g.get("name", "")
                for s in g.get("scores", []):
                    if search != s.get("userName", "").lower():
                        continue
                    try:
                        score = int(float(s.get("score", "0")))
                    except (ValueError, TypeError):
                        score = 0
                    key = f"t:{tid}:{game_name}"
                    snapshot[key] = {
                        "type": "tournament",
                        "name": game_name,
                        "tournament_name": t.get("name", ""),
                        "score": score,
                        "rank": s.get("rank"),
                    }
                    break
        return snapshot

    def _compute_changes(self, old: dict, new: dict,
                         current_tids: set[int] | None = None
                         ) -> tuple[list, list]:
        """Return (improvements, overtaken) by diffing old vs new snapshots."""
        improvements = []
        overtaken = []
        current_tids = current_tids or set()

        for gid, new_e in new.items():
            entry_type = new_e.get("type", "game")
            tournament_name = new_e.get("tournament_name", "")
            new_score = new_e.get("score", 0)
            new_rank = new_e.get("rank")

            old_e = old.get(gid)
            if old_e is None:
                # Game entries always exist in old when the user has any score,
                # so a missing key is uninteresting. Tournament entries only
                # exist while in Top 50, so a new key means the user just
                # entered the leaderboard.
                if entry_type == "tournament" and new_rank is not None:
                    improvements.append({
                        "type": entry_type,
                        "tournament_name": tournament_name,
                        "name": new_e["name"],
                        "old_score": 0,
                        "new_score": new_score,
                        "score_diff": 0,
                        "old_rank": None,
                        "new_rank": new_rank,
                        "rank_diff": 0,
                    })
                continue

            old_score = old_e.get("score", 0)
            old_rank = old_e.get("rank")

            score_diff = new_score - old_score

            rank_improved = False
            rank_worsened = False
            rank_diff = 0
            if old_rank is not None and new_rank is not None:
                rank_diff = old_rank - new_rank
                rank_improved = rank_diff > 0
                rank_worsened = rank_diff < 0
            elif old_rank is None and new_rank is not None:
                rank_improved = True  # entered Top 100
            elif old_rank is not None and new_rank is None:
                rank_worsened = True  # dropped out

            if score_diff > 0 or rank_improved:
                improvements.append({
                    "type": entry_type,
                    "tournament_name": tournament_name,
                    "name": new_e["name"],
                    "old_score": old_score,
                    "new_score": new_score,
                    "score_diff": score_diff,
                    "old_rank": old_rank,
                    "new_rank": new_rank,
                    "rank_diff": rank_diff,
                })
            elif rank_worsened:
                overtaken.append({
                    "type": entry_type,
                    "tournament_name": tournament_name,
                    "name": new_e["name"],
                    "old_rank": old_rank,
                    "new_rank": new_rank,
                    "rank_diff": -rank_diff if rank_diff else 0,
                })

        # Tournament entries that disappeared from `new` ⇒ fell out of Top 50.
        # Only flag this when the tournament is still being tracked, so that
        # tournaments dropping out of the API window don't produce noise.
        for key, old_e in old.items():
            if key in new:
                continue
            if old_e.get("type") != "tournament":
                continue
            if old_e.get("rank") is None:
                continue
            try:
                tid = int(key.split(":", 2)[1])
            except (IndexError, ValueError):
                continue
            if tid not in current_tids:
                continue
            overtaken.append({
                "type": "tournament",
                "tournament_name": old_e.get("tournament_name", ""),
                "name": old_e.get("name", ""),
                "old_rank": old_e.get("rank"),
                "new_rank": None,
                "rank_diff": 0,
            })

        # Sort: largest score/rank improvements first
        improvements.sort(key=lambda x: (-x["score_diff"], -x["rank_diff"]))
        overtaken.sort(key=lambda x: -x["rank_diff"])
        return improvements, overtaken

    def _maybe_compare_snapshot(self):
        """Called after scrape completes. Defer if personal scores not ready."""
        if not self._token:
            # Not logged in — no personal state to compare
            return
        if not self._personal_scores:
            # Defer until personal scores arrive
            self._pending_compare = True
            return
        self._do_compare_and_popup()

    def _do_compare_and_popup(self):
        """Compute snapshot, compare to previous, show popup if changes exist."""
        new_snapshot = self._compute_snapshot()
        if not new_snapshot:
            return

        if self._prev_snapshot:
            current_tids = {t.get("id") for t in self._tournaments
                            if t.get("id") is not None}
            improvements, overtaken = self._compute_changes(
                self._prev_snapshot, new_snapshot, current_tids)
            if improvements or overtaken:
                self._show_changes_popup(improvements, overtaken)

        self._prev_snapshot = new_snapshot
        try:
            save_snapshot(new_snapshot)
        except Exception:
            pass

    def _show_changes_popup(self, improvements: list, overtaken: list):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Score Update")
        dlg.geometry("560x560")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(fg_color=BG_PANEL)

        ctk.CTkLabel(
            dlg, text="UPDATE SINCE LAST SESSION",
            font=(TITLE_FONT_FAMILY, 16), text_color=AMBER,
        ).pack(pady=(16, 6))

        quote = random.choice(MOTIVATING_QUOTES)
        ctk.CTkLabel(
            dlg, text=f'"{quote}"', font=(FONT_FAMILY, 12, "italic"),
            text_color=NEON_YELLOW, wraplength=500, justify="center",
        ).pack(pady=(0, 10), padx=20)

        ctk.CTkFrame(dlg, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=16, pady=4)

        scroll = ctk.CTkScrollableFrame(
            dlg, fg_color=BG_DARK,
            scrollbar_button_color=AMBER_DIM,
            scrollbar_button_hover_color=AMBER,
        )
        scroll.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        def _ceiling(is_tournament: bool) -> tuple[int, str]:
            # Leaderboard size — tournaments use Top 50, games use Top 100
            return (50, "Top 50") if is_tournament else (100, "Top 100")

        def _header_text(entry) -> tuple[str, str]:
            """Returns (title, subtitle) for a change entry."""
            if entry.get("type") == "tournament":
                return entry["name"], f"Tournament: {entry.get('tournament_name', '')}"
            return entry["name"], ""

        if improvements:
            ctk.CTkLabel(
                scroll, text=f"▲ IMPROVEMENTS ({len(improvements)})",
                font=(FONT_FAMILY, 14, "bold"),
                text_color=NEON_GREEN, anchor="w",
            ).pack(fill="x", pady=(6, 4), padx=4)

            for imp in improvements:
                is_t = imp.get("type") == "tournament"
                ceiling, ceiling_label = _ceiling(is_t)
                title, subtitle = _header_text(imp)

                row = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=4)
                row.pack(fill="x", pady=2, padx=4)
                ctk.CTkLabel(
                    row, text=title, font=(FONT_FAMILY, 13, "bold"),
                    text_color=AMBER_BRIGHT, anchor="w",
                ).pack(fill="x", padx=8, pady=(6, 0))
                if subtitle:
                    ctk.CTkLabel(
                        row, text=subtitle, font=(FONT_FAMILY, 13),
                        text_color=NEON_CYAN, anchor="w",
                    ).pack(fill="x", padx=8)

                if imp["score_diff"] > 0:
                    txt = (f"{_format_score(str(imp['old_score']))}"
                           f"  →  {_format_score(str(imp['new_score']))}"
                           f"   (+{_format_score(str(imp['score_diff']))} pts)")
                    ctk.CTkLabel(
                        row, text=txt, font=(FONT_FAMILY, 12),
                        text_color=NEON_GREEN, anchor="w",
                    ).pack(fill="x", padx=8, pady=1)

                if imp["rank_diff"] > 0:
                    old_r = f"#{imp['old_rank']}" if imp['old_rank'] else f"> {ceiling}"
                    new_r = f"#{imp['new_rank']}" if imp['new_rank'] else f"> {ceiling}"
                    txt = f"Rank: {old_r}  →  {new_r}   (+{imp['rank_diff']} places)"
                    ctk.CTkLabel(
                        row, text=txt, font=(FONT_FAMILY, 12),
                        text_color=NEON_CYAN, anchor="w",
                    ).pack(fill="x", padx=8, pady=(1, 6))
                elif imp["old_rank"] is None and imp["new_rank"] is not None:
                    txt = f"Entered {ceiling_label} — now #{imp['new_rank']}!"
                    ctk.CTkLabel(
                        row, text=txt, font=(FONT_FAMILY, 12, "bold"),
                        text_color=GOLD, anchor="w",
                    ).pack(fill="x", padx=8, pady=(1, 6))
                else:
                    ctk.CTkLabel(row, text="", height=1).pack()

        if overtaken:
            ctk.CTkLabel(
                scroll, text=f"▼ OVERTAKEN ({len(overtaken)})",
                font=(FONT_FAMILY, 14, "bold"),
                text_color=NEON_PINK, anchor="w",
            ).pack(fill="x", pady=(14, 4), padx=4)

            for ot in overtaken:
                is_t = ot.get("type") == "tournament"
                ceiling, ceiling_label = _ceiling(is_t)
                title, subtitle = _header_text(ot)

                row = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=4)
                row.pack(fill="x", pady=2, padx=4)
                ctk.CTkLabel(
                    row, text=title, font=(FONT_FAMILY, 13, "bold"),
                    text_color=AMBER_BRIGHT, anchor="w",
                ).pack(fill="x", padx=8, pady=(6, 0))
                if subtitle:
                    ctk.CTkLabel(
                        row, text=subtitle, font=(FONT_FAMILY, 13),
                        text_color=NEON_CYAN, anchor="w",
                    ).pack(fill="x", padx=8)

                old_r = f"#{ot['old_rank']}" if ot['old_rank'] else f"> {ceiling}"
                new_r = f"#{ot['new_rank']}" if ot['new_rank'] else f"> {ceiling}"
                if ot['new_rank'] is None and ot['old_rank'] is not None:
                    txt = f"Fell out of {ceiling_label}  (was {old_r})"
                else:
                    txt = f"Rank: {old_r}  →  {new_r}   (-{ot['rank_diff']} places)"
                ctk.CTkLabel(
                    row, text=txt, font=(FONT_FAMILY, 12),
                    text_color=NEON_PINK, anchor="w",
                ).pack(fill="x", padx=8, pady=(1, 6))

        ctk.CTkButton(
            dlg, text="KEEP PLAYING", width=160, height=34,
            font=(FONT_FAMILY, 13, "bold"),
            fg_color=AMBER_DIM, hover_color=AMBER,
            text_color=BG_DARK, command=dlg.destroy,
        ).pack(pady=(0, 14))

    # Default game tiers (top 100/50/10/#1)
    _GAME_TIERS = [
        (100, "Enter Top 100", "top100"),
        (50, "Enter Top 50", "top50"),
        (10, "Enter Top 10", "top10"),
        (1, "Become #1", "high"),
    ]
    # Tournament tiers — tournaments expose only top 50
    _TOURNAMENT_TIERS = [
        (50, "Enter Top 50", "top50"),
        (10, "Enter Top 10", "top10"),
        (1, "Become #1", "high"),
    ]

    def _compute_target(self, user_score: int, thresholds: dict,
                         tiers: list | None = None) -> tuple:
        """Returns (next_target_label, gap_str, target_score_str, progress)."""
        milestones = tiers if tiers is not None else self._GAME_TIERS
        for rank, label, key in milestones:
            th_val = thresholds.get(key, "")
            if not th_val:
                continue
            try:
                th_score = int(float(th_val))
            except (ValueError, TypeError):
                continue
            if user_score < th_score:
                gap = th_score - user_score
                progress = user_score / th_score if th_score > 0 else 0
                return (label, f"+{_format_score(str(gap))}",
                        _format_score(str(th_score)), progress)
        # User is #1 or above all thresholds
        return "", "", "", 1.0

    def _refresh_list(self):
        """Debounced refresh — coalesces rapid calls into one rebuild."""
        if self._refresh_pending is not None:
            self.root.after_cancel(self._refresh_pending)
        self._refresh_pending = self.root.after(50, self._do_refresh_list)

    def _do_refresh_list(self):
        self._refresh_pending = None

        if self._current_view == "tournaments":
            self._populate_tournament_list()
            return

        if self._current_view == "players":
            self._populate_players_list()
            return

        personal_map = self._get_personal_map()
        search = get_token_username(self._token).lower() if self._token else ""

        items = []

        for gid, game in self.data.items():
            if gid in self._hidden_games:
                continue
            th = _get_thresholds(game["scores"])

            entry = None
            in_top100 = False
            if search:
                for s in game["scores"]:
                    if search == s.get("userName", "").lower():
                        entry = s
                        in_top100 = True
                        break
                if entry is None and gid in personal_map:
                    entry = personal_map[gid]

            if self._current_view == "my" and entry is None:
                continue

            overall_r, device_r, device_nm = self._resolve_user_ranks(
                game.get("scores", []), entry, in_top100, search)

            items.append((gid, game, entry, in_top100, th,
                          overall_r, device_r, device_nm))

        if self._current_view == "my":
            existing_gids = {i[0] for i in items}
            for gid, ps_entry in personal_map.items():
                if gid not in existing_gids and gid not in self._hidden_games:
                    stub = {"name": ps_entry.get("userName", "Unknown"),
                            "scores": []}
                    overall_r, device_r, device_nm = self._resolve_user_ranks(
                        [], ps_entry, False, search)
                    items.append((gid, stub, ps_entry, False,
                                   _get_thresholds([]),
                                   overall_r, device_r, device_nm))

        # Sort
        sort_key = self._sort_var.get()
        if sort_key == "Rank":
            def rank_sort(item):
                overall = item[5]
                device = item[6]
                if overall is not None:
                    return (0, overall, device if device is not None else 9999)
                if device is not None:
                    return (1, device)
                return (2, 0)
            items.sort(key=rank_sort)
        elif sort_key == "Name":
            items.sort(key=lambda x: x[1]["name"].lower())
        elif sort_key == "Score":
            def score_sort(item):
                if not item[2]:
                    return 0
                try:
                    return -int(float(item[2].get("score", "0")))
                except (ValueError, TypeError):
                    return 0
            items.sort(key=score_sort)

        # Build lightweight item dicts for the canvas
        accent_colors = [NEON_PINK, NEON_CYAN, NEON_GREEN, NEON_ORANGE,
                          AMBER_BRIGHT, NEON_YELLOW]
        canvas_items = []
        for idx, (gid, game, entry, in_top100, th,
                  overall_r, device_r, device_nm) in enumerate(items):
            if entry:
                try:
                    user_score = int(float(entry.get("score", "0")))
                except (ValueError, TypeError):
                    user_score = 0
                rank_str = _format_rank_display(overall_r, device_r, device_nm)
                score_str = _format_score(entry.get("score", "0"))
                target_label, gap_str, target_score, progress = self._compute_target(user_score, th)
            else:
                rank_str = "—"
                score_str = ""
                target_label = ""
                gap_str = ""
                target_score = ""
                progress = 0

            canvas_items.append({
                "gid": gid,
                "name": game.get("name", "Unknown"),
                "rank_str": rank_str,
                "score_str": score_str,
                "target": target_label,
                "gap_str": gap_str,
                "target_score": target_score,
                "accent": accent_colors[idx % len(accent_colors)],
                "boxart_url": game.get("boxart", "") if isinstance(game, dict) else "",
            })

        self._game_list.set_items(canvas_items)
        self._game_list.set_selected(self._selected_game_id)

        count = len(items)
        total = len(self.data) - len(self._hidden_games)
        if self._current_view == "my":
            self._count_label.configure(text=f"{count} ranked games / {total} total")
        else:
            self._count_label.configure(text=f"{count} games")

        self._update_hidden_btn()

    # ── Top Players View ────────────────────────────────────────

    def _populate_players_list(self):
        """Rank all players by points earned across Top 100 entries."""
        totals: dict[str, int] = {}
        for game in self.data.values():
            for s in game.get("scores", []):
                name = s.get("userName", "")
                rank = s.get("rank")
                if not name or rank is None:
                    continue
                try:
                    r = int(rank)
                except (ValueError, TypeError):
                    continue
                if 1 <= r <= 100:
                    totals[name] = totals.get(name, 0) + (101 - r)

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        self._ranked_players = ranked
        me = get_token_username(self._token).lower() if self._token else ""

        txt = self._players_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.mark_unset("me_mark") if "me_mark" in txt.mark_names() else None

        for i, (name, pts) in enumerate(ranked, 1):
            pts_str = f"{pts:,}".replace(",", ".")
            line = f"#{i}\t{name}\t{pts_str}\n"

            row_tag = "row_a" if i % 2 else "row_b"
            is_me = bool(me) and name.lower() == me
            if is_me:
                tags = (row_tag, "me")
            elif i == 1:
                tags = (row_tag, "top1")
            elif i == 2:
                tags = (row_tag, "top2")
            elif i == 3:
                tags = (row_tag, "top3")
            elif i <= 10:
                tags = (row_tag, "top10")
            else:
                tags = (row_tag,)

            line_start = txt.index("end - 1c")
            txt.insert("end", line, tags)
            if is_me:
                txt.mark_set("me_mark", line_start)
                txt.mark_gravity("me_mark", "left")

        txt.configure(state="disabled")

        self._show_me_btn.configure(
            state="normal" if me and "me_mark" in txt.mark_names() else "disabled")
        self._count_label.configure(text=f"{len(ranked)} players ranked")
        self._update_hidden_btn()

    def _on_players_resize(self, event):
        # Right-align points column near the widget's right edge
        # (account for padx=12 on each side + a few px breathing room)
        right = max(_sf(140), event.width - _sf(24))
        self._players_text.configure(tabs=f"{_sf(56)} {right} right")

    def _on_player_click(self, event):
        txt = self._players_text
        idx = txt.index(f"@{event.x},{event.y}")
        try:
            line_no = int(idx.split(".")[0])
        except (ValueError, IndexError):
            return
        # Data starts at line 1 (no header rows)
        pos = line_no - 1
        if pos < 0 or pos >= len(self._ranked_players):
            return
        name, _pts = self._ranked_players[pos]
        self._select_player(name, line_no)

    def _select_player(self, name: str, line_no: int):
        self._selected_player = name
        txt = self._players_text
        txt.tag_remove("selected_row", "1.0", "end")
        # Don't override the "me" row's own background highlight
        me = get_token_username(self._token).lower() if self._token else ""
        if not (me and name.lower() == me):
            txt.tag_add("selected_row", f"{line_no}.0", f"{line_no + 1}.0")
        self._show_player_detail(name)

    def _show_player_detail(self, name: str):
        self._clear_detail_panel()

        # Collect all top-100 entries for this player across all games
        entries = []
        for gid, game in self.data.items():
            for s in game.get("scores", []):
                if s.get("userName", "") == name:
                    r = s.get("rank")
                    try:
                        r_int = int(r) if r is not None else None
                    except (ValueError, TypeError):
                        r_int = None
                    if r_int is not None and 1 <= r_int <= 100:
                        entries.append((gid, game, s, r_int))
                        break
        entries.sort(key=lambda e: e[3])

        total_pts = sum(101 - r for (_, _, _, r) in entries)
        try:
            overall_idx = next(
                i for i, (n, _) in enumerate(self._ranked_players, 1)
                if n == name)
        except StopIteration:
            overall_idx = None

        # Header
        header = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            header, text=name.upper(),
            font=(TITLE_FONT_FAMILY, 17), text_color=AMBER,
            anchor="w",
        ).pack(fill="x")

        rank_str = f"Overall rank: #{overall_idx}" if overall_idx else ""
        summary = f"{total_pts:,}".replace(",", ".") + " points"
        if rank_str:
            summary = f"{rank_str}   ·   {summary}   ·   {len(entries)} Top-100 entries"
        else:
            summary = f"{summary}   ·   {len(entries)} Top-100 entries"
        ctk.CTkLabel(
            header, text=summary, font=(FONT_FAMILY, 13),
            text_color=NEON_YELLOW, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=12, pady=(8, 6))

        ctk.CTkLabel(
            self._detail_panel, text="▸ RANKINGS",
            font=(FONT_FAMILY, 14, "bold"), text_color=GOLD, anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 4))

        # Rows: rank · game · score (click → jump to game)
        for (gid, game, s, r) in entries:
            row = ctk.CTkFrame(self._detail_panel, fg_color=BG_CARD,
                                corner_radius=3, cursor="hand2")
            row.pack(fill="x", padx=12, pady=1)

            if r == 1:
                rc = GOLD
            elif r <= 3:
                rc = AMBER_BRIGHT
            elif r <= 10:
                rc = AMBER
            else:
                rc = FG_DEFAULT

            rank_lbl = ctk.CTkLabel(row, text=f"#{r}", width=48,
                                     font=(FONT_FAMILY, 13, "bold"),
                                     text_color=rc, anchor="w", cursor="hand2")
            rank_lbl.pack(side="left", padx=(10, 0), pady=4)
            name_lbl = ctk.CTkLabel(row, text=game.get("name", "")[:40],
                                     font=(FONT_FAMILY, 13),
                                     text_color=FG_DEFAULT, anchor="w",
                                     cursor="hand2")
            name_lbl.pack(side="left", fill="x", expand=True, pady=4)
            score_lbl = ctk.CTkLabel(row,
                                      text=_format_score(str(s.get("score", "0"))),
                                      font=(FONT_FAMILY, 13),
                                      text_color=NEON_YELLOW, anchor="e",
                                      cursor="hand2")
            score_lbl.pack(side="right", padx=(0, 12), pady=4)

            for w in [row, rank_lbl, name_lbl, score_lbl]:
                w.bind("<Button-1>",
                       lambda e, g=gid: self._jump_to_game(g))

    def _scroll_to_me(self):
        txt = self._players_text
        if "me_mark" not in txt.mark_names():
            return
        try:
            idx = txt.index("me_mark")
            line_no = int(idx.split(".")[0])
        except Exception:
            return
        top_line = max(1, line_no - 6)
        txt.see(f"{top_line}.0")
        txt.see("me_mark")

        # Also select the player so the detail panel opens
        me_lower = get_token_username(self._token).lower() if self._token else ""
        if not me_lower:
            return
        found = next((n for n, _ in self._ranked_players
                      if n.lower() == me_lower), None)
        if found:
            self._select_player(found, line_no)

    def _jump_to_player(self, name: str):
        """Switch to Top Players view and select the given player."""
        if not name:
            return
        self._view_toggle.set("TOP PLAYERS")
        self._current_view = "players"
        self._update_left_panel_widgets()
        self._populate_players_list()

        target = name.lower()
        line_no = None
        for i, (n, _) in enumerate(self._ranked_players, 1):
            if n.lower() == target:
                line_no = i  # lines match rank numbers 1:1 now
                break
        if line_no is None:
            self._clear_detail_panel(f"{name} — not in any Top 100")
            return

        txt = self._players_text
        top_line = max(1, line_no - 6)
        txt.see(f"{top_line}.0")
        txt.see(f"{line_no}.0")
        # Use the original-cased name from ranked list
        canonical = next(n for n, _ in self._ranked_players
                          if n.lower() == target)
        self._select_player(canonical, line_no)

    def _jump_to_game(self, game_id: str):
        """Switch to All Games view and select the given game."""
        if game_id not in self.data:
            return
        self._view_toggle.set("ALL GAMES")
        self._current_view = "all"
        self._selected_player = None
        self._update_left_panel_widgets()
        self._selected_game_id = game_id
        self._refresh_list()
        self._show_detail(game_id)
        # After refresh completes, ensure the card is marked selected
        self.root.after(80, lambda: self._game_list.set_selected(game_id))

    # ── Tournament View ─────────────────────────────────────────

    def _populate_tournament_list(self):
        """Build canvas items for the tournaments view."""
        search = get_token_username(self._token).lower() if self._token else ""
        canvas_items = []

        if not self._tournaments:
            self._game_list.set_items([])
            self._count_label.configure(
                text="Loading tournaments…" if not self._tournaments_loaded
                else "No tournaments found")
            self._update_hidden_btn()
            return

        active_count = 0
        upcoming_count = 0
        for t in self._tournaments:
            status = t.get("status", "")
            if status == "Active":
                active_count += 1
                status_color = NEON_GREEN
            elif status == "Upcoming":
                upcoming_count += 1
                status_color = NEON_YELLOW
            elif status == "Expired":
                status_color = NEON_PINK
            else:
                status_color = NEON_YELLOW

            start = (t.get("start", "") or "")[:10]
            end = (t.get("end", "") or "")[:10]
            dates = f"{start} → {end}" if start else ""

            tid = t.get("id")
            cached = self._tournament_scores_cache.get(tid, [])
            game_count = len(cached)
            if status == "Upcoming":
                subtitle = f"{len(t.get('game_ids') or [])} games" if t.get("game_ids") else "Coming soon"
            else:
                subtitle = f"{game_count} games" if game_count else ""

            # Boxart of the first game, if available
            boxart_url = ""
            if cached:
                boxart_url = cached[0].get("boxart", "") or ""

            # User's best rank across the tournament's games
            user_rank_str = ""
            if search and cached:
                best_rank = None
                best_game = None
                best_entry = None
                for g in cached:
                    for s in g.get("scores", []):
                        if search == s.get("userName", "").lower():
                            r = s.get("rank")
                            if r is not None and (best_rank is None or r < best_rank):
                                best_rank = r
                                best_game = g
                                best_entry = s
                if best_rank and best_entry:
                    overall_r, device_r, device_nm = self._resolve_user_ranks(
                        best_game.get("scores", []), best_entry, True, search)
                    user_rank_str = f"Your best: {_format_rank_display(overall_r, device_r, device_nm)}"

            canvas_items.append({
                "kind": "tournament",
                "gid": str(tid),
                "name": t.get("name", "Unknown"),
                "status": status.upper(),
                "status_color": status_color,
                "dates": dates,
                "subtitle": subtitle,
                "user_rank_str": user_rank_str,
                "accent": NEON_CYAN if status in ("Active", "Upcoming") else FG_DIM,
                "boxart_url": boxart_url,
                # Unused but required by canvas code paths
                "rank_str": "", "score_str": "",
                "target": "", "gap_str": "", "target_score": "",
            })

        self._game_list.set_items(canvas_items)
        self._game_list.set_selected(
            str(self._selected_tournament_id) if self._selected_tournament_id else None)

        total = len(self._tournaments)
        parts = [f"{total} tournaments", f"{active_count} active"]
        if upcoming_count:
            parts.append(f"{upcoming_count} upcoming")
        self._count_label.configure(text="  ·  ".join(parts))
        self._update_hidden_btn()

    def _show_tournament_detail(self, tournament_id: int):
        """Render tournament games + top scores in the right detail panel."""
        tournament = next(
            (t for t in self._tournaments if t.get("id") == tournament_id), None)
        if not tournament:
            return

        for w in self._detail_panel.winfo_children():
            w.destroy()

        # Header
        header = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            header, text=tournament.get("name", "").upper(),
            font=(TITLE_FONT_FAMILY, 17), text_color=AMBER,
            anchor="w", wraplength=500,
        ).pack(fill="x")

        status = tournament.get("status", "")
        start = (tournament.get("start", "") or "")[:10]
        end = (tournament.get("end", "") or "")[:10]
        meta = f"{status}   ·   {start} → {end}" if start else status
        status_color = NEON_GREEN if status == "Active" else NEON_PINK
        ctk.CTkLabel(
            header, text=meta, font=(FONT_FAMILY, 13),
            text_color=status_color, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        games = self._tournament_scores_cache.get(tournament_id)

        if games is None:
            loading = ctk.CTkLabel(
                self._detail_panel, text="Loading scores…",
                font=(FONT_FAMILY, 13), text_color=FG_DIM,
            )
            loading.pack(pady=20)

            def do_fetch():
                try:
                    scores = fetch_tournament_scores(tournament_id)
                    self._tournament_scores_cache[tournament_id] = scores
                    self.root.after(0,
                        lambda: self._show_tournament_detail(tournament_id))
                    self.root.after(0, self._refresh_list)
                except Exception:
                    self.root.after(0, lambda: loading.configure(text="Load failed"))

            self._thumb_pool.submit(do_fetch)
            return

        if not games:
            ctk.CTkLabel(
                self._detail_panel, text="No scores available.",
                font=(FONT_FAMILY, 13), text_color=FG_DIM,
            ).pack(pady=20)
            return

        search = get_token_username(self._token).lower() if self._token else ""

        for gi, g in enumerate(games):
            game_name = g.get("name", "Unknown")
            boxart_url = g.get("boxart", "") or ""
            scores = g.get("scores", [])

            # Build per-game thresholds (tournament has top 50 only)
            sorted_scores = sorted(
                scores,
                key=lambda s: int(float(s.get("score", "0") or "0")),
                reverse=True,
            )
            thresholds = {}
            if len(sorted_scores) >= 1:
                thresholds["high"] = sorted_scores[0].get("score", "")
            if len(sorted_scores) >= 10:
                thresholds["top10"] = sorted_scores[9].get("score", "")
            if len(sorted_scores) >= 50:
                thresholds["top50"] = sorted_scores[49].get("score", "")

            # Find user entry
            user_entry = None
            if search:
                for s in scores:
                    if search == s.get("userName", "").lower():
                        user_entry = s
                        break

            if gi > 0:
                ctk.CTkFrame(
                    self._detail_panel, fg_color=BG_HEADER, height=1,
                ).pack(fill="x", padx=12, pady=(10, 6))

            # Header: boxart + game name + user status
            header = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
            header.pack(fill="x", padx=12, pady=(4, 4))

            box = ctk.CTkLabel(header, text="", height=70, width=70,
                                fg_color=BG_DARK, corner_radius=4)
            box.pack(side="left", padx=(0, 10))
            if boxart_url:
                self._load_boxart(boxart_url, box, height=70)

            info = ctk.CTkFrame(header, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                info, text=game_name.upper(),
                font=(FONT_FAMILY, 14, "bold"),
                text_color=GOLD, anchor="w", wraplength=420,
            ).pack(fill="x")

            if user_entry:
                try:
                    user_score_int = int(float(user_entry.get("score", "0")))
                except (ValueError, TypeError):
                    user_score_int = 0

                overall_r, device_r, device_nm = self._resolve_user_ranks(
                    scores, user_entry, True, search)
                rank_display = _format_rank_display(overall_r, device_r, device_nm)

                ctk.CTkLabel(
                    info,
                    text=f"Your Score: {_format_score(user_entry.get('score', '0'))}  "
                         f"|  Rank: {rank_display}",
                    font=(FONT_FAMILY, 13), text_color=NEON_YELLOW, anchor="w",
                ).pack(fill="x", pady=(2, 0))

                target_label, gap_str, target_score, _ = self._compute_target(
                    user_score_int, thresholds, tiers=self._TOURNAMENT_TIERS)
                if target_label:
                    target_text = f"Next Target: {target_label}"
                    if target_score:
                        target_text += f"  [{target_score}]"
                    if gap_str:
                        target_text += f"   ({gap_str})"
                    ctk.CTkLabel(
                        info, text=target_text, font=(FONT_FAMILY, 13),
                        text_color=AMBER_BRIGHT, anchor="w",
                    ).pack(fill="x", pady=(2, 0))
                else:
                    ctk.CTkLabel(
                        info, text="✓ You're #1 here!", font=(FONT_FAMILY, 13, "bold"),
                        text_color=NEON_GREEN, anchor="w",
                    ).pack(fill="x", pady=(2, 0))
            elif search:
                # Not ranked yet — show target based on thresholds
                target_label, _, target_score, _ = self._compute_target(
                    0, thresholds, tiers=self._TOURNAMENT_TIERS)
                if target_label:
                    txt = f"Not ranked · Target: {target_label}"
                    if target_score:
                        txt += f"  [{target_score}]"
                    ctk.CTkLabel(
                        info, text=txt, font=(FONT_FAMILY, 13),
                        text_color=FG_DIM, anchor="w",
                    ).pack(fill="x", pady=(2, 0))

            # Top 10 scores
            if not scores:
                ctk.CTkLabel(
                    self._detail_panel, text="No scores",
                    font=(FONT_FAMILY, 13), text_color=FG_DIM, anchor="w",
                ).pack(fill="x", padx=24, pady=(0, 4))
                continue

            for s in sorted_scores:
                rank = s.get("rank", "?")
                name = s.get("userName", "")
                score = s.get("score", "0")
                is_user = search and search == name.lower()
                fg = NEON_YELLOW if is_user else (
                    GOLD if rank == 1 else (
                        NEON_GREEN if isinstance(rank, int) and rank <= 10 else FG_DEFAULT))

                row = ctk.CTkFrame(self._detail_panel, fg_color="transparent",
                                    cursor="hand2")
                row.pack(fill="x", padx=16)
                ctk.CTkLabel(row, text=f"#{rank}", font=(FONT_FAMILY, 13),
                             text_color=fg, width=40, anchor="e",
                             cursor="hand2").pack(side="left")
                ctk.CTkLabel(row, text=name, font=(FONT_FAMILY, 13),
                             text_color=fg, width=140, anchor="w",
                             cursor="hand2").pack(side="left", padx=(8, 0))
                ctk.CTkLabel(row, text=_format_score(score),
                             font=(FONT_FAMILY, 13), text_color=fg,
                             anchor="e", cursor="hand2").pack(side="left", padx=(4, 0))
                ctk.CTkLabel(row, text=_hw_name(s.get("hardware", "")),
                             font=(FONT_FAMILY, 12), text_color=FG_DIM,
                             width=80, anchor="e",
                             cursor="hand2").pack(side="right")
                if name:
                    for w in [row] + list(row.winfo_children()):
                        w.bind("<Button-1>",
                               lambda e, u=name: self._jump_to_player(u))

    # ── Game Selection & Detail ─────────────────────────────────

    def _select_game(self, game_id: str):
        # In tournaments view, game_id holds a tournament id (as string)
        if self._current_view == "tournaments":
            try:
                tid = int(game_id)
            except (ValueError, TypeError):
                return
            self._selected_tournament_id = tid
            self._game_list.set_selected(game_id)
            self._show_tournament_detail(tid)
            return

        self._selected_game_id = game_id
        self._game_list.set_selected(game_id)
        self._show_detail(game_id)

    def _show_detail(self, game_id: str):
        # Clear detail panel
        for w in self._detail_panel.winfo_children():
            w.destroy()

        game = self.data.get(game_id)
        if not game:
            return

        search = get_token_username(self._token).lower() if self._token else ""
        personal_map = self._get_personal_map()

        # Header: Boxart + Title + User Score
        header = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))

        self._boxart_label = ctk.CTkLabel(header, text="", height=100)
        self._boxart_label.pack(side="left", padx=(0, 12))
        self._load_boxart(game.get("boxart", ""), self._boxart_label, height=100)

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_frame, text=game["name"].upper(),
            font=(TITLE_FONT_FAMILY, 17), text_color=AMBER,
            anchor="w", wraplength=400,
        ).pack(fill="x")

        # User score info
        user_entry = None
        in_top100 = False
        if search:
            for s in game["scores"]:
                if search == s.get("userName", "").lower():
                    user_entry = s
                    in_top100 = True
                    break
            if not user_entry and game_id in personal_map:
                user_entry = personal_map[game_id]

        if user_entry:
            overall_r, device_r, device_nm = self._resolve_user_ranks(
                game.get("scores", []), user_entry, in_top100, search)
            rank_display = _format_rank_display(overall_r, device_r, device_nm)

            ctk.CTkLabel(
                title_frame,
                text=f"Your Score: {_format_score(user_entry.get('score', '0'))}  |  Rank: {rank_display}",
                font=(FONT_FAMILY, 13), text_color=NEON_YELLOW, anchor="w",
            ).pack(fill="x", pady=(4, 0))

        # Separator
        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        # Next Targets section
        th = _get_thresholds(game["scores"])
        if user_entry:
            try:
                user_score = int(float(user_entry.get("score", "0")))
            except (ValueError, TypeError):
                user_score = 0

            ctk.CTkLabel(
                self._detail_panel, text="▸ NEXT TARGETS",
                font=(FONT_FAMILY, 14, "bold"), text_color=NEON_CYAN, anchor="w",
            ).pack(fill="x", padx=12, pady=(0, 4))

            for label, key in [("Top 100", "top100"), ("Top 50", "top50"),
                                ("Top 10", "top10"), ("#1", "high")]:
                th_val = th.get(key, "")
                if not th_val:
                    continue
                try:
                    th_score = int(float(th_val))
                except (ValueError, TypeError):
                    continue

                gap = th_score - user_score
                progress = user_score / th_score if th_score > 0 else 0

                row = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=1)

                if gap <= 0:
                    status_color = NEON_GREEN
                    gap_text = "✓"
                else:
                    status_color = AMBER_BRIGHT if progress > 0.9 else (
                        AMBER if progress > 0.7 else FG_DEFAULT)
                    gap_text = f"+{_compact_score(str(gap))}"

                ctk.CTkLabel(row, text=label, font=(FONT_FAMILY, 13),
                             text_color=FG_DIM, width=65, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=_format_score(str(th_score)),
                             font=(FONT_FAMILY, 13), text_color=status_color,
                             width=120, anchor="e").pack(side="left")
                ctk.CTkLabel(row, text=gap_text, font=(FONT_FAMILY, 13),
                             text_color=status_color, width=80,
                             anchor="e").pack(side="left", padx=(8, 0))

                bar = ctk.CTkProgressBar(
                    row, height=8, corner_radius=3, width=120,
                    fg_color=BG_DARK,
                    progress_color=NEON_GREEN if gap <= 0 else AMBER_DIM,
                )
                bar.set(min(progress, 1.0))
                bar.pack(side="right", padx=(8, 0))

        # Separator
        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        # Time-range scores (loaded on demand)
        time_frame = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
        time_frame.pack(fill="x", padx=12)

        internal = game.get("internal_number", "")
        if internal:
            for period, label, color in [("weekly", "▸ THIS WEEK", NEON_GREEN),
                                          ("monthly", "▸ THIS MONTH", NEON_ORANGE)]:
                section = ctk.CTkFrame(time_frame, fg_color="transparent")
                section.pack(fill="x", pady=(0, 4))
                ctk.CTkLabel(section, text=label, font=(FONT_FAMILY, 14, "bold"),
                             text_color=color, anchor="w").pack(fill="x")
                loading = ctk.CTkLabel(section, text="Loading...",
                                        font=(FONT_FAMILY, 13), text_color=FG_DIM,
                                        anchor="w")
                loading.pack(fill="x")
                self._load_time_scores(internal, period, section, loading)

        # Separator
        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        # Leaderboard
        ctk.CTkLabel(
            self._detail_panel, text="▸ LEADERBOARD",
            font=(FONT_FAMILY, 14, "bold"), text_color=GOLD, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 4))

        scores = game["scores"]

        # Show all available scores (Top 100); append user if outside
        user_shown = False
        display_scores = scores

        for i, s in enumerate(display_scores):
            rank = s.get("rank", i + 1)
            username = s.get("userName", "")
            is_user = search and search == username.lower()
            if is_user:
                user_shown = True

            row = ctk.CTkFrame(self._detail_panel, fg_color="transparent",
                                cursor="hand2")
            row.pack(fill="x", padx=12, pady=0)

            fg = NEON_YELLOW if is_user else (
                GOLD if rank == 1 else (
                    NEON_GREEN if rank <= 10 else FG_DEFAULT))

            ctk.CTkLabel(row, text=f"#{rank}", font=(FONT_FAMILY, 13),
                         text_color=fg, width=40, anchor="e",
                         cursor="hand2").pack(side="left")
            ctk.CTkLabel(row, text=username, font=(FONT_FAMILY, 13),
                         text_color=fg, width=130, anchor="w",
                         cursor="hand2").pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=_format_score(s.get("score", "0")),
                         font=(FONT_FAMILY, 13), text_color=fg,
                         anchor="e", cursor="hand2").pack(side="left", padx=(4, 0))
            ctk.CTkLabel(row, text=_hw_name(s.get("hardware", "")),
                         font=(FONT_FAMILY, 12), text_color=FG_DIM,
                         width=80, anchor="e",
                         cursor="hand2").pack(side="right")

            for w in [row] + list(row.winfo_children()):
                w.bind("<Button-3>",
                       lambda e, u=username: self._show_lb_menu(e, u))
                if username:
                    w.bind("<Button-1>",
                           lambda e, u=username: self._jump_to_player(u))

        # Append user if not shown
        if search and not user_shown and user_entry:
            ctk.CTkLabel(self._detail_panel, text="···",
                         font=(FONT_FAMILY, 12), text_color=FG_DIM).pack(pady=2)
            row = ctk.CTkFrame(self._detail_panel, fg_color=HIGHLIGHT_BG,
                               corner_radius=4, cursor="hand2")
            row.pack(fill="x", padx=12, pady=2)

            overall_r2, device_r2, device_nm2 = self._resolve_user_ranks(
                game.get("scores", []), user_entry, in_top100, search)
            rank_display = _format_rank_display(overall_r2, device_r2, device_nm2)
            ctk.CTkLabel(row, text=rank_display, font=(FONT_FAMILY, 13, "bold"),
                         text_color=NEON_YELLOW, anchor="w",
                         cursor="hand2").pack(side="left", padx=(4, 0))
            user_name = user_entry.get("userName", "")
            ctk.CTkLabel(row, text=user_name,
                         font=(FONT_FAMILY, 13, "bold"), text_color=NEON_YELLOW,
                         width=130, anchor="w",
                         cursor="hand2").pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=_format_score(user_entry.get("score", "0")),
                         font=(FONT_FAMILY, 13, "bold"), text_color=NEON_YELLOW,
                         anchor="e", cursor="hand2").pack(side="left", padx=(4, 0))
            if user_name:
                for w in [row] + list(row.winfo_children()):
                    w.bind("<Button-1>",
                           lambda e, u=user_name: self._jump_to_player(u))

    def _load_time_scores(self, internal_number, period, parent, loading_label):
        def do_fetch():
            try:
                scores = fetch_scores(internal_number, time_range=period)
                self.root.after(0, lambda: self._display_time_scores(
                    scores, parent, loading_label))
            except Exception:
                self.root.after(0, lambda: loading_label.configure(text="—"))

        self._thumb_pool.submit(do_fetch)

    def _display_time_scores(self, scores, parent, loading_label):
        loading_label.destroy()
        if not scores:
            ctk.CTkLabel(parent, text="No scores", font=(FONT_FAMILY, 13),
                         text_color=FG_DIM, anchor="w").pack(fill="x")
            return
        for s in scores[:3]:
            text = f"#{s.get('rank', '?')}  {s.get('userName', '')}  {_format_score(s.get('score', '0'))}"
            ctk.CTkLabel(parent, text=text, font=(FONT_FAMILY, 13),
                         text_color=FG_DEFAULT, anchor="w").pack(fill="x")

    # ── Boxart (detail view) ──────────────────────────────────────

    def _load_boxart(self, url: str, target_label, height: int = 100):
        """Async-load boxart and place in the given label."""
        if not url or target_label is None:
            return
        key = (url, height)
        if key in self._image_cache:
            try:
                target_label.configure(image=self._image_cache[key])
            except Exception:
                pass
            return

        def fetch():
            try:
                resp = self._http.get(url, timeout=8)
                resp.raise_for_status()
                pil = Image.open(io.BytesIO(resp.content))
                # Resize at physical pixel size for sharpness on hi-DPI;
                # CTkImage's size= takes logical units and scales internally.
                physical_h = _sf(height)
                w_physical = (int(pil.width * physical_h / pil.height)
                              if pil.height else physical_h)
                pil = pil.resize((w_physical, physical_h), Image.LANCZOS)
                logical_w = (int(round(w_physical / UI_SCALE))
                             if UI_SCALE > 1.0 else w_physical)
                self.root.after(0, lambda: self._set_boxart(
                    key, pil, logical_w, height, target_label))
            except Exception:
                pass

        self._thumb_pool.submit(fetch)

    def _set_boxart(self, key, pil_img, w, h, target_label):
        try:
            ctk_img = ctk.CTkImage(pil_img, size=(w, h))
            self._image_cache[key] = ctk_img
            try:
                target_label.configure(image=ctk_img)
            except Exception:
                pass
        except Exception:
            pass

    # ── Context Menus ───────────────────────────────────────────

    def _show_card_menu(self, event, game_id):
        if self._current_view == "tournaments":
            return  # no context actions for tournaments
        self._ctx_game_id = game_id
        self._ctx_menu.post(event.x_root, event.y_root)

    def _ctx_hide_game(self):
        if self._ctx_game_id:
            self._hidden_games.add(self._ctx_game_id)
            self._save_hidden_games()
            self._refresh_list()

    def _show_lb_menu(self, event, username):
        self._lb_ctx_username = username
        self._lb_ctx_menu.post(event.x_root, event.y_root)

    def _ctx_search_user(self):
        # For now just print — could switch to viewing that user's games
        pass

    def _show_hidden_dialog(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Hidden Games")
        dlg.geometry("400x350")
        dlg.transient(self.root)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="HIDDEN GAMES", font=(FONT_FAMILY, 14, "bold"),
                     text_color=AMBER).pack(pady=(12, 8))

        scroll = ctk.CTkScrollableFrame(dlg, fg_color=BG_PANEL)
        scroll.pack(fill="both", expand=True, padx=12, pady=4)

        hidden_list = []
        for gid in sorted(self._hidden_games):
            game = self.data.get(gid)
            name = game["name"] if game else f"Game #{gid}"
            hidden_list.append((gid, name))

            row = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=4)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=name, font=(FONT_FAMILY, 12),
                         text_color=FG_DEFAULT, anchor="w").pack(
                side="left", padx=8, pady=4)
            ctk.CTkButton(
                row, text="Unhide", width=60, height=24,
                font=(FONT_FAMILY, 13), fg_color=BG_HEADER,
                hover_color=AMBER_DIM, text_color=FG_DEFAULT,
                command=lambda g=gid, r=row: (
                    self._hidden_games.discard(g),
                    self._save_hidden_games(),
                    r.destroy(),
                ),
            ).pack(side="right", padx=4, pady=4)

        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=12)

        ctk.CTkButton(btn_frame, text="UNHIDE ALL", width=100,
                       font=(FONT_FAMILY, 12, "bold"),
                       fg_color=BG_CARD, hover_color=AMBER_DIM,
                       text_color=NEON_ORANGE,
                       command=lambda: (self._hidden_games.clear(),
                                        self._save_hidden_games(),
                                        dlg.destroy(),
                                        self._refresh_list())).pack(side="left")
        ctk.CTkButton(btn_frame, text="CLOSE", width=80,
                       font=(FONT_FAMILY, 12),
                       fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
                       text_color=FG_DIM,
                       command=lambda: (dlg.destroy(),
                                        self._refresh_list())).pack(side="right")

    # ── Cleanup ─────────────────────────────────────────────────

    def _on_close(self):
        # Persist current data and snapshot so next start is ready
        if self.data:
            try:
                save_data(self.data)
            except Exception:
                pass
        try:
            snapshot = self._compute_snapshot()
            if snapshot:
                save_snapshot(snapshot)
        except Exception:
            pass
        if self._tournaments or self._tournament_scores_cache:
            try:
                save_tournaments_cache(self._tournaments,
                                        self._tournament_scores_cache)
            except Exception:
                pass
        self._thumb_pool.shutdown(wait=False)
        self.root.destroy()


def main():
    app = ScoreChaserApp()
    app.run()


if __name__ == "__main__":
    main()
