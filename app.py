"""PinballScores - ATGames Leaderboard Viewer."""

import io
import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import requests
from PIL import Image, ImageTk

from scraper import (load_data, scrape_all, save_data, _APP_DIR,
                     fetch_tournaments, fetch_tournament_scores,
                     load_settings, save_settings, login_via_browser,
                     is_token_valid, get_token_username,
                     fetch_personal_scores)

SETTINGS_FILE = _APP_DIR / "data" / "settings.json"

# -- Color Scheme (Pinball / Arcade) --
BG_DARK = "#0a0a1a"
BG_PANEL = "#12122b"
BG_WIDGET = "#1a1a3e"
BG_HEADER = "#252550"
FG_DEFAULT = "#d0d0e0"
FG_DIM = "#707090"
NEON_PINK = "#ff2d78"
NEON_CYAN = "#00e5ff"
NEON_GREEN = "#00ff41"
NEON_YELLOW = "#ffe600"
NEON_ORANGE = "#ff9100"
GOLD = "#ffd700"
HIGHLIGHT_BG = "#3a2a00"
TOP10_BG = "#1a2e1a"
TOP50_BG = "#141e30"

FONT_FAMILY = "Ubuntu Sans Mono"
def _format_score(score_str: str) -> str:
    try:
        return f"{int(score_str):,}".replace(",", ".")
    except (ValueError, TypeError):
        return score_str


def _get_thresholds(scores: list[dict]) -> dict:
    """Extract score thresholds from a scores list."""
    by_rank = {s["rank"]: s["score"] for s in scores if s.get("rank")}
    return {
        "top100": by_rank.get(100, ""),
        "top50": by_rank.get(50, ""),
        "top10": by_rank.get(10, ""),
        "high": by_rank.get(1, ""),
    }


SEL_BG = "#252560"
ROW_EVEN = BG_PANEL
ROW_ODD = "#0f0f25"


