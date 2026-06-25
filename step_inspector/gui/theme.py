"""Visual theming for the STEP Inspector GUI.

Two palettes (light and dark) plus helpers to apply them to a Tk root, a
``ttk.Style``, and the per-widget colors that ttk does not reach (Text,
Listbox, Canvas, and tag colors).  The layout is untouched — this module
only controls colors, fonts, spacing and borders.
"""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    name: str
    is_dark: bool

    # surfaces
    window: str          # app background
    panel: str           # content background (Text / Treeview / Listbox)
    panel_alt: str       # zebra rows, line-number gutter
    raised: str          # cards / banner / toolbars
    border: str

    # text
    text: str            # primary
    text_dim: str        # secondary / inactive

    # accent + selection
    accent: str
    accent_text: str
    sel_bg: str
    sel_fg: str

    # semantic foregrounds (tags)
    good: str
    warn: str
    danger: str
    info: str
    comment: str

    # banner badge background / foreground by status
    badge_good: tuple
    badge_caution: tuple
    badge_warn: tuple
    badge_danger: tuple

    # File-audit span backgrounds
    span_entity: str
    span_header: str
    span_structure: str
    span_comment: str
    span_orphan: str

    # 3D viewer
    canvas_bg: str
    grid: str
    vertex: str
    free_point: str
    axis_x: str
    axis_y: str
    axis_z: str
    hud: str
    surface: tuple        # base shaded-surface RGB (0-255)
    surface_edge: str     # triangle edge color in shaded mode
    kind_line: str
    kind_curve: str
    kind_spline: str
    kind_polyline: str
    kind_tess: str
    kind_approx: str

    def kind_colors(self) -> dict:
        return {
            "line": self.kind_line,
            "circle": self.kind_curve,
            "ellipse": self.kind_curve,
            "spline": self.kind_spline,
            "polyline": self.kind_polyline,
            "tess": self.kind_tess,
            "approx": self.kind_approx,
        }

    def span_backgrounds(self) -> dict:
        return {
            "entity": self.span_entity,
            "header": self.span_header,
            "structure": self.span_structure,
            "comment": self.span_comment,
            "orphan": self.span_orphan,
        }

    def severity_colors(self) -> dict:
        return {"orphan": self.danger, "comment": self.comment,
                "warning": self.warn, "info": self.good}


LIGHT = Palette(
    name="light", is_dark=False,
    window="#eef1f5", panel="#ffffff", panel_alt="#f3f5f8", raised="#ffffff",
    border="#d4d9e0",
    text="#1f2933", text_dim="#6b7480",
    accent="#2563eb", accent_text="#ffffff",
    sel_bg="#dbe7ff", sel_fg="#10243f",
    good="#1e7d34", warn="#9a5b00", danger="#c62828", info="#1d4ed8",
    comment="#9a5b00",
    badge_good=("#1e7d34", "#ffffff"),
    badge_caution=("#f4b400", "#3a2f00"),
    badge_warn=("#e8730c", "#ffffff"),
    badge_danger=("#c62828", "#ffffff"),
    span_entity="#ffffff", span_header="#e6f4ea", span_structure="#e4ecfb",
    span_comment="#fff3cd", span_orphan="#fcdcdc",
    canvas_bg="#1a1d24", grid="#2a2e38",
    vertex="#ffffff", free_point="#ffe07a",
    axis_x="#e06c60", axis_y="#7bc86c", axis_z="#6c9fe0", hud="#ff9d6b",
    surface=(150, 170, 205), surface_edge="#0e1220",
    kind_line="#7fb2ff", kind_curve="#ffce6e", kind_spline="#9be08a",
    kind_polyline="#e58bff", kind_tess="#6fd6c4", kind_approx="#ff8f8f",
)

