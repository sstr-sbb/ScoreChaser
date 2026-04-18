"""ScoreChaser - ATGames Leaderboard Viewer."""

import io
import sys
import threading
import tkinter as tk
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
)

# -- Asset paths (PyInstaller extracts data to sys._MEIPASS) --
if getattr(sys, "frozen", False):
    _ASSET_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _ASSET_DIR = Path(__file__).parent
_FONT_DIR = _ASSET_DIR / "fonts"

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
FONT_FAMILY = "Ubuntu Sans Mono"
TITLE_FONT_FAMILY = FONT_FAMILY  # fallback

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


def _hw_codes_for_filter(name: str) -> set[str] | None:
    if name == "All Devices":
        return None
    return _HW_GROUPS.get(name, set())


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


def _get_thresholds(scores: list[dict], hw_filter: set[str] | None = None) -> dict:
    if hw_filter:
        scores = [s for s in scores if s.get("hardware", "") in hw_filter]
        by_rank = {i + 1: s["score"] for i, s in enumerate(scores)}
    else:
        by_rank = {s["rank"]: s["score"] for s in scores if s.get("rank")}
    return {
        "top100": by_rank.get(100, ""),
        "top50": by_rank.get(50, ""),
        "top10": by_rank.get(10, ""),
        "high": by_rank.get(1, ""),
    }


def _install_font(font_path: Path):
    if not font_path.exists():
        return
    path_str = str(font_path)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.gdi32.AddFontResourceExW(path_str, 0x10, 0)
        except Exception:
            pass
    else:
        try:
            user_fonts = Path.home() / ".local" / "share" / "fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            dest = user_fonts / font_path.name
            if not dest.exists():
                import shutil
                shutil.copy2(path_str, dest)
        except Exception:
            pass


def _load_fonts():
    global TITLE_FONT_FAMILY, FONT_FAMILY
    _install_font(_FONT_DIR / "DSEG14Classic-Bold.ttf")
    _install_font(_FONT_DIR / "DSEG14Classic-Regular.ttf")
    _install_font(_FONT_DIR / "ShareTechMono-Regular.ttf")
    try:
        import tkinter.font as tkfont
        families = [f.lower() for f in tkfont.families()]
        if "dseg14 classic" in families:
            TITLE_FONT_FAMILY = "DSEG14 Classic"
        if "share tech mono" in families:
            FONT_FAMILY = "Share Tech Mono"
    except Exception:
        pass


# ─── Game Card Widget ───────────────────────────────────────────────