class ColorTable:
    """Table widget with per-column foreground colors using synced treeviews."""

    def __init__(self, parent, col_defs):
        """Create a color table.

        col_defs: list of (col_id, header_text, width, anchor, stretch, fg_color)
                  or (col_id, header_text, width, anchor, stretch, fg_color, font)
        """
        self._trees: list[ttk.Treeview] = []
        self._col_frames: list[tk.Frame] = []
        self._col_defs = col_defs
        self._row_count = 0
        self._sel_idx = -1
        self._select_callbacks: list = []
        self._values: list[tuple] = []
        self._row_tags: list[list[str]] = []

        # Scrollbar (right side of everything)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Body frame — each column is a vertical frame with header + treeview
        self._body = tk.Frame(parent, bg=BG_PANEL)
        self._body.pack(fill=tk.BOTH, expand=True)

        # Measure font for auto-sizing
        import tkinter.font as tkfont
        self._measure_font = tkfont.Font(family=FONT_FAMILY, size=10)
        self._header_font = tkfont.Font(family=FONT_FAMILY, size=9, weight="bold")

        for col_def in col_defs:
            _, text, width, anchor, stretch, fg = col_def[:6]
            font = col_def[6] if len(col_def) > 6 else None

            # --- Column container (header + tree stacked vertically) ---
            col_frame = tk.Frame(self._body, width=width, bg=BG_PANEL)
            if stretch:
                col_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                col_frame.pack(side=tk.LEFT, fill=tk.Y)
                col_frame.pack_propagate(False)
            self._col_frames.append(col_frame)

            # Header label at top of column
            tk.Label(col_frame, text=text, fg=fg, bg=BG_HEADER, height=1,
                     font=(FONT_FAMILY, 9, "bold")).pack(fill=tk.X)

            # Treeview below header
            t = ttk.Treeview(col_frame, columns=("v",), show="", selectmode="none",
                             padding=0, style="Borderless.Treeview")
            t.column("#0", width=0, stretch=False)
            t.column("v", width=width, anchor=anchor, stretch=stretch)

            tag_kw = {"foreground": fg}
            if font:
                tag_kw["font"] = font
            t.tag_configure("even", background=ROW_EVEN, **tag_kw)
            t.tag_configure("odd", background=ROW_ODD, **tag_kw)
            t.tag_configure("even_sel", background=SEL_BG, **tag_kw)
            t.tag_configure("odd_sel", background=SEL_BG, **tag_kw)

            t.pack(fill=tk.BOTH, expand=True)

            t.bind("<Button-1>", self._on_click)
            t.bind("<Button-4>", self._on_scroll_up)
            t.bind("<Button-5>", self._on_scroll_down)
            t.bind("<MouseWheel>", self._on_mousewheel)
            self._trees.append(t)

        # Connect scrollbar to first tree, sync rest
        self._scroll = scroll
        scroll.configure(command=self._scroll_all)
        self._trees[0].configure(yscrollcommand=self._on_yscroll)

    def auto_resize(self, padding: int = 16):
        """Resize non-stretch columns to fit content, stretch columns fill rest."""
        for i, col_def in enumerate(self._col_defs):
            stretch = col_def[4]
            if stretch:
                continue

            header_text = col_def[1]
            # Measure header width
            max_w = self._header_font.measure(header_text)

            # Measure all data values
            for row in self._values:
                val = str(row[i]) if i < len(row) else ""
                w = self._measure_font.measure(val)
                if w > max_w:
                    max_w = w

            new_width = max_w + padding
            self._col_frames[i].configure(width=new_width)
            self._trees[i].column("v", width=new_width)

    def _scroll_all(self, *args):
        for t in self._trees:
            t.yview(*args)

    def _on_scroll_up(self, _event):
        self._scroll_all("scroll", -3, "units")
        return "break"

    def _on_scroll_down(self, _event):
        self._scroll_all("scroll", 3, "units")
        return "break"

    def _on_mousewheel(self, event):
        self._scroll_all("scroll", -1 * (event.delta // 120), "units")
        return "break"

    def _on_yscroll(self, first, last):
        self._scroll.set(first, last)
        for t in self._trees[1:]:
            t.yview_moveto(first)

    def _on_click(self, event):
        iid = event.widget.identify_row(event.y)
        if not iid:
            return
        idx = event.widget.index(iid)
        self._set_selection(idx)
        for cb in self._select_callbacks:
            cb()

    def _set_selection(self, idx):
        # Deselect previous — restore original per-column tags
        if 0 <= self._sel_idx < self._row_count:
            orig_tags = self._row_tags[self._sel_idx]
            for i, t in enumerate(self._trees):
                t.item(t.get_children()[self._sel_idx], tags=(orig_tags[i],))

        self._sel_idx = idx

        # Select new — use _sel variant of each column's tag
        if 0 <= idx < self._row_count:
            orig_tags = self._row_tags[idx]
            for i, t in enumerate(self._trees):
                items = t.get_children()
                t.item(items[idx], tags=(f"{orig_tags[i]}_sel",))
                t.see(items[idx])

    def configure_column_tag(self, col_idx: int, tag_name: str, **kw):
        """Configure a tag on a specific column's treeview.
        Also register a _sel variant with the same foreground but selection background."""
        if 0 <= col_idx < len(self._trees):
            self._trees[col_idx].tag_configure(tag_name, **kw)
            # Create a selection variant preserving foreground
            sel_kw = {k: v for k, v in kw.items() if k == "foreground"}
            sel_kw["background"] = SEL_BG
            self._trees[col_idx].tag_configure(f"{tag_name}_sel", **sel_kw)

    def insert(self, values, col_tags: dict[int, str] | None = None):
        """Insert a row. col_tags: optional {col_index: tag_name} overrides."""
        base_tag = "even" if self._row_count % 2 == 0 else "odd"
        row_tags = []
        for i, t in enumerate(self._trees):
            val = values[i] if i < len(values) else ""
            tag = col_tags.get(i, base_tag) if col_tags else base_tag
            t.insert("", tk.END, values=(val,), tags=(tag,))
            row_tags.append(tag)
        self._values.append(values)
        self._row_tags.append(row_tags)
        self._row_count += 1

    def delete_all(self):
        for t in self._trees:
            t.delete(*t.get_children())
        self._values.clear()
        self._row_tags.clear()
        self._row_count = 0
        self._sel_idx = -1

    def selection_index(self) -> int:
        return self._sel_idx

    def bind_select(self, callback):
        self._select_callbacks.append(callback)


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


def _hw_name(code: str) -> str:
    return HARDWARE_NAMES.get(code, code)


def _apply_theme(root: tk.Tk):
    """Apply dark pinball arcade theme to ttk widgets."""
    style = ttk.Style(root)
    style.theme_use("clam")

    # General
    style.configure(".", background=BG_DARK, foreground=FG_DEFAULT,
                     fieldbackground=BG_WIDGET, borderwidth=0,
                     font=(FONT_FAMILY, 10))

    # Frames
    style.configure("TFrame", background=BG_DARK)
    style.configure("TLabel", background=BG_DARK, foreground=FG_DEFAULT,
                     font=(FONT_FAMILY, 10))
    style.configure("Title.TLabel", foreground=NEON_PINK,
                     font=(FONT_FAMILY, 13, "bold"))
    style.configure("Status.TLabel", foreground=FG_DIM, font=(FONT_FAMILY, 9))

    # Entry
    style.configure("TEntry", fieldbackground=BG_WIDGET, foreground=NEON_CYAN,
                     insertcolor=NEON_CYAN, font=(FONT_FAMILY, 11))
    style.map("TEntry",
              fieldbackground=[("focus", "#1e1e4a")],
              foreground=[("focus", NEON_CYAN)])

    # Button
    style.configure("TButton", background=BG_WIDGET, foreground=NEON_PINK,
                     font=(FONT_FAMILY, 10, "bold"), padding=(12, 6),
                     borderwidth=1, relief="raised")
    style.map("TButton",
              background=[("active", "#2a2a5e"), ("pressed", "#1a1a3e")],
              foreground=[("active", NEON_YELLOW), ("disabled", FG_DIM)])

    # Notebook (tabs)
    style.configure("TNotebook", background=BG_DARK, borderwidth=0)
    style.configure("TNotebook.Tab", background=BG_WIDGET, foreground=FG_DIM,
                     font=(FONT_FAMILY, 10, "bold"), padding=(16, 6),
                     borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", BG_HEADER)],
              foreground=[("selected", NEON_CYAN)])

    # Treeview
    style.configure("Treeview",
                     background=BG_PANEL,
                     foreground=FG_DEFAULT,
                     fieldbackground=BG_PANEL,
                     rowheight=26,
                     font=(FONT_FAMILY, 10),
                     borderwidth=0,
                     relief="flat")

    # Borderless variant for ColorTable sub-treeviews (no surrounding frame)
    style.layout("Borderless.Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"}),
    ])

    style.configure("Treeview.Heading",
                     background=BG_HEADER,
                     foreground=NEON_PINK,
                     font=(FONT_FAMILY, 9, "bold"),
                     borderwidth=1, relief="flat")
    style.map("Treeview.Heading",
              background=[("active", "#303068")])
    style.map("Treeview",
              background=[("selected", "#252560")],
              foreground=[("selected", NEON_CYAN)])

    # Scrollbar
    style.configure("Vertical.TScrollbar",
                     background=BG_WIDGET, troughcolor=BG_DARK,
                     arrowcolor=FG_DIM, borderwidth=0)
    style.map("Vertical.TScrollbar",
              background=[("active", "#2a2a5e")])

    # Progressbar
    style.configure("TProgressbar",
                     background=NEON_GREEN, troughcolor=BG_WIDGET,
                     borderwidth=1, lightcolor="#303060", darkcolor="#303060",
                     thickness=18)

    # PanedWindow
    style.configure("TPanedwindow", background=BG_DARK)


class PinballScoresApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PinballScores - ATGames Leaderboards")
        self.root.geometry("1400x750")
        self.root.minsize(1000, 500)
        self.root.configure(bg=BG_DARK)

        _apply_theme(root)

        self.data: dict = {}
        self._image_cache: dict[str, ImageTk.PhotoImage] = {}
        self._http_session = requests.Session()
        self._http_session.headers.update({"User-Agent": "PinballScores/1.0"})

        self._token: str | None = None
        self._personal_scores: list[dict] = []

        self._build_ui()
        self._load_saved_username()
        self._load_token()
        self._load_existing_data()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="USERNAME", style="Title.TLabel").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search())
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=25)
        search_entry.pack(side=tk.LEFT, padx=(8, 16))
        search_entry.focus()

        # ArcadeNet login button (right side of top bar)
        self.login_btn = ttk.Button(top, text="LOGIN", command=self._start_login)
        self.login_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.login_status = tk.Label(top, text="", fg=FG_DIM, bg=BG_DARK,
                                      font=(FONT_FAMILY, 9))
        self.login_status.pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="No data loaded.")

        # Permanent status bar at bottom (always visible)
        self.statusbar = tk.Frame(self.root, bg=BG_PANEL, padx=8, pady=4)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        self.scrape_btn = ttk.Button(self.statusbar, text="REFRESH", command=self._start_scrape)
        self.scrape_btn.pack(side=tk.RIGHT)

        self.status_label = tk.Label(
            self.statusbar, textvariable=self.status_var,
            fg=FG_DIM, bg=BG_PANEL, font=(FONT_FAMILY, 9), anchor="w",
        )
        self.status_label.pack(side=tk.LEFT)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.statusbar, variable=self.progress_var, maximum=100
        )
        self.progress_label = tk.Label(
            self.statusbar, text="", fg=NEON_GREEN, bg=BG_PANEL,
            font=(FONT_FAMILY, 9),
        )
        # Progress widgets start hidden, shown during scraping

        # Main area with PanedWindow
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Left: tabs with ranked / unranked tables
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Score column color definitions (with LCD font)
        score_col_defs = [
            ("high",  "HIGHSCORE", 110, GOLD),
            ("top10", "TOP 10",    110, NEON_GREEN),
            ("top50", "TOP 50",    110, NEON_CYAN),
            ("top100","TOP 100",   110, NEON_ORANGE),
        ]

        # --- Tab 0: All Games (always visible, rebuilt on search change) ---
        self.allgames_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.allgames_frame, text=" ALL TABLES ")
        self.allgames_table: ColorTable | None = None
        self._allgames_has_user_cols = False

        # --- Tab 1: User Rankings (added/removed dynamically) ---
        self.ranked_frame = ttk.Frame(self.notebook)

        self.ranked_table = ColorTable(self.ranked_frame, [
            ("game",  "TABLE",       200, "w",      False, NEON_PINK),
            ("rank",  "RANK",        50, "center", False, NEON_PINK),
            ("date",  "DATE",        85, "center", False, NEON_PINK),
            ("score", "USER SCORE", 105, "center", True, NEON_YELLOW),
            ("high",  "HIGHSCORE",  105, "center", True, GOLD),
            ("top10", "TOP 10",     105, "center", True, NEON_GREEN),
            ("top50", "TOP 50",     105, "center", True, NEON_CYAN),
        ])
        self.ranked_table.bind_select(lambda: self._on_left_select("ranked"))

        # --- Tab 2: User Unranked Games (added/removed dynamically) ---
        self.unranked_frame = ttk.Frame(self.notebook)

        self.unranked_table = ColorTable(self.unranked_frame, [
            ("game", "TABLE", 230, "w", False, NEON_PINK),
            *[(c, t, w, "center", True, fg) for c, t, w, fg in score_col_defs],
        ])
        self.unranked_table.bind_select(lambda: self._on_left_select("unranked"))

        self._user_tabs_visible = False

        # --- Tab 3: Tournaments (always visible, drill-down) ---
        self.tournament_outer = ttk.Frame(self.notebook)
        self.notebook.add(self.tournament_outer, text=" TOURNAMENTS ")

        # Tournament header bar (hidden initially, shows name + dates + back)
        self.tournament_back_frame = tk.Frame(self.tournament_outer, bg=BG_HEADER,
                                               padx=6, pady=4)
        self.tournament_back_btn = tk.Button(
            self.tournament_back_frame, text="←",
            fg=NEON_CYAN, bg=BG_WIDGET, activeforeground=NEON_YELLOW,
            activebackground="#2a2a5e", font=(FONT_FAMILY, 11, "bold"),
            bd=0, padx=6, pady=0, cursor="hand2",
            command=self._tournament_go_back,
        )
        self.tournament_back_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.tournament_info_label = tk.Label(
            self.tournament_back_frame, text="", fg=NEON_PINK, bg=BG_HEADER,
            font=(FONT_FAMILY, 10, "bold"), anchor="w", cursor="hand2",
        )
        self.tournament_info_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.tournament_info_label.bind("<Button-1>", lambda _: self._on_tournament_header_click())

        # Container for swapping between list and game views
        self.tournament_container = ttk.Frame(self.tournament_outer)
        self.tournament_container.pack(fill=tk.BOTH, expand=True)

        # Tournament list view (rebuilt on search change)
        self.tournament_list_frame = ttk.Frame(self.tournament_container)
        self.tournament_list_frame.pack(fill=tk.BOTH, expand=True)
        self.tournament_table: ColorTable | None = None
        self._tournament_list_has_user = False

        # Tournament games view (created on demand)
        self.tournament_games_frame = ttk.Frame(self.tournament_container)
        self.tournament_games_table: ColorTable | None = None
        self._tournament_game_ids: list[int] = []  # game_ids in current view
        self._tournament_game_scores: list[dict] = []  # scores per game

        self._tournaments: list[dict] = []
        self._tournament_scores_cache: dict[int, list[dict]] = {}
        self._current_tournament: dict | None = None

        # Right: boxart + full top 100 detail
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        # Header row: boxart image + game title
        header = ttk.Frame(right)
        header.pack(fill=tk.X, pady=(4, 4))

        self.boxart_label = tk.Label(header, bg=BG_DARK, width=80, height=80)
        self.boxart_label.pack(side=tk.LEFT, padx=(0, 8))
        self._boxart_placeholder = None  # will hold a blank image

        self.detail_label = ttk.Label(header, text="SELECT A TABLE", style="Title.TLabel")
        self.detail_label.pack(side=tk.LEFT, anchor=tk.W)

        detail_cols = ("rank", "userName", "initials", "score", "hardware", "date")
        self.detail_tree = self._create_treeview(right, detail_cols)
        self.detail_tree.heading("rank", text="#")
        self.detail_tree.heading("userName", text="USER")
        self.detail_tree.heading("initials", text="INI")
        self.detail_tree.heading("score", text="SCORE")
        self.detail_tree.heading("hardware", text="HW")
        self.detail_tree.heading("date", text="DATE")

        self.detail_tree.column("rank", width=35, minwidth=35, stretch=False, anchor="center")
        self.detail_tree.column("userName", width=115, minwidth=80, stretch=True, anchor="center")
        self.detail_tree.column("initials", width=40, minwidth=40, stretch=False, anchor="center")
        self.detail_tree.column("score", width=110, minwidth=80, stretch=False, anchor="center")
        self.detail_tree.column("hardware", width=85, minwidth=65, stretch=False, anchor="center")
        self.detail_tree.column("date", width=82, minwidth=72, stretch=False, anchor="center")

        self.detail_tree.tag_configure("highlight", background=HIGHLIGHT_BG, foreground=NEON_YELLOW)
        self.detail_tree.tag_configure("top10", background=TOP10_BG, foreground=NEON_GREEN)
        self.detail_tree.tag_configure("top50", background=TOP50_BG, foreground=NEON_CYAN)
        self.detail_tree.tag_configure("rank1", background="#2a1a00", foreground=GOLD)
        self.detail_tree.tag_configure("game_sep", background=BG_HEADER, foreground=NEON_PINK)

        # Right-click context menu on detail tree
        self._ctx_menu = tk.Menu(self.root, tearoff=0, bg=BG_WIDGET, fg=FG_DEFAULT,
                                  activebackground=NEON_PINK, activeforeground="white",
                                  font=(FONT_FAMILY, 10))
        self._ctx_menu.add_command(label="Search this user", command=self._ctx_search_user)
        self.detail_tree.bind("<Button-3>", self._on_detail_right_click)

        # Store game_id mapping for left-side trees
        self._allgames_game_ids: list[str] = []
        self._ranked_game_ids: list[str] = []
        self._unranked_game_ids: list[str] = []


    @staticmethod
    def _create_treeview(parent: ttk.Frame, columns: tuple) -> ttk.Treeview:
        """Create a themed treeview with scrollbar."""
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        return tree


    def _on_detail_right_click(self, event):
        item = self.detail_tree.identify_row(event.y)
        if not item:
            return
        self.detail_tree.selection_set(item)
        self._ctx_menu.post(event.x_root, event.y_root)

    def _ctx_search_user(self):
        sel = self.detail_tree.selection()
        if not sel:
            return
        values = self.detail_tree.item(sel[0], "values")
        username = values[1]  # userName column
        if username:
            self.search_var.set(username)

    def _load_saved_username(self):
        try:
            settings = load_settings()
            name = settings.get("username", "")
            if name:
                self.search_var.set(name)
        except Exception:
            pass

    def _save_username(self):
        settings = load_settings()
        settings["username"] = self.search_var.get().strip()
        save_settings(settings)

    def _load_token(self):
        """Load stored token and update UI."""
        settings = load_settings()
        token = settings.get("token")
        if is_token_valid(token):
            self._token = token
            username = get_token_username(token)
            self.login_btn.config(text="LOGOUT")
            self.login_status.config(text=f"✓ {username}", fg=NEON_GREEN)
            self.root.after(200, self._fetch_personal_scores)
        else:
            self._token = None
            self.login_btn.config(text="LOGIN")
            self.login_status.config(text="", fg=FG_DIM)

    def _save_token(self, token: str | None):
        """Save token to settings."""
        settings = load_settings()
        if token:
            settings["token"] = token
        else:
            settings.pop("token", None)
        save_settings(settings)

    def _start_login(self):
        """Handle login/logout button click."""
        if self._token:
            # Logout
            self._token = None
            self._personal_scores.clear()
            self._save_token(None)
            self.login_btn.config(text="LOGIN")
            self.login_status.config(text="", fg=FG_DIM)
            self._on_search()  # Refresh tabs
            return

        self.login_btn.config(state=tk.DISABLED)
        self.login_status.config(text="Logging in...", fg=NEON_YELLOW)

        def do_login():
            token, error = login_via_browser()
            self.root.after(0, lambda: self._on_login_done(token, error))

        threading.Thread(target=do_login, daemon=True).start()

    def _on_login_done(self, token: str | None, error: str | None):
        """Called when login browser window closes."""
        self.login_btn.config(state=tk.NORMAL)
        if token and is_token_valid(token):
            self._token = token
            self._save_token(token)
            username = get_token_username(token)
            self.login_btn.config(text="LOGOUT")
            self.login_status.config(text=f"✓ {username}", fg=NEON_GREEN)
            self._fetch_personal_scores()
        elif error:
            self.login_status.config(text="Login failed", fg=NEON_PINK)
            messagebox.showerror("Login Error", error)
        else:
            self.login_status.config(text="", fg=FG_DIM)

    def _fetch_personal_scores(self):
        """Fetch personal scores in background."""
        if not is_token_valid(self._token):
            return

        def do_fetch():
            try:
                scores = fetch_personal_scores(self._token)
                self.root.after(0, lambda: self._on_personal_scores(scores))
            except Exception:
                pass

        threading.Thread(target=do_fetch, daemon=True).start()

    def _on_personal_scores(self, scores: list[dict]):
        """Called when personal scores are fetched."""
        self._personal_scores = scores
        self._on_search()  # Refresh tabs to show personal data

    def _on_close(self):
        self._save_username()
        self.root.destroy()

    def _load_existing_data(self):
        data = load_data()
        if data:
            self.data = data
            self._populate_tabs(self.search_var.get().strip().lower())
            self.status_var.set(f"{len(data)} games loaded. Refreshing...")
        else:
            self.status_var.set("No data. Loading...")
        # Auto-refresh on startup
        self.root.after(100, self._start_scrape)
        self.root.after(200, self._load_tournaments)

    def _on_search(self):
        search = self.search_var.get().strip().lower()
        self._populate_tabs(search)
        if self._tournaments:
            self._populate_tournaments(self._tournaments)
        self.detail_tree.delete(*self.detail_tree.get_children())
        self.detail_label.config(text="SELECT A TABLE")
        self.boxart_label.config(image="", width=0)

    def _rebuild_allgames_table(self, with_user: bool):
        """Rebuild the All Games table with or without user columns."""
        if self.allgames_table is not None and self._allgames_has_user_cols == with_user:
            return  # no change needed
        # Destroy old widgets inside the frame
        for w in self.allgames_frame.winfo_children():
            w.destroy()

        score_col_defs = [
            ("high",  "HIGHSCORE", 110, GOLD),
            ("top10", "TOP 10",    110, NEON_GREEN),
            ("top50", "TOP 50",    110, NEON_CYAN),
            ("top100","TOP 100",   110, NEON_ORANGE),
        ]

        cols = [("game", "TABLE", 200 if with_user else 230, "w", False, NEON_PINK)]
        if with_user:
            cols.append(("rank", "RANK", 65, "center", False, NEON_PINK))
            cols.append(("uscore", "USER SCORE", 105, "center", True, NEON_YELLOW))
        cols.extend([(c, t, w, "center", True, fg) for c, t, w, fg in score_col_defs])

        self.allgames_table = ColorTable(self.allgames_frame, cols)
        self.allgames_table.bind_select(lambda: self._on_left_select("allgames"))
        self._allgames_has_user_cols = with_user

    def _add_user_tabs(self):
        if not self._user_tabs_visible:
            self.notebook.add(self.ranked_frame)
            self.notebook.add(self.unranked_frame)
            self._user_tabs_visible = True

    def _remove_user_tabs(self):
        if self._user_tabs_visible:
            self.notebook.forget(self.ranked_frame)
            self.notebook.forget(self.unranked_frame)
            self._user_tabs_visible = False

    def _get_personal_map(self, search: str) -> dict[str, dict]:
        """Build a map of game_id -> personal score entry from personal API data.

        Only returns entries for the searched user.
        """
        pmap: dict[str, dict] = {}
        if not self._personal_scores:
            return pmap
        token_user = get_token_username(self._token) if self._token else None
        if not token_user or search != token_user.lower():
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

    def _populate_tabs(self, search: str = ""):
        has_search = bool(search and self.data)

        # Rebuild All Games table if user-column state changed
        self._rebuild_allgames_table(with_user=has_search)
        self.allgames_table.delete_all()
        self._allgames_game_ids.clear()

        # Personal scores from API (includes ranks beyond top 100)
        personal_map = self._get_personal_map(search) if has_search else {}

        # Pre-compute user entries for all games (needed for All Games + user tabs)
        user_map: dict[str, dict | None] = {}  # game_id -> user score entry or None
        if has_search:
            for game_id, game in self.data.items():
                # First check leaderboard data (top 100)
                entry = None
                for s in game["scores"]:
                    if search == s.get("userName", "").lower():
                        entry = s
                        break
                # If not in top 100, check personal API data
                if entry is None and game_id in personal_map:
                    entry = personal_map[game_id]
                user_map[game_id] = entry

        sorted_games = sorted(self.data.items(), key=lambda x: x[1]["name"].lower())
        for game_id, game in sorted_games:
            th = _get_thresholds(game["scores"])
            if has_search:
                entry = user_map.get(game_id)
                rank_str = str(entry["rank"]) if entry else "unranked"
                score_str = _format_score(entry["score"]) if entry else ""
                self.allgames_table.insert((
                    game["name"], rank_str, score_str,
                    _format_score(th["high"]),
                    _format_score(th["top10"]),
                    _format_score(th["top50"]),
                    _format_score(th["top100"]),
                ))
            else:
                self.allgames_table.insert((
                    game["name"],
                    _format_score(th["high"]),
                    _format_score(th["top10"]),
                    _format_score(th["top50"]),
                    _format_score(th["top100"]),
                ))
            self._allgames_game_ids.append(game_id)

        self.allgames_table.auto_resize()
        self.notebook.tab(0, text=f" ALL TABLES ({len(self.data)}) ")

        if not has_search:
            self._remove_user_tabs()
            self.notebook.select(0)
            return

        # Build ranked / unranked lists for user tabs
        username = self.search_var.get().strip()
        ranked = []
        unranked = []

        for game_id, game in self.data.items():
            th = _get_thresholds(game["scores"])
            entry = user_map.get(game_id)
            if entry:
                ranked.append((game_id, game, entry, th))
            else:
                unranked.append((game_id, game, th))

        # Also add personal scores for games not in the scraped data
        for gid, ps_entry in personal_map.items():
            if gid not in self.data and gid not in {r[0] for r in ranked}:
                # Create a minimal game dict for games only in personal data
                ps_raw = next((p for p in self._personal_scores
                               if str(p.get("game_id", "")) == gid), None)
                if ps_raw:
                    game = {
                        "name": ps_raw.get("name", "Unknown"),
                        "game_id": ps_raw.get("game_id"),
                        "internal_number": ps_raw.get("internal_number", ""),
                        "boxart": ps_raw.get("boxart_480w") or ps_raw.get("boxart", ""),
                        "scores": [],
                    }
                    ranked.append((gid, game, ps_entry, _get_thresholds([])))

        # Only show user tabs if there's at least one match
        if not ranked:
            self._remove_user_tabs()
            self.notebook.select(0)
            return

        self._add_user_tabs()

        self.ranked_table.delete_all()
        self.unranked_table.delete_all()
        self._ranked_game_ids.clear()
        self._unranked_game_ids.clear()

        ranked.sort(key=lambda x: (x[2].get("rank", 999)))
        for game_id, game, entry, th in ranked:
            self.ranked_table.insert((
                game["name"],
                entry.get("rank", ""),
                entry.get("createdAt", "")[:10],
                _format_score(entry.get("score", "0")),
                _format_score(th["high"]),
                _format_score(th["top10"]),
                _format_score(th["top50"]),
            ))
            self._ranked_game_ids.append(game_id)

        unranked.sort(key=lambda x: x[1]["name"].lower())
        for game_id, game, th in unranked:
            self.unranked_table.insert((
                game["name"],
                _format_score(th["high"]),
                _format_score(th["top10"]),
                _format_score(th["top50"]),
                _format_score(th["top100"]),
            ))
            self._unranked_game_ids.append(game_id)

        self.ranked_table.auto_resize()
        self.unranked_table.auto_resize()

        self.notebook.tab(self.ranked_frame,
                          text=f" {username}'s Rankings ({len(ranked)}) ")
        self.notebook.tab(self.unranked_frame,
                          text=f" {username}'s Unranked ({len(unranked)}) ")
        self.notebook.select(self.ranked_frame)

    def _on_left_select(self, source: str):
        table_map = {
            "allgames": (self.allgames_table, self._allgames_game_ids),
            "ranked": (self.ranked_table, self._ranked_game_ids),
            "unranked": (self.unranked_table, self._unranked_game_ids),
        }
        table, id_list = table_map[source]
        idx = table.selection_index()
        if idx < 0 or idx >= len(id_list):
            return
        game_id = id_list[idx]
        self._show_detail(self.data[game_id])

    def _load_boxart(self, url: str):
        """Load boxart image on-demand in background thread."""
        if not url:
            self.boxart_label.config(image="", width=0)
            return

        # Already cached?
        if url in self._image_cache:
            img = self._image_cache[url]
            self.boxart_label.config(image=img, width=img.width())
            return

        # Show blank while loading
        self.boxart_label.config(image="", width=80)

        def _fetch():
            try:
                resp = self._http_session.get(url, timeout=8)
                resp.raise_for_status()
                pil_img = Image.open(io.BytesIO(resp.content))
                # Scale to 80px height, keep aspect ratio
                h = 80
                w = int(pil_img.width * h / pil_img.height)
                pil_img = pil_img.resize((w, h), Image.LANCZOS)
                self.root.after(0, lambda: self._set_boxart(url, pil_img))
            except Exception:
                self.root.after(0, lambda: self.boxart_label.config(image="", width=0))

        threading.Thread(target=_fetch, daemon=True).start()

    def _set_boxart(self, url: str, pil_img: Image.Image):
        try:
            tk_img = ImageTk.PhotoImage(pil_img)
            self._image_cache[url] = tk_img
            self.boxart_label.config(image=tk_img, width=tk_img.width())
        except Exception:
            self.boxart_label.config(image="", width=0)

    def _show_detail(self, game: dict):
        self.detail_label.config(text=game["name"].upper())
        self._load_boxart(game.get("boxart", ""))
        self.detail_tree.delete(*self.detail_tree.get_children())

        search = self.search_var.get().strip().lower()

        for s in game["scores"]:
            rank = s.get("rank", 999)
            tags: tuple = ()
            if search and search == s.get("userName", "").lower():
                tags = ("highlight",)
            elif rank == 1:
                tags = ("rank1",)
            elif rank <= 10:
                tags = ("top10",)
            elif rank <= 50:
                tags = ("top50",)

            self.detail_tree.insert("", tk.END, values=(
                rank,
                s.get("userName", ""),
                s.get("signature", ""),
                _format_score(s.get("score", "0")),
                _hw_name(s.get("hardware", "")),
                s.get("createdAt", "")[:10],
            ), tags=tags)

        if search:
            children = self.detail_tree.get_children()
            total = len(children)
            for item in children:
                if "highlight" in self.detail_tree.item(item, "tags"):
                    self.detail_tree.selection_set(item)
                    # Scroll so the user's row appears in the center
                    idx = self.detail_tree.index(item)
                    if total > 0:
                        # Position the row at ~center of visible area
                        fraction = max(0.0, (idx - 5) / total)
                        self.detail_tree.yview_moveto(fraction)
                    break

    def _load_tournaments(self):
        """Load tournament list and pre-fetch scores in background."""
        def _fetch():
            try:
                tournaments = fetch_tournaments()
                # Pre-fetch scores for all tournaments
                for t in tournaments:
                    tid = t["id"]
                    if tid not in self._tournament_scores_cache:
                        try:
                            scores = fetch_tournament_scores(tid)
                            self._tournament_scores_cache[tid] = scores
                        except Exception:
                            pass
                self.root.after(0, lambda: self._populate_tournaments(tournaments))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _rebuild_tournament_table(self, with_user: bool):
        """Rebuild tournament list table with or without user columns."""
        if self.tournament_table is not None and self._tournament_list_has_user == with_user:
            return
        for w in self.tournament_list_frame.winfo_children():
            w.destroy()

        score_cols = [
            ("high",  "HIGHSCORE", 100, GOLD),
            ("top10", "TOP 10",    100, NEON_GREEN),
            ("top50", "TOP 50",    100, NEON_CYAN),
        ]

        cols = [
            ("status", "STATUS", 60, "center", False, NEON_GREEN),
            ("name",   "TOURNAMENT", 200, "w", False, NEON_PINK),
            ("dates",  "DATES", 140, "center", False, FG_DEFAULT),
        ]
        if with_user:
            cols.append(("rank", "RANK", 50, "center", False, NEON_PINK))
            cols.append(("uscore", "USER SCORE", 100, "center", True, NEON_YELLOW))
        cols.extend([(c, t, w, "center", True, fg) for c, t, w, fg in score_cols])

        self.tournament_table = ColorTable(self.tournament_list_frame, cols)
        self.tournament_table.bind_select(self._on_tournament_select)
        self._tournament_list_has_user = with_user

    def _populate_tournaments(self, tournaments: list[dict]):
        self._tournaments = tournaments
        search = self.search_var.get().strip().lower()
        has_user = bool(search)

        self._rebuild_tournament_table(with_user=has_user)
        self.tournament_table.delete_all()

        # Configure status-specific tags on the status column (index 0)
        self.tournament_table.configure_column_tag(0, "active",
            foreground=NEON_GREEN, background=ROW_EVEN)
        self.tournament_table.configure_column_tag(0, "expired",
            foreground="#ff4444", background=ROW_EVEN)
        self.tournament_table.configure_column_tag(0, "upcoming",
            foreground=NEON_YELLOW, background=ROW_EVEN)

        for t in tournaments:
            status = t.get("status", "")
            name = t.get("name", "")
            start = t.get("start", "")[:10]
            end = t.get("end", "")[:10]
            dates = f"{start} — {end}" if start else ""

            # Get aggregated scores across all tournament games
            tid = t["id"]
            cached = self._tournament_scores_cache.get(tid, [])
            all_scores: list[dict] = []
            for g in cached:
                all_scores.extend(g.get("scores", []))

            # Thresholds from combined scores (sorted by score descending)
            all_scores.sort(key=lambda s: int(s.get("score", "0")), reverse=True)
            high = _format_score(all_scores[0]["score"]) if all_scores else "—"
            top10 = _format_score(all_scores[9]["score"]) if len(all_scores) >= 10 else "—"
            top50 = _format_score(all_scores[49]["score"]) if len(all_scores) >= 50 else "—"

            status_tag = status.lower() if status.lower() in ("active", "expired", "upcoming") else "even"

            if has_user:
                # Find user's best rank across tournament games
                user_rank = None
                user_score = None
                for g in cached:
                    for s in g.get("scores", []):
                        if search == s.get("userName", "").lower():
                            if user_rank is None or s["rank"] < user_rank:
                                user_rank = s["rank"]
                                user_score = s["score"]

                self.tournament_table.insert((
                    status, name, dates,
                    str(user_rank) if user_rank else "unranked",
                    _format_score(user_score) if user_score else "",
                    high, top10, top50,
                ), col_tags={0: status_tag})
            else:
                self.tournament_table.insert((
                    status, name, dates, high, top10, top50,
                ), col_tags={0: status_tag})

        count = len([t for t in tournaments if t.get("status") == "Active"])
        self.tournament_table.auto_resize()
        self._update_tournament_tab_title(count)

    def _update_tournament_tab_title(self, active_count: int = 0):
        for i in range(self.notebook.index("end")):
            if self.notebook.tab(i, "text").strip().startswith("TOURNAMENTS"):
                self.notebook.tab(i, text=f" TOURNAMENTS ({active_count} active) ")
                break

    def _on_tournament_select(self):
        """Drill down: tournament selected -> show its games."""
        idx = self.tournament_table.selection_index()
        if idx < 0 or idx >= len(self._tournaments):
            return
        tournament = self._tournaments[idx]
        tid = tournament["id"]
        self._current_tournament = tournament

        # Check cache
        if tid in self._tournament_scores_cache:
            self._show_tournament_games(tournament, self._tournament_scores_cache[tid])
            return

        # Fetch scores in background
        self.detail_label.config(text="Loading...")
        self.detail_tree.delete(*self.detail_tree.get_children())

        def _fetch():
            try:
                game_scores = fetch_tournament_scores(tid)
                self._tournament_scores_cache[tid] = game_scores
                self.root.after(0, lambda: self._show_tournament_games(tournament, game_scores))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _show_tournament_games(self, tournament: dict, game_scores: list[dict]):
        """Show the games of a tournament with score thresholds."""
        self._tournament_game_scores = game_scores

        # Build the games table (with user columns if search active)
        for w in self.tournament_games_frame.winfo_children():
            w.destroy()

        search = self.search_var.get().strip().lower()
        has_user = bool(search)

        score_cols = [
            ("high",  "HIGHSCORE", 110, GOLD),
            ("top10", "TOP 10",    110, NEON_GREEN),
            ("top50", "TOP 50",    110, NEON_CYAN),
        ]

        cols = [("game", "TABLE", 230, "w", False, NEON_PINK)]
        if has_user:
            cols.append(("rank", "RANK", 55, "center", False, NEON_PINK))
            cols.append(("uscore", "USER SCORE", 105, "center", True, NEON_YELLOW))
        cols.extend([(c, t, w, "center", True, fg) for c, t, w, fg in score_cols])

        self.tournament_games_table = ColorTable(self.tournament_games_frame, cols)
        self.tournament_games_table.bind_select(self._on_tournament_game_select)

        for game in game_scores:
            scores = game.get("scores", [])
            by_rank = {s["rank"]: s["score"] for s in scores if s.get("rank")}
            th_high = by_rank.get(1, "")
            th_top10 = by_rank.get(10, "")
            th_top50 = by_rank.get(50, "") if len(scores) >= 50 else (
                scores[-1]["score"] if scores else ""
            )

            if has_user:
                user_entry = None
                for s in scores:
                    if search == s.get("userName", "").lower():
                        user_entry = s
                        break
                rank_str = str(user_entry["rank"]) if user_entry else "unranked"
                score_str = _format_score(user_entry["score"]) if user_entry else ""
                self.tournament_games_table.insert((
                    game["name"], rank_str, score_str,
                    _format_score(th_high),
                    _format_score(th_top10),
                    _format_score(th_top50),
                ))
            else:
                self.tournament_games_table.insert((
                    game["name"],
                    _format_score(th_high),
                    _format_score(th_top10),
                    _format_score(th_top50),
                ))

        self.tournament_games_table.auto_resize()

        # Update header with tournament name + dates + user info
        start = tournament.get("start", "")[:10]
        end = tournament.get("end", "")[:10]
        dates = f"  ({start} — {end})" if start else ""
        user_info = ""
        if search:
            for g in game_scores:
                for s in g.get("scores", []):
                    if search == s.get("userName", "").lower():
                        user_info = f"  |  Rank #{s['rank']} — {_format_score(s['score'])} ({g['name']})"
                        break
                if user_info:
                    break
            if not user_info:
                user_info = "  |  unranked"
        self.tournament_info_label.config(text=f"{tournament['name']}{dates}{user_info}")

        # Switch view: hide list, show header + games
        self.tournament_list_frame.pack_forget()
        self.tournament_back_frame.pack(fill=tk.X, before=self.tournament_container)
        self.tournament_games_frame.pack(fill=tk.BOTH, expand=True)

        # Show overlay and combined scores on the right
        overlay = tournament.get("overlay", "")
        if overlay:
            self._load_boxart(overlay)
        else:
            self.boxart_label.config(image="", width=0)
        self._show_tournament_combined_detail(tournament, game_scores)

    def _on_tournament_game_select(self):
        """Show full leaderboard for selected tournament game."""
        idx = self.tournament_games_table.selection_index()
        if idx < 0 or idx >= len(self._tournament_game_scores):
            return
        game = self._tournament_game_scores[idx]
        self._show_tournament_game_detail(game)

    def _show_tournament_combined_detail(self, tournament: dict, game_scores: list[dict]):
        """Show all tournament game scores combined in the detail tree."""
        self.detail_label.config(text=tournament["name"].upper())
        self.detail_tree.delete(*self.detail_tree.get_children())
        search = self.search_var.get().strip().lower()

        for game in game_scores:
            # Game name separator
            self.detail_tree.insert("", tk.END, values=(
                "", f"── {game['name']} ──", "", "", "", "",
            ), tags=("game_sep",))

            for s in game["scores"]:
                rank = s.get("rank", 999)
                tags: tuple = ()
                if search and search == s.get("userName", "").lower():
                    tags = ("highlight",)
                elif rank == 1:
                    tags = ("rank1",)
                elif rank <= 10:
                    tags = ("top10",)

                self.detail_tree.insert("", tk.END, values=(
                    rank,
                    s.get("userName", ""),
                    s.get("signature", ""),
                    _format_score(s.get("score", "0")),
                    _hw_name(s.get("hardware", "")),
                    "",
                ), tags=tags)

        # Scroll to user if found
        if search:
            for item in self.detail_tree.get_children():
                if "highlight" in self.detail_tree.item(item, "tags"):
                    self.detail_tree.selection_set(item)
                    children = self.detail_tree.get_children()
                    total = len(children)
                    i = self.detail_tree.index(item)
                    if total > 0:
                        fraction = max(0.0, (i - 5) / total)
                        self.detail_tree.yview_moveto(fraction)
                    break

    def _show_tournament_game_detail(self, game: dict):
        """Show a single tournament game's scores in the right detail tree."""
        self.detail_label.config(text=game["name"].upper())

        boxart = game.get("boxart", "")
        if boxart:
            self._load_boxart(boxart)

        self.detail_tree.delete(*self.detail_tree.get_children())
        search = self.search_var.get().strip().lower()

        for s in game["scores"]:
            rank = s.get("rank", 999)
            tags: tuple = ()
            if search and search == s.get("userName", "").lower():
                tags = ("highlight",)
            elif rank == 1:
                tags = ("rank1",)
            elif rank <= 10:
                tags = ("top10",)

            self.detail_tree.insert("", tk.END, values=(
                rank,
                s.get("userName", ""),
                s.get("signature", ""),
                _format_score(s.get("score", "0")),
                _hw_name(s.get("hardware", "")),
                "",
            ), tags=tags)

        if search:
            children = self.detail_tree.get_children()
            total = len(children)
            for item in children:
                if "highlight" in self.detail_tree.item(item, "tags"):
                    self.detail_tree.selection_set(item)
                    i = self.detail_tree.index(item)
                    if total > 0:
                        fraction = max(0.0, (i - 5) / total)
                        self.detail_tree.yview_moveto(fraction)
                    break

    def _on_tournament_header_click(self):
        """Click on tournament name -> show combined scores on right."""
        if self._current_tournament:
            tid = self._current_tournament["id"]
            if tid in self._tournament_scores_cache:
                self._show_tournament_combined_detail(
                    self._current_tournament,
                    self._tournament_scores_cache[tid],
                )
                overlay = self._current_tournament.get("overlay", "")
                if overlay:
                    self._load_boxart(overlay)

    def _tournament_go_back(self):
        """Go back from games view to tournament list."""
        self.tournament_games_frame.pack_forget()
        self.tournament_back_frame.pack_forget()
        self.tournament_list_frame.pack(fill=tk.BOTH, expand=True)
        self._current_tournament = None
        self.detail_tree.delete(*self.detail_tree.get_children())
        self.detail_label.config(text="SELECT A TABLE")
        self.boxart_label.config(image="", width=0)

    def _show_progress(self):
        self.progress_label.pack(side=tk.LEFT, padx=(12, 4))
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _hide_progress(self):
        self.progress_label.pack_forget()
        self.progress_bar.pack_forget()

    def _start_scrape(self):
        self.scrape_btn.config(state=tk.DISABLED)
        self._show_progress()
        self.progress_var.set(0)

        def do_scrape():
            estimated_total = len(self.data) if self.data else 0

            def on_progress(scores_done, games_found, games_done, name):
                if games_done:
                    total = games_found
                    total_str = str(total)
                else:
                    total = max(games_found, estimated_total)
                    total_str = f"~{total}"
                pct = (scores_done / total) * 100 if total else 0
                text = f"Loading scores [{scores_done}/{total_str}] {name}"
                self.root.after(0, lambda: self.progress_var.set(pct))
                self.root.after(0, lambda: self.progress_label.config(text=text))

            try:
                data = scrape_all(progress_callback=on_progress)
                save_data(data)
                self.root.after(0, lambda: self._on_scrape_done(data))
            except Exception as e:
                self.root.after(0, lambda: self._on_scrape_error(str(e)))

        thread = threading.Thread(target=do_scrape, daemon=True)
        thread.start()

    def _on_scrape_done(self, data: dict):
        self.data = data
        self.status_var.set(f"{len(data)} games loaded.")
        self._hide_progress()
        self.scrape_btn.config(state=tk.NORMAL)
        self._on_search()

    def _on_scrape_error(self, error: str):
        self._hide_progress()
        self.scrape_btn.config(state=tk.NORMAL)
        messagebox.showerror("Error", f"Scraping failed:\n{error}")


def main():
    root = tk.Tk()
    PinballScoresApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
