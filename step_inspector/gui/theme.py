"""Visual theming for the STEP Inspector GUI.

Two palettes (light and dark) plus helpers to apply them to a Tk root, a
``ttk.Style``, and the per-widget colors that ttk does not reach (Text,
Listbox, Canvas, and tag colors).  The layout is untouched — this module
only controls colors, fonts, spacing and borders.
"""

from __future__ import annotations

from dataclasses import dataclass


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


# Preferred font families (Qt resolves the first available).
UI_FONT = "Inter, 'Segoe UI', Cantarell, 'Noto Sans', 'DejaVu Sans', sans-serif"
MONO_FONT = ("'JetBrains Mono', 'Cascadia Code', Hack, 'DejaVu Sans Mono', "
             "'Noto Sans Mono', 'Liberation Mono', monospace")


def qss(pal: Palette) -> str:
    """Build a Qt stylesheet for the whole application from a palette."""
    p = pal
    return f"""
    * {{
        font-family: {UI_FONT};
        font-size: 13px;
        outline: 0;
    }}
    QMainWindow, QWidget {{
        background: {p.window};
        color: {p.text};
    }}
    QMenuBar {{ background: {p.raised}; color: {p.text}; border: 0; }}
    QMenuBar::item {{ background: transparent; padding: 6px 10px; }}
    QMenuBar::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
    QMenu {{ background: {p.raised}; color: {p.text};
             border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}

    #Banner {{ background: {p.raised}; }}
    #BannerFile {{ font-size: 15px; font-weight: 600; color: {p.text}; }}
    #StatusBar {{ background: {p.raised}; color: {p.text_dim}; }}
    #Toolbar {{ background: {p.raised}; }}
    #Toolbar QLabel {{ color: {p.text_dim}; }}
    #Hint {{ color: {p.text_dim}; }}
    #SectionHint {{ color: {p.text_dim}; padding: 6px 2px; }}

    QTabWidget::pane {{ border: 0; background: {p.panel}; }}
    QTabBar {{ background: {p.window}; }}
    QTabBar::tab {{
        background: {p.window}; color: {p.text_dim};
        padding: 9px 18px; margin-right: 2px; border: 0;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{ color: {p.text}; background: {p.panel_alt}; }}
    QTabBar::tab:selected {{
        color: {p.accent}; background: {p.panel};
        border-bottom: 2px solid {p.accent};
    }}

    QPushButton {{
        background: {p.panel_alt}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{ background: {p.sel_bg}; }}
    QPushButton:pressed {{ background: {p.accent}; color: {p.accent_text}; }}
    QPushButton#Accent {{
        background: {p.accent}; color: {p.accent_text}; border: 0;
        font-weight: 600;
    }}
    QPushButton#Accent:hover {{ background: {p.accent}; }}

    QRadioButton, QCheckBox {{ color: {p.text}; spacing: 6px; }}
    QRadioButton::indicator, QCheckBox::indicator {{
        width: 14px; height: 14px;
    }}
    QRadioButton::indicator {{
        border: 1px solid {p.border}; border-radius: 8px;
        background: {p.panel};
    }}
    QRadioButton::indicator:checked {{
        border: 4px solid {p.accent}; background: {p.panel};
    }}
    QCheckBox::indicator {{
        border: 1px solid {p.border}; border-radius: 3px; background: {p.panel};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent}; border: 1px solid {p.accent};
    }}

    QLineEdit {{
        background: {p.panel}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 6px; padding: 5px 8px;
        selection-background-color: {p.sel_bg};
    }}
    QLineEdit:focus {{ border: 1px solid {p.accent}; }}

    QTreeWidget, QTreeView, QListWidget {{
        background: {p.panel}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 6px;
        alternate-background-color: {p.panel_alt};
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
    QTreeView::item:selected, QListWidget::item:selected,
    QTreeWidget::item:selected {{
        background: {p.sel_bg}; color: {p.sel_fg};
    }}
    QHeaderView::section {{
        background: {p.panel_alt}; color: {p.text_dim};
        padding: 5px 8px; border: 0; border-right: 1px solid {p.border};
        font-weight: 600;
    }}
    QTextEdit, QPlainTextEdit {{
        background: {p.panel}; color: {p.text};
        border: 1px solid {p.border}; border-radius: 6px;
        selection-background-color: {p.sel_bg};
        selection-color: {p.sel_fg};
    }}
    QTextEdit#Flat {{ background: {p.window}; border: 0; }}

    QGroupBox {{
        border: 1px solid {p.border}; border-radius: 6px;
        margin-top: 10px; padding-top: 6px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 10px; padding: 0 4px;
        color: {p.text_dim};
    }}

    QSplitter::handle {{ background: {p.window}; }}
    QScrollBar:vertical {{ background: {p.window}; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {p.panel_alt}; border-radius: 6px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.border}; }}
    QScrollBar:horizontal {{ background: {p.window}; height: 12px; }}
    QScrollBar::handle:horizontal {{
        background: {p.panel_alt}; border-radius: 6px; min-width: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