DARK = Palette(
    name="dark", is_dark=True,
    window="#1b1d23", panel="#23262d", panel_alt="#2a2e36", raised="#262932",
    border="#363b45",
    text="#e6e8eb", text_dim="#9aa3af",
    accent="#5b9dff", accent_text="#0c1626",
    sel_bg="#33415c", sel_fg="#eef4ff",
    good="#5fce7e", warn="#e3b341", danger="#ff6b6b", info="#7aa2ff",
    comment="#e3b341",
    badge_good=("#2e7d46", "#eafff0"),
    badge_caution=("#b9912b", "#1d1600"),
    badge_warn=("#c4631a", "#fff3e9"),
    badge_danger=("#b3322f", "#ffecec"),
    span_entity="#23262d", span_header="#1f3326", span_structure="#1f2a44",
    span_comment="#3a3320", span_orphan="#43272a",
    canvas_bg="#15171c", grid="#262a33",
    vertex="#ffffff", free_point="#ffe07a",
    axis_x="#e8736a", axis_y="#85d178", axis_z="#7aa6e8", hud="#ffb07a",
    surface=(126, 148, 184), surface_edge="#0a0c12",
    kind_line="#7fb2ff", kind_curve="#ffce6e", kind_spline="#9be08a",
    kind_polyline="#e58bff", kind_tess="#6fd6c4", kind_approx="#ff8f8f",
)

PALETTES = {"light": LIGHT, "dark": DARK}


def fonts() -> dict:
    """Pick reasonable UI / monospace families available on the system."""
    families = set(tkfont.families())
    ui = next((f for f in ("Inter", "Segoe UI", "Cantarell", "Noto Sans",
                           "DejaVu Sans", "Helvetica") if f in families),
              "TkDefaultFont")
    mono = next((f for f in ("JetBrains Mono", "Cascadia Code", "Hack",
                             "DejaVu Sans Mono", "Noto Sans Mono",
                             "Liberation Mono") if f in families),
                "TkFixedFont")
    return {"ui": ui, "mono": mono}