class GameCard(ctk.CTkFrame):
    """A compact card showing one game's rank, score, and improvement target."""

    CARD_HEIGHT = 76
    BOXART_HEIGHT = 64
    BOXART_WIDTH = 64  # will be replaced by actual aspect ratio

    def __init__(self, parent, game_name: str, rank_str: str, score_str: str,
                 next_target: str, gap_str: str, progress: float,
                 boxart_image=None, accent_color: str = AMBER,
                 on_click=None, on_right_click=None, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=8,
                         border_width=2, border_color=BG_CARD,
                         cursor="hand2", height=self.CARD_HEIGHT, **kwargs)

        self._on_click = on_click
        self._on_right_click = on_right_click
        self._selected = False
        self._accent_color = accent_color

        # ── Boxart thumbnail (left) — fixed height, variable width ──
        self._boxart_label = ctk.CTkLabel(
            self, text="", height=self.BOXART_HEIGHT,
            fg_color=BG_DARK, corner_radius=4,
        )
        self._boxart_label.pack(side="left", padx=(6, 8), pady=6)
        if boxart_image:
            self._boxart_label.configure(image=boxart_image, text="")

        # ── Right side: info stacked vertically ──
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=4)

        # Row 1: Game name
        self._name_label = ctk.CTkLabel(
            info, text=game_name, font=(FONT_FAMILY, 12, "bold"),
            text_color=accent_color, anchor="w",
        )
        self._name_label.pack(fill="x")

        # Row 2: Rank + Score
        info_top = ctk.CTkFrame(info, fg_color="transparent")
        info_top.pack(fill="x", pady=(1, 0))

        ctk.CTkLabel(
            info_top, text=f"#{rank_str}", font=(FONT_FAMILY, 11, "bold"),
            text_color=NEON_CYAN, width=60, anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            info_top, text=score_str, font=(FONT_FAMILY, 11),
            text_color=NEON_YELLOW, anchor="w",
        ).pack(side="left")

        if gap_str:
            gap_color = NEON_GREEN if progress >= 0.9 else (
                NEON_ORANGE if progress >= 0.7 else NEON_PINK)
            ctk.CTkLabel(
                info_top, text=gap_str, font=(FONT_FAMILY, 10, "bold"),
                text_color=gap_color, anchor="e",
            ).pack(side="right")

        # Row 3: Target + Progress bar
        if next_target or progress > 0:
            bottom = ctk.CTkFrame(info, fg_color="transparent")
            bottom.pack(fill="x", pady=(2, 0))

            if next_target:
                ctk.CTkLabel(
                    bottom, text=f"→ {next_target}", font=(FONT_FAMILY, 9),
                    text_color=FG_DIM, anchor="w", width=60,
                ).pack(side="left")

            if progress > 0:
                bar_color = NEON_GREEN if progress >= 0.9 else (
                    NEON_ORANGE if progress >= 0.7 else NEON_PINK)
                self._progress = ctk.CTkProgressBar(
                    bottom, height=5, corner_radius=2,
                    fg_color=BG_DARK, progress_color=bar_color,
                )
                self._progress.set(min(progress, 1.0))
                self._progress.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Bind clicks on all child widgets
        self._bind_all(self)

    def _bind_all(self, widget):
        widget.bind("<Button-1>", self._click)
        widget.bind("<Button-3>", self._right_click)
        for child in widget.winfo_children():
            self._bind_all(child)

    def set_boxart(self, image):
        """Update boxart after async load."""
        if image:
            self._boxart_label.configure(image=image, text="")

    def _click(self, event=None):
        if self._on_click:
            self._on_click()

    def _right_click(self, event=None):
        if self._on_right_click and event:
            self._on_right_click(event)

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.configure(fg_color=BG_CARD_SELECTED,
                           border_color=self._accent_color)
        else:
            self.configure(fg_color=BG_CARD, border_color=BG_CARD)

    def on_enter(self, event=None):
        if not self._selected:
            self.configure(fg_color=BG_CARD_HOVER, border_color=BG_HEADER)

    def on_leave(self, event=None):
        if not self._selected:
            self.configure(fg_color=BG_CARD, border_color=BG_CARD)


# ─── Main Application ──────────────────────────────────────────────


