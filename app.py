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

from scraper import load_data, scrape_all, save_data, _APP_DIR

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
        self._row_count = 0
        self._sel_idx = -1
        self._select_callbacks: list = []

        # Scrollbar (right side of everything)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Colored header row
        header = tk.Frame(parent, bg=BG_HEADER, height=26)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        # Body frame for synced treeviews
        body = tk.Frame(parent, bg=BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True)

        for col_def in col_defs:
            _, text, width, anchor, stretch, fg = col_def[:6]
            font = col_def[6] if len(col_def) > 6 else None

            # --- Header cell ---
            if stretch:
                hc = tk.Frame(header, bg=BG_HEADER)
                hc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                hc = tk.Frame(header, width=width, bg=BG_HEADER)
                hc.pack(side=tk.LEFT, fill=tk.Y)
                hc.pack_propagate(False)
            tk.Label(hc, text=text, fg=fg, bg=BG_HEADER,
                     font=(FONT_FAMILY, 9, "bold")).pack(expand=True, fill=tk.BOTH)

            # --- Column treeview ---
            t = ttk.Treeview(body, columns=("v",), show="", selectmode="none",
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

            if stretch:
                t.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                t.pack(side=tk.LEFT, fill=tk.Y)

            t.bind("<Button-1>", self._on_click)
            t.bind("<Button-4>", lambda e: self._scroll_all("scroll", -3, "units"))
            t.bind("<Button-5>", lambda e: self._scroll_all("scroll", 3, "units"))
            self._trees.append(t)

        # Connect scrollbar to first tree, sync rest
        self._scroll = scroll
        scroll.configure(command=self._scroll_all)
        self._trees[0].configure(yscrollcommand=self._on_yscroll)

    def _scroll_all(self, *args):
        for t in self._trees:
            t.yview(*args)

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
        # Deselect previous
        if 0 <= self._sel_idx < self._row_count:
            tag = "even" if self._sel_idx % 2 == 0 else "odd"
            for t in self._trees:
                t.item(t.get_children()[self._sel_idx], tags=(tag,))

        self._sel_idx = idx

        # Select new
        if 0 <= idx < self._row_count:
            tag = "even_sel" if idx % 2 == 0 else "odd_sel"
            for t in self._trees:
                items = t.get_children()
                t.item(items[idx], tags=(tag,))
                t.see(items[idx])

    def insert(self, values):
        tag = "even" if self._row_count % 2 == 0 else "odd"
        for i, t in enumerate(self._trees):
            val = values[i] if i < len(values) else ""
            t.insert("", tk.END, values=(val,), tags=(tag,))
        self._row_count += 1

    def delete_all(self):
        for t in self._trees:
            t.delete(*t.get_children())
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

    # Borderless style for ColorTable sub-treeviews
    style.configure("Borderless.Treeview",
                     background=BG_PANEL, foreground=FG_DEFAULT,
                     fieldbackground=BG_PANEL, rowheight=26,
                     font=(FONT_FAMILY, 10), borderwidth=0, relief="flat")
    style.layout("Borderless.Treeview", [
        ("Borderless.Treeview.treearea", {"sticky": "nswe"}),
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

        self._build_ui()
        self._load_saved_username()
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
        self.notebook.add(self.allgames_frame, text=" ALL GAMES ")
        self.allgames_table: ColorTable | None = None
        self._allgames_has_user_cols = False

        # --- Tab 1: User Rankings (added/removed dynamically) ---
        self.ranked_frame = ttk.Frame(self.notebook)

        self.ranked_table = ColorTable(self.ranked_frame, [
            ("game",  "GAME",       200, "w",      True,  NEON_PINK),
            ("rank",  "RANK",        50, "center", False, NEON_PINK),
            ("date",  "DATE",        85, "center", False, NEON_PINK),
            ("score", "USER SCORE", 105, "center", False, NEON_YELLOW),
            ("high",  "HIGHSCORE",  105, "center", False, GOLD),
            ("top10", "TOP 10",     105, "center", False, NEON_GREEN),
            ("top50", "TOP 50",     105, "center", False, NEON_CYAN),
        ])
        self.ranked_table.bind_select(lambda: self._on_left_select("ranked"))

        # --- Tab 2: User Unranked Games (added/removed dynamically) ---
        self.unranked_frame = ttk.Frame(self.notebook)

        self.unranked_table = ColorTable(self.unranked_frame, [
            ("game", "GAME", 230, "w", True, NEON_PINK),
            *[(c, t, w, "center", False, fg) for c, t, w, fg in score_col_defs],
        ])
        self.unranked_table.bind_select(lambda: self._on_left_select("unranked"))

        self._user_tabs_visible = False

        # Right: boxart + full top 100 detail
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        # Header row: boxart image + game title
        header = ttk.Frame(right)
        header.pack(fill=tk.X, pady=(4, 4))

        self.boxart_label = tk.Label(header, bg=BG_DARK, width=80, height=80)
        self.boxart_label.pack(side=tk.LEFT, padx=(0, 8))
        self._boxart_placeholder = None  # will hold a blank image

        self.detail_label = ttk.Label(header, text="SELECT A GAME", style="Title.TLabel")
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
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            name = settings.get("username", "")
            if name:
                self.search_var.set(name)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_username(self):
        SETTINGS_FILE.parent.mkdir(exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps({"username": self.search_var.get().strip()}, indent=2),
            encoding="utf-8",
        )

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

    def _on_search(self):
        search = self.search_var.get().strip().lower()
        self._populate_tabs(search)
        self.detail_tree.delete(*self.detail_tree.get_children())
        self.detail_label.config(text="SELECT A GAME")
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

        cols = [("game", "GAME", 200 if with_user else 230, "w", True, NEON_PINK)]
        if with_user:
            cols.append(("rank", "RANK", 65, "center", False, NEON_PINK))
            cols.append(("uscore", "USER SCORE", 105, "center", False, NEON_YELLOW))
        cols.extend([(c, t, w, "center", False, fg) for c, t, w, fg in score_col_defs])

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

    def _populate_tabs(self, search: str = ""):
        has_search = bool(search and self.data)

        # Rebuild All Games table if user-column state changed
        self._rebuild_allgames_table(with_user=has_search)
        self.allgames_table.delete_all()
        self._allgames_game_ids.clear()

        # Pre-compute user entries for all games (needed for All Games + user tabs)
        user_map: dict[str, dict | None] = {}  # game_id -> user score entry or None
        if has_search:
            for game_id, game in self.data.items():
                for s in game["scores"]:
                    if search == s.get("userName", "").lower():
                        user_map[game_id] = s
                        break
                else:
                    user_map[game_id] = None

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

        self.notebook.tab(0, text=f" ALL GAMES ({len(self.data)}) ")

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
        tk_img = ImageTk.PhotoImage(pil_img)
        self._image_cache[url] = tk_img
        self.boxart_label.config(image=tk_img, width=tk_img.width())

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
            for item in self.detail_tree.get_children():
                if "highlight" in self.detail_tree.item(item, "tags"):
                    self.detail_tree.see(item)
                    self.detail_tree.selection_set(item)
                    break

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