def apply(root, style: ttk.Style, pal: Palette, fnt: dict) -> None:
    """Apply a palette to the whole application via ttk + root options."""
    try:
        style.theme_use("clam")
    except Exception:
        pass
    ui = fnt["ui"]
    base = (ui, 10)

    root.configure(background=pal.window)
    root.option_clear()
    # tk (non-ttk) widget defaults
    root.option_add("*background", pal.window)
    root.option_add("*foreground", pal.text)
    root.option_add("*Menu.background", pal.raised)
    root.option_add("*Menu.foreground", pal.text)
    root.option_add("*Menu.activeBackground", pal.accent)
    root.option_add("*Menu.activeForeground", pal.accent_text)
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 0)

    style.configure(".", background=pal.window, foreground=pal.text,
                    fieldbackground=pal.panel, bordercolor=pal.border,
                    lightcolor=pal.window, darkcolor=pal.window,
                    troughcolor=pal.panel_alt, focuscolor=pal.accent,
                    font=base)
    style.configure("TFrame", background=pal.window)
    style.configure("TLabel", background=pal.window, foreground=pal.text)
    style.configure("Dim.TLabel", foreground=pal.text_dim)
    style.configure("Title.TLabel", font=(ui, 12, "bold"))
    style.configure("Status.TLabel", background=pal.raised,
                    foreground=pal.text_dim, padding=(10, 4))
    style.configure("Toolbar.TFrame", background=pal.raised)
    style.configure("Toolbar.TLabel", background=pal.raised,
                    foreground=pal.text_dim)
    style.configure("Toolbar.TCheckbutton", background=pal.raised)
    style.map("Toolbar.TCheckbutton", background=[("active", pal.raised)])
    style.configure("Toolbar.TRadiobutton", background=pal.raised)
    style.map("Toolbar.TRadiobutton", background=[("active", pal.raised)],
              indicatorcolor=[("selected", pal.accent),
                              ("!selected", pal.panel_alt)])
    style.configure("Banner.TFrame", background=pal.raised)
    style.configure("Banner.TLabel", background=pal.raised, foreground=pal.text)
    style.configure("Hint.TLabel", background=pal.window,
                    foreground=pal.text_dim)

    # buttons
    style.configure("TButton", background=pal.panel_alt, foreground=pal.text,
                    bordercolor=pal.border, focusthickness=1, relief="flat",
                    padding=(12, 6))
    style.map("TButton",
              background=[("active", pal.sel_bg), ("pressed", pal.accent)],
              foreground=[("pressed", pal.accent_text)])
    style.configure("Accent.TButton", background=pal.accent,
                    foreground=pal.accent_text, padding=(14, 6))
    style.map("Accent.TButton",
              background=[("active", pal.accent), ("pressed", pal.accent)])

    # checkbuttons
    style.configure("TCheckbutton", background=pal.raised, foreground=pal.text,
                    focuscolor=pal.accent)
    style.map("TCheckbutton",
              background=[("active", pal.raised)],
              foreground=[("active", pal.text)],
              indicatorcolor=[("selected", pal.accent),
                              ("!selected", pal.panel_alt)])

    # entries
    style.configure("TEntry", fieldbackground=pal.panel,
                    foreground=pal.text, bordercolor=pal.border,
                    insertcolor=pal.text, padding=4)
    style.map("TEntry", bordercolor=[("focus", pal.accent)])

    # notebook — flat tabs, accent on the active one
    style.configure("TNotebook", background=pal.window, borderwidth=0,
                    tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=pal.window,
                    foreground=pal.text_dim, padding=(16, 8),
                    borderwidth=0, font=base)
    style.map("TNotebook.Tab",
              background=[("selected", pal.panel), ("active", pal.panel_alt)],
              foreground=[("selected", pal.accent), ("active", pal.text)],
              expand=[("selected", (0, 0, 0, 0))])

    # paned windows
    style.configure("TPanedwindow", background=pal.window)
    style.configure("Sash", sashthickness=6, gripcount=0,
                    background=pal.window, bordercolor=pal.border)

    # labelframes
    style.configure("TLabelframe", background=pal.window,
                    bordercolor=pal.border, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=pal.window,
                    foreground=pal.text_dim, font=(ui, 9, "bold"))

    # treeview
    rowh = max(22, fnt.get("rowheight", 24))
    style.configure("Treeview", background=pal.panel, fieldbackground=pal.panel,
                    foreground=pal.text, bordercolor=pal.border,
                    borderwidth=0, rowheight=rowh, font=base)
    style.map("Treeview",
              background=[("selected", pal.sel_bg)],
              foreground=[("selected", pal.sel_fg)])
    style.configure("Treeview.Heading", background=pal.panel_alt,
                    foreground=pal.text_dim, relief="flat",
                    font=(ui, 9, "bold"), padding=(6, 5),
                    bordercolor=pal.border)
    style.map("Treeview.Heading",
              background=[("active", pal.sel_bg)])

    # scrollbars
    style.configure("TScrollbar", background=pal.panel_alt,
                    troughcolor=pal.window, bordercolor=pal.window,
                    arrowcolor=pal.text_dim, relief="flat")
    style.map("TScrollbar", background=[("active", pal.border)])


def style_text(widget, pal: Palette, mono: bool = True, dim: bool = False,
               flat_bg: bool = False) -> None:
    """Apply palette colors to a tk.Text widget."""
    bg = pal.window if flat_bg else pal.panel
    widget.configure(
        background=bg, foreground=pal.text_dim if dim else pal.text,
        insertbackground=pal.text, selectbackground=pal.sel_bg,
        selectforeground=pal.sel_fg, highlightthickness=0, borderwidth=0,
        relief="flat")


def style_listbox(widget, pal: Palette) -> None:
    widget.configure(
        background=pal.panel, foreground=pal.text,
        selectbackground=pal.sel_bg, selectforeground=pal.sel_fg,
        highlightthickness=0, borderwidth=0, relief="flat",
        activestyle="none")