class ScoreChaserApp:
    def __init__(self):
        _load_fonts()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
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
        self._image_cache: dict[str, ImageTk.PhotoImage] = {}
        self._thumb_cache: dict[str, ctk.CTkImage] = {}
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "ScoreChaser/1.0"})

        self._token: str | None = None
        self._personal_scores: list[dict] = []
        self._hidden_games: set[str] = set()
        self._load_hidden_games()

        self._selected_game_id: str | None = None
        self._game_cards: dict[str, GameCard] = {}
        self._current_view = "my"  # "my" or "all"

        self._build_ui()
        self._load_token()
        self._load_existing_data()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self):
        self.root.mainloop()

    # ── UI Building ─────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = ctk.CTkFrame(self.root, fg_color=BG_PANEL, height=50, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkLabel(
            top, text="SCORE CHASER", font=(TITLE_FONT_FAMILY, 18),
            text_color=AMBER,
        ).pack(side="left", padx=(16, 24))

        # Device filter
        ctk.CTkLabel(top, text="DEVICE", font=(FONT_FAMILY, 9),
                     text_color=FG_DIM).pack(side="left")
        self._hw_var = tk.StringVar(value="All Devices")
        self._hw_combo = ctk.CTkComboBox(
            top, variable=self._hw_var, values=["All Devices"],
            width=150, font=(FONT_FAMILY, 11), state="readonly",
            fg_color=BG_CARD, border_color=BG_HEADER,
            button_color=AMBER_DIM, button_hover_color=AMBER,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            command=lambda _: self._refresh_list(),
        )
        self._hw_combo.pack(side="left", padx=(4, 16))

        # Login area (right side)
        self._login_btn = ctk.CTkButton(
            top, text="LOGIN", width=80, font=(FONT_FAMILY, 11, "bold"),
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=NEON_ORANGE, command=self._start_login,
        )
        self._login_btn.pack(side="right", padx=(8, 16))

        self._login_label = ctk.CTkLabel(
            top, text="", font=(FONT_FAMILY, 10), text_color=FG_DIM,
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
            left_header, values=["MY GAMES", "ALL GAMES"],
            font=(FONT_FAMILY, 10, "bold"),
            fg_color=BG_DARK, selected_color=AMBER_DIM,
            selected_hover_color=AMBER, unselected_color=BG_CARD,
            unselected_hover_color=BG_CARD_HOVER,
            text_color=FG_DEFAULT, text_color_disabled=FG_DIM,
            command=self._on_view_toggle,
        )
        self._view_toggle.set("MY GAMES")
        self._view_toggle.pack(side="left")

        self._sort_var = tk.StringVar(value="Potential")
        self._sort_combo = ctk.CTkComboBox(
            left_header, variable=self._sort_var,
            values=["Potential", "Rank", "Name", "Score"],
            width=110, font=(FONT_FAMILY, 10), state="readonly",
            fg_color=BG_CARD, border_color=BG_HEADER,
            button_color=AMBER_DIM, button_hover_color=AMBER,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            command=lambda _: self._refresh_list(),
        )
        self._sort_combo.pack(side="right")
        ctk.CTkLabel(left_header, text="Sort:", font=(FONT_FAMILY, 9),
                     text_color=FG_DIM).pack(side="right", padx=(0, 4))

        # Game count label
        self._count_label = ctk.CTkLabel(
            left, text="", font=(FONT_FAMILY, 9), text_color=FG_DIM,
        )
        self._count_label.pack(fill="x", padx=12, pady=(0, 4))

        # Scrollable game list
        self._game_list = ctk.CTkScrollableFrame(
            left, fg_color=BG_PANEL, corner_radius=0,
            scrollbar_button_color=AMBER_DIM,
            scrollbar_button_hover_color=AMBER,
        )
        self._game_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Right panel
        self._detail_panel = ctk.CTkScrollableFrame(
            main, fg_color=BG_PANEL, corner_radius=8,
            scrollbar_button_color=AMBER_DIM,
            scrollbar_button_hover_color=AMBER,
        )
        self._detail_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self._detail_placeholder = ctk.CTkLabel(
            self._detail_panel, text="Select a game",
            font=(TITLE_FONT_FAMILY, 14), text_color=FG_DIM,
        )
        self._detail_placeholder.pack(pady=40)

        # Status bar
        status = ctk.CTkFrame(self.root, fg_color=BG_PANEL, height=36, corner_radius=0)
        status.pack(fill="x", pady=(4, 0))
        status.pack_propagate(False)

        self._status_label = ctk.CTkLabel(
            status, text="No data loaded.", font=(FONT_FAMILY, 9),
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
            font=(FONT_FAMILY, 10, "bold"),
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=NEON_ORANGE, command=self._start_scrape,
        )
        self._refresh_btn.pack(side="right", padx=8)

        self._hidden_btn = ctk.CTkButton(
            status, text="Hidden (0)", width=90, height=26,
            font=(FONT_FAMILY, 10), fg_color=BG_CARD,
            hover_color=BG_CARD_HOVER, text_color=FG_DIM,
            command=self._show_hidden_dialog,
        )

        # Context menu (tk.Menu works fine inside CTk)
        self._ctx_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=FG_DEFAULT,
                                  activebackground=AMBER_DIM,
                                  activeforeground=AMBER_BRIGHT,
                                  font=(FONT_FAMILY, 10))
        self._ctx_menu.add_command(label="Hide game", command=self._ctx_hide_game)
        self._ctx_game_id: str | None = None

        # Detail leaderboard context menu
        self._lb_ctx_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=FG_DEFAULT,
                                     activebackground=AMBER_DIM,
                                     activeforeground=AMBER_BRIGHT,
                                     font=(FONT_FAMILY, 10))
        self._lb_ctx_menu.add_command(label="Search this user",
                                       command=self._ctx_search_user)
        self._lb_ctx_username: str | None = None

    # ── Data Loading ────────────────────────────────────────────

    def _load_existing_data(self):
        data = load_data()
        if data:
            self.data = data
            self._status_label.configure(text=f"{len(data)} games loaded. Refreshing...")
            self._update_hw_options()
            self._refresh_list()
        else:
            self._status_label.configure(text="No data. Loading...")
        self.root.after(100, self._start_scrape)
        self.root.after(200, self._load_tournaments)

    def _load_tournaments(self):
        # Keep tournament data for potential future use
        pass

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
        self._backfill_missing_games()
        self._refresh_list()

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
        self._update_hw_options()
        self._backfill_missing_games()
        self._refresh_list()

    def _on_scrape_error(self, error):
        self._progress_bar.pack_forget()
        self._refresh_btn.configure(state="normal")
        from tkinter import messagebox
        messagebox.showerror("Error", f"Scraping failed:\n{error}")

    # ── Hardware Filter ─────────────────────────────────────────

    def _update_hw_options(self):
        hw_names = set()
        for game in self.data.values():
            for s in game.get("scores", []):
                name = _hw_name(s.get("hardware", ""))
                if name:
                    hw_names.add(name)
        self._hw_combo.configure(values=["All Devices"] + sorted(hw_names))

    def _get_hw_filter(self) -> set[str] | None:
        return _hw_codes_for_filter(self._hw_var.get())

    # ── View Toggle ─────────────────────────────────────────────

    def _on_view_toggle(self, value):
        self._current_view = "my" if value == "MY GAMES" else "all"
        self._refresh_list()

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

    def _compute_target(self, user_score: int, thresholds: dict) -> tuple:
        """Returns (next_target_label, gap_str, progress)."""
        milestones = [
            (100, "Top 100", "top100"),
            (50, "Top 50", "top50"),
            (10, "Top 10", "top10"),
            (1, "#1", "high"),
        ]
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
                return label, f"+{_compact_score(str(gap))}", progress
        # User is #1 or above all thresholds
        return "", "", 1.0

    def _refresh_list(self):
        # Clear existing cards
        for widget in self._game_list.winfo_children():
            widget.destroy()
        self._game_cards.clear()

        hw_filter = self._get_hw_filter()
        personal_map = self._get_personal_map()
        search = get_token_username(self._token).lower() if self._token else ""

        items = []  # (game_id, game, user_entry_or_None, thresholds)

        for gid, game in self.data.items():
            if gid in self._hidden_games:
                continue
            th = _get_thresholds(game["scores"], hw_filter)

            # Find user entry
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

            items.append((gid, game, entry, in_top100, th))

        # Also add personal-only games not in scraped data
        if self._current_view == "my":
            existing_gids = {i[0] for i in items}
            for gid, ps_entry in personal_map.items():
                if gid not in existing_gids and gid not in self._hidden_games:
                    items.append((gid, {"name": ps_entry.get("userName", "Unknown"),
                                        "scores": []}, ps_entry, False,
                                  _get_thresholds([])))

        # Sort
        sort_key = self._sort_var.get()
        if sort_key == "Potential":
            def potential_sort(item):
                gid, game, entry, in_top100, th = item
                if not entry:
                    return (1, 0)
                try:
                    score = int(float(entry.get("score", "0")))
                except (ValueError, TypeError):
                    score = 0
                _, _, progress = self._compute_target(score, th)
                return (0, -progress)  # higher progress = closer to goal = sort first
            items.sort(key=potential_sort)
        elif sort_key == "Rank":
            items.sort(key=lambda x: x[2].get("rank", 9999) if x[2] else 9999)
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

        # Color rotation for game cards — cycling through vivid neon accents
        accent_colors = [NEON_PINK, NEON_CYAN, NEON_GREEN, NEON_ORANGE,
                          AMBER_BRIGHT, NEON_YELLOW]

        # Build cards
        for idx, (gid, game, entry, in_top100, th) in enumerate(items):
            if entry:
                try:
                    user_score = int(float(entry.get("score", "0")))
                except (ValueError, TypeError):
                    user_score = 0
                rank = entry.get("rank", "")
                if not hw_filter and not in_top100:
                    rank_str = "> 100"
                else:
                    rank_str = str(rank)
                score_str = _compact_score(entry.get("score", "0"))
                target_label, gap_str, progress = self._compute_target(user_score, th)
            else:
                rank_str = "—"
                score_str = ""
                target_label = ""
                gap_str = ""
                progress = 0

            boxart_url = game.get("boxart", "") if isinstance(game, dict) else ""
            thumb = self._get_thumbnail(boxart_url)

            accent = accent_colors[idx % len(accent_colors)]

            card = GameCard(
                self._game_list,
                game_name=game["name"] if "name" in game else "Unknown",
                rank_str=rank_str,
                score_str=score_str,
                next_target=target_label,
                gap_str=gap_str,
                progress=progress,
                boxart_image=thumb,
                accent_color=accent,
                on_click=lambda g=gid: self._select_game(g),
                on_right_click=lambda e, g=gid: self._show_card_menu(e, g),
            )
            card.pack(fill="x", padx=6, pady=3)
            card.bind("<Enter>", card.on_enter)
            card.bind("<Leave>", card.on_leave)
            self._game_cards[gid] = card

            # Async-load boxart if not cached
            if boxart_url and thumb is None:
                self._load_thumbnail(boxart_url, card)

        count = len(items)
        total = len(self.data) - len(self._hidden_games)
        if self._current_view == "my":
            self._count_label.configure(text=f"{count} ranked games / {total} total")
        else:
            self._count_label.configure(text=f"{count} games")

        self._update_hidden_btn()

        # Re-select if still valid
        if self._selected_game_id and self._selected_game_id in self._game_cards:
            self._game_cards[self._selected_game_id].set_selected(True)

    # ── Game Selection & Detail ─────────────────────────────────

    def _select_game(self, game_id: str):
        # Deselect old
        if self._selected_game_id and self._selected_game_id in self._game_cards:
            self._game_cards[self._selected_game_id].set_selected(False)

        self._selected_game_id = game_id
        if game_id in self._game_cards:
            self._game_cards[game_id].set_selected(True)

        self._show_detail(game_id)

    def _show_detail(self, game_id: str):
        # Clear detail panel
        for w in self._detail_panel.winfo_children():
            w.destroy()

        game = self.data.get(game_id)
        if not game:
            return

        hw_filter = self._get_hw_filter()
        search = get_token_username(self._token).lower() if self._token else ""
        personal_map = self._get_personal_map()

        # Header: Boxart + Title + User Score
        header = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))

        self._boxart_label = ctk.CTkLabel(header, text="", height=100)
        self._boxart_label.pack(side="left", padx=(0, 12))
        self._load_boxart(game.get("boxart", ""))

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_frame, text=game["name"].upper(),
            font=(TITLE_FONT_FAMILY, 13), text_color=AMBER,
            anchor="w", wraplength=400,
        ).pack(fill="x")

        # User score info
        user_entry = None
        in_top100 = False
        if search:
            scores = game["scores"]
            if hw_filter:
                scores = [s for s in scores if s.get("hardware", "") in hw_filter]
            for s in scores:
                if search == s.get("userName", "").lower():
                    user_entry = s
                    in_top100 = True
                    break
            if not user_entry and game_id in personal_map:
                user_entry = personal_map[game_id]

        if user_entry:
            rank = user_entry.get("rank", "")
            if not hw_filter and not in_top100:
                rank_display = "> 100"
            else:
                rank_display = f"#{rank}"

            ctk.CTkLabel(
                title_frame,
                text=f"Your Score: {_format_score(user_entry.get('score', '0'))}  |  Rank: {rank_display}",
                font=(FONT_FAMILY, 12), text_color=NEON_YELLOW, anchor="w",
            ).pack(fill="x", pady=(4, 0))

        # Separator
        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        # Next Targets section
        th = _get_thresholds(game["scores"], hw_filter)
        if user_entry:
            try:
                user_score = int(float(user_entry.get("score", "0")))
            except (ValueError, TypeError):
                user_score = 0

            ctk.CTkLabel(
                self._detail_panel, text="▸ NEXT TARGETS",
                font=(FONT_FAMILY, 12, "bold"), text_color=NEON_CYAN, anchor="w",
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

                ctk.CTkLabel(row, text=label, font=(FONT_FAMILY, 11),
                             text_color=FG_DIM, width=65, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=_format_score(str(th_score)),
                             font=(FONT_FAMILY, 11), text_color=status_color,
                             width=120, anchor="e").pack(side="left")
                ctk.CTkLabel(row, text=gap_text, font=(FONT_FAMILY, 11),
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
                ctk.CTkLabel(section, text=label, font=(FONT_FAMILY, 12, "bold"),
                             text_color=color, anchor="w").pack(fill="x")
                loading = ctk.CTkLabel(section, text="Loading...",
                                        font=(FONT_FAMILY, 10), text_color=FG_DIM,
                                        anchor="w")
                loading.pack(fill="x")
                self._load_time_scores(internal, period, section, loading)

        # Separator
        ctk.CTkFrame(self._detail_panel, fg_color=NEON_PINK, height=2).pack(
            fill="x", padx=8, pady=6)

        # Leaderboard
        ctk.CTkLabel(
            self._detail_panel, text="▸ LEADERBOARD",
            font=(FONT_FAMILY, 12, "bold"), text_color=GOLD, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 4))

        scores = game["scores"]
        if hw_filter:
            scores = [s for s in scores if s.get("hardware", "") in hw_filter]

        # Show top 20 + user if outside
        user_shown = False
        display_scores = scores[:20]

        for i, s in enumerate(display_scores):
            rank = i + 1 if hw_filter else s.get("rank", i + 1)
            is_user = search and search == s.get("userName", "").lower()
            if is_user:
                user_shown = True

            row = ctk.CTkFrame(self._detail_panel, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=0)

            fg = NEON_YELLOW if is_user else (
                GOLD if rank == 1 else (
                    NEON_GREEN if rank <= 10 else FG_DEFAULT))

            ctk.CTkLabel(row, text=f"#{rank}", font=(FONT_FAMILY, 10),
                         text_color=fg, width=40, anchor="e").pack(side="left")
            ctk.CTkLabel(row, text=s.get("userName", ""), font=(FONT_FAMILY, 10),
                         text_color=fg, width=130, anchor="w").pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=_format_score(s.get("score", "0")),
                         font=(FONT_FAMILY, 10), text_color=fg,
                         anchor="e").pack(side="left", padx=(4, 0))
            ctk.CTkLabel(row, text=_hw_name(s.get("hardware", "")),
                         font=(FONT_FAMILY, 9), text_color=FG_DIM,
                         width=80, anchor="e").pack(side="right")

            # Right-click binding
            for w in [row] + list(row.winfo_children()):
                w.bind("<Button-3>", lambda e, u=s.get("userName", ""): self._show_lb_menu(e, u))

        # Append user if not shown
        if search and not user_shown and user_entry:
            ctk.CTkLabel(self._detail_panel, text="···",
                         font=(FONT_FAMILY, 10), text_color=FG_DIM).pack(pady=2)
            row = ctk.CTkFrame(self._detail_panel, fg_color=HIGHLIGHT_BG,
                               corner_radius=4)
            row.pack(fill="x", padx=12, pady=2)

            rank_display = "> 100" if not hw_filter and not in_top100 else f"#{user_entry.get('rank', '')}"
            ctk.CTkLabel(row, text=rank_display, font=(FONT_FAMILY, 10, "bold"),
                         text_color=NEON_YELLOW, width=50, anchor="e").pack(side="left", padx=(4, 0))
            ctk.CTkLabel(row, text=user_entry.get("userName", ""),
                         font=(FONT_FAMILY, 10, "bold"), text_color=NEON_YELLOW,
                         width=130, anchor="w").pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=_format_score(user_entry.get("score", "0")),
                         font=(FONT_FAMILY, 10, "bold"), text_color=NEON_YELLOW,
                         anchor="e").pack(side="left", padx=(4, 0))

    def _load_time_scores(self, internal_number, period, parent, loading_label):
        def do_fetch():
            try:
                scores = fetch_scores(internal_number, time_range=period)
                self.root.after(0, lambda: self._display_time_scores(
                    scores, parent, loading_label))
            except Exception:
                self.root.after(0, lambda: loading_label.configure(text="—"))

        threading.Thread(target=do_fetch, daemon=True).start()

    def _display_time_scores(self, scores, parent, loading_label):
        loading_label.destroy()
        if not scores:
            ctk.CTkLabel(parent, text="No scores", font=(FONT_FAMILY, 10),
                         text_color=FG_DIM, anchor="w").pack(fill="x")
            return
        for s in scores[:3]:
            text = f"#{s.get('rank', '?')}  {s.get('userName', '')}  {_format_score(s.get('score', '0'))}"
            ctk.CTkLabel(parent, text=text, font=(FONT_FAMILY, 10),
                         text_color=FG_DEFAULT, anchor="w").pack(fill="x")

    # ── Thumbnail (for game cards) ──────────────────────────────

    def _get_thumbnail(self, url: str):
        """Return cached thumbnail CTkImage or None if not loaded yet."""
        if not url:
            return None
        return self._thumb_cache.get(url)

    def _load_thumbnail(self, url: str, card: GameCard):
        """Load thumbnail async and update the card."""
        if url in self._thumb_cache:
            card.set_boxart(self._thumb_cache[url])
            return

        def fetch():
            try:
                resp = self._http.get(url, timeout=8)
                resp.raise_for_status()
                pil = Image.open(io.BytesIO(resp.content))
                # Scale to fixed height, preserve aspect ratio
                h = GameCard.BOXART_HEIGHT
                w = int(pil.width * h / pil.height) if pil.height else h
                pil = pil.resize((w, h), Image.LANCZOS)
                self.root.after(0, lambda: self._set_thumbnail(url, pil, card, w, h))
            except Exception:
                pass

        threading.Thread(target=fetch, daemon=True).start()

    def _set_thumbnail(self, url: str, pil_img, card, w, h):
        try:
            ctk_img = ctk.CTkImage(pil_img, size=(w, h))
            self._thumb_cache[url] = ctk_img
            try:
                card.set_boxart(ctk_img)
            except Exception:
                pass
        except Exception:
            pass

    # ── Boxart ──────────────────────────────────────────────────

    def _load_boxart(self, url: str):
        if not url:
            return
        if url in self._image_cache:
            self._boxart_label.configure(image=self._image_cache[url])
            return

        def fetch():
            try:
                resp = self._http.get(url, timeout=8)
                resp.raise_for_status()
                pil = Image.open(io.BytesIO(resp.content))
                # Preserve aspect ratio, fixed height
                h = 100
                w = int(pil.width * h / pil.height) if pil.height else h
                pil = pil.resize((w, h), Image.LANCZOS)
                self.root.after(0, lambda: self._set_boxart(url, pil, w, h))
            except Exception:
                pass

        threading.Thread(target=fetch, daemon=True).start()

    def _set_boxart(self, url, pil_img, w=100, h=100):
        try:
            ctk_img = ctk.CTkImage(pil_img, size=(w, h))
            self._image_cache[url] = ctk_img
            self._boxart_label.configure(image=ctk_img)
        except Exception:
            pass

    # ── Context Menus ───────────────────────────────────────────

    def _show_card_menu(self, event, game_id):
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

        ctk.CTkLabel(dlg, text="HIDDEN GAMES", font=(FONT_FAMILY, 12, "bold"),
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
            ctk.CTkLabel(row, text=name, font=(FONT_FAMILY, 10),
                         text_color=FG_DEFAULT, anchor="w").pack(
                side="left", padx=8, pady=4)
            ctk.CTkButton(
                row, text="Unhide", width=60, height=24,
                font=(FONT_FAMILY, 9), fg_color=BG_HEADER,
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
                       font=(FONT_FAMILY, 10, "bold"),
                       fg_color=BG_CARD, hover_color=AMBER_DIM,
                       text_color=NEON_ORANGE,
                       command=lambda: (self._hidden_games.clear(),
                                        self._save_hidden_games(),
                                        dlg.destroy(),
                                        self._refresh_list())).pack(side="left")
        ctk.CTkButton(btn_frame, text="CLOSE", width=80,
                       font=(FONT_FAMILY, 10),
                       fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
                       text_color=FG_DIM,
                       command=lambda: (dlg.destroy(),
                                        self._refresh_list())).pack(side="right")

    # ── Cleanup ─────────────────────────────────────────────────

    def _on_close(self):
        self.root.destroy()


def main():
    app = ScoreChaserApp()
    app.run()


if __name__ == "__main__":
    main()
