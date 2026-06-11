"""Minimal dependency-free 3D wireframe viewer on a Tk canvas.

Orthographic projection with mouse orbit (left drag), pan (right/middle
drag) and zoom (wheel).  Designed for reviewing part geometry, not for
photorealism.
"""

from __future__ import annotations

import math
import tkinter as tk

from ..geometry import WireModel

KIND_COLORS = {
    "line": "#9ecbff",
    "circle": "#ffd47f",
    "ellipse": "#ffd47f",
    "spline": "#b8f0a8",
    "polyline": "#f3a6ff",
    "tess": "#8fd6c8",
    "approx": "#ff8f8f",
}

KIND_LABELS = {
    "line": "lines",
    "circle": "circles",
    "ellipse": "ellipses",
    "spline": "splines",
    "polyline": "polylines",
    "tess": "tessellation",
    "approx": "approximated (chord)",
}

MAX_SEGMENTS = 150_000   # hard cap to keep the canvas responsive


class Viewer3D(tk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, bg="#16181d", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.model: WireModel | None = None
        self.show = {"edges": True, "vertices": True, "points": True,
                     "axes": True}
        # view state
        self.yaw = math.radians(-35.0)
        self.pitch = math.radians(22.0)
        self.scale = 1.0
        self.panx = 0.0
        self.pany = 0.0
        self.center = (0.0, 0.0, 0.0)
        self.fit_scale = 1.0
        self._drag = None
        self.truncated = False

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", lambda e: self._motion(e, "orbit"))
        self.canvas.bind("<ButtonPress-3>", self._press)
        self.canvas.bind("<B3-Motion>", lambda e: self._motion(e, "pan"))
        self.canvas.bind("<ButtonPress-2>", self._press)
        self.canvas.bind("<B2-Motion>", lambda e: self._motion(e, "pan"))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(1.15))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.15))
        self.canvas.bind("<Configure>", lambda e: self.redraw())

    # -- public API ---------------------------------------------------------
    def set_model(self, model: WireModel) -> None:
        self.model = model
        self.fit()

    def set_visible(self, layer: str, on: bool) -> None:
        self.show[layer] = bool(on)
        self.redraw()

    def fit(self) -> None:
        self.panx = self.pany = 0.0
        self.scale = 1.0
        b = self.model.bounds() if self.model else None
        if b:
            (x0, y0, z0), (x1, y1, z1) = b
            self.center = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        else:
            self.center = (0.0, 0.0, 0.0)
        self.redraw()

    def _update_fit_scale(self) -> None:
        """Scale so the model diagonal fits the current canvas size."""
        b = self.model.bounds() if self.model else None
        if not b:
            self.fit_scale = 1.0
            return
        (x0, y0, z0), (x1, y1, z1) = b
        diag = math.dist((x0, y0, z0), (x1, y1, z1)) or 1.0
        w = max(self.canvas.winfo_width(), 50)
        h = max(self.canvas.winfo_height(), 50)
        self.fit_scale = 0.8 * min(w, h) / diag

    # -- interaction ---------------------------------------------------------
    def _press(self, ev):
        self._drag = (ev.x, ev.y)

    def _motion(self, ev, mode):
        if self._drag is None:
            self._drag = (ev.x, ev.y)
            return
        dx = ev.x - self._drag[0]
        dy = ev.y - self._drag[1]
        self._drag = (ev.x, ev.y)
        if mode == "orbit":
            self.yaw += dx * 0.01
            self.pitch = max(-1.55, min(1.55, self.pitch + dy * 0.01))
        else:
            self.panx += dx
            self.pany += dy
        self.redraw()

    def _wheel(self, ev):
        self._zoom(1.15 if ev.delta > 0 else 1 / 1.15)

    def _zoom(self, f):
        self.scale = max(1e-4, min(1e5, self.scale * f))
        self.redraw()

    # -- projection -----------------------------------------------------------
    def _proj(self):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        s = self.fit_scale * self.scale
        cx0, cy0, cz0 = self.center
        w2 = self.canvas.winfo_width() / 2 + self.panx
        h2 = self.canvas.winfo_height() / 2 + self.pany

        def project(p):
            x, y, z = p[0] - cx0, p[1] - cy0, p[2] - cz0
            # yaw about Z, then pitch about screen X; Z up on screen
            x1 = x * cy - y * sy
            y1 = x * sy + y * cy
            y2 = y1 * cp - z * sp
            z2 = y1 * sp + z * cp
            return (w2 + x1 * s, h2 - z2 * s, y2)
        return project

    # -- drawing -----------------------------------------------------------
    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        self._update_fit_scale()
        project = self._proj()
        if self.show["axes"]:
            self._axes(project)
        if self.model is None:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2,
                          text="No geometry loaded", fill="#777",
                          font=("TkDefaultFont", 11))
            return
        m = self.model
        segs = 0
        self.truncated = False
        if self.show["edges"]:
            for pl in m.polylines:
                color = KIND_COLORS.get(pl.kind, "#cccccc")
                pts = [project(p) for p in pl.points]
                flat = []
                for px, py, _ in pts:
                    flat.extend((px, py))
                if len(flat) >= 4:
                    dash = (4, 3) if pl.kind == "approx" else None
                    c.create_line(*flat, fill=color, width=1, dash=dash)
                segs += len(pts)
                if segs > MAX_SEGMENTS:
                    self.truncated = True
                    break
        if self.show["vertices"]:
            seen = set()
            for p, _eid in m.vertex_points:
                key = (round(p[0], 9), round(p[1], 9), round(p[2], 9))
                if key in seen:
                    continue
                seen.add(key)
                px, py, _ = project(p)
                c.create_oval(px - 2, py - 2, px + 2, py + 2,
                              fill="#ffffff", outline="")
        if self.show["points"]:
            for p, _eid in m.free_points[:20000]:
                px, py, _ = project(p)
                c.create_rectangle(px - 1, py - 1, px + 1, py + 1,
                                   fill="#ffe97f", outline="")
        if m.empty:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2,
                          text="No drawable wireframe geometry in this file",
                          fill="#888", font=("TkDefaultFont", 11))
        if self.truncated:
            c.create_text(8, 8, anchor="nw", fill="#ff9d6b",
                          text="Display truncated (very large model)")

    def _axes(self, project) -> None:
        b = self.model.bounds() if self.model else None
        if b:
            (x0, y0, z0), (x1, y1, z1) = b
            L = 0.25 * max(x1 - x0, y1 - y0, z1 - z0, 1e-6)
            o = (x0, y0, z0)
        else:
            L, o = 1.0, (0.0, 0.0, 0.0)
        for axis, color, label in (((L, 0, 0), "#e06c60", "X"),
                                   ((0, L, 0), "#7bc86c", "Y"),
                                   ((0, 0, L), "#6c9fe0", "Z")):
            ox, oy, _ = project(o)
            px, py, _ = project((o[0] + axis[0], o[1] + axis[1],
                                 o[2] + axis[2]))
            self.canvas.create_line(ox, oy, px, py, fill=color, width=2)
            self.canvas.create_text(px + 6, py, text=label, fill=color,
                                    font=("TkDefaultFont", 9, "bold"))
