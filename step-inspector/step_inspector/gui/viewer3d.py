"""3D wireframe viewer on a Tk canvas.

Orthographic projection with mouse orbit (left drag), pan (right/middle
drag) and zoom (wheel).  The whole point cloud is projected with a single
vectorized NumPy matrix multiply per redraw, so even large models stay
responsive.  Designed for reviewing part geometry, not photorealism.
"""

from __future__ import annotations

import math
import tkinter as tk

import numpy as np

from ..geometry import WireModel
from .theme import LIGHT, Palette

KIND_LABELS = {
    "line": "lines",
    "circle": "circles",
    "ellipse": "ellipses",
    "spline": "splines",
    "polyline": "polylines",
    "tess": "tessellation",
    "approx": "approximated (chord)",
}

MAX_SEGMENTS = 200_000   # hard cap on drawn vertices to stay responsive


class Viewer3D(tk.Frame):
    def __init__(self, master, palette: Palette = LIGHT, **kw):
        super().__init__(master, **kw)
        self.pal = palette
        self.canvas = tk.Canvas(self, bg=palette.canvas_bg,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.model: WireModel | None = None
        self.mesh = None                 # occ_backend.ShadedMesh or None
        self.mode = "wire"               # "wire" | "shaded"
        self.show = {"edges": True, "vertices": True, "points": True,
                     "axes": True}
        # view state
        self.yaw = math.radians(-35.0)
        self.pitch = math.radians(22.0)
        self.scale = 1.0
        self.panx = 0.0
        self.pany = 0.0
        self.center = np.zeros(3)
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

    def set_mesh(self, mesh) -> None:
        self.mesh = mesh

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.fit()

    def set_visible(self, layer: str, on: bool) -> None:
        self.show[layer] = bool(on)
        self.redraw()

    def apply_palette(self, palette: Palette) -> None:
        self.pal = palette
        self.canvas.configure(bg=palette.canvas_bg)
        self.redraw()

    def _active_bounds(self):
        if self.mode == "shaded" and self.mesh is not None \
                and not self.mesh.empty:
            return self.mesh.bounds()
        return self.model.bounds() if self.model else None

    def fit(self) -> None:
        self.panx = self.pany = 0.0
        self.scale = 1.0
        b = self._active_bounds()
        if b:
            (x0, y0, z0), (x1, y1, z1) = b
            self.center = np.array([(x0 + x1) / 2, (y0 + y1) / 2,
                                    (z0 + z1) / 2])
        else:
            self.center = np.zeros(3)
        self.redraw()

    def _update_fit_scale(self) -> None:
        b = self._active_bounds()
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
    def _rotation(self):
        """Unit rotation mapping world -> (screen_x, screen_y_down, depth)."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return np.array([
            [cy,        -sy,        0.0],     # screen x (right)
            [-sy * sp,  -cy * sp,   -cp],     # screen y (down)
            [sy * cp,   cy * cp,    -sp],     # depth (into screen)
        ])

    def _proj_matrix(self):
        """Return (M, offset) so screen_xy = points @ M.T + offset, plus a
        depth column.  Pure NumPy: projecting an (N,3) array is one matmul."""
        R = self._rotation()
        s = self.fit_scale * self.scale
        M = R * np.array([[s], [s], [1.0]])   # scale screen axes, not depth
        w2 = self.canvas.winfo_width() / 2 + self.panx
        h2 = self.canvas.winfo_height() / 2 + self.pany
        offset = np.array([w2, h2, 0.0])
        return M, offset

    def _project(self, pts: np.ndarray) -> np.ndarray:
        """pts: (N,3) world -> (N,3) where [:, :2] are screen px."""
        M, offset = self._proj_matrix()
        local = pts - self.center
        out = local @ M.T
        out[:, 0] += offset[0]
        out[:, 1] += offset[1]
        return out

    # -- drawing -----------------------------------------------------------
    MAX_TRIANGLES = 120_000   # painter's-algorithm cap for the Tk canvas

    def redraw(self) -> None:
        c = self.canvas
        pal = self.pal
        c.delete("all")
        self._update_fit_scale()
        if self.show["axes"]:
            self._axes()
        if self.mode == "shaded":
            self._redraw_shaded()
            return
        if self.model is None:
            self._center_text("No geometry loaded", pal.text_dim)
            return
        m = self.model
        kind_colors = pal.kind_colors()
        segs = 0
        self.truncated = False
        if self.show["edges"]:
            for pl in m.polylines:
                if len(pl.points) < 2:
                    continue
                color = kind_colors.get(pl.kind, "#cccccc")
                scr = self._project(pl.points)
                flat = scr[:, :2].ravel().tolist()
                dash = (4, 3) if pl.kind == "approx" else None
                c.create_line(*flat, fill=color, width=1, dash=dash)
                segs += len(pl.points)
                if segs > MAX_SEGMENTS:
                    self.truncated = True
                    break
        if self.show["vertices"] and m.vertex_points:
            pts = np.array([p for p, _ in m.vertex_points])
            pts = np.unique(pts.round(9), axis=0)
            scr = self._project(pts)
            for px, py in scr[:, :2]:
                c.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5,
                              fill=pal.vertex, outline=pal.canvas_bg)
        if self.show["points"] and m.free_points:
            pts = np.array([p for p, _ in m.free_points[:20000]])
            scr = self._project(pts)
            for px, py in scr[:, :2]:
                c.create_rectangle(px - 1.5, py - 1.5, px + 1.5, py + 1.5,
                                   fill=pal.free_point, outline="")
        if m.empty:
            self._center_text("No drawable wireframe geometry in this file",
                              pal.text_dim)
        if self.truncated:
            c.create_text(10, 10, anchor="nw", fill=pal.hud,
                          text="Display truncated (very large model)",
                          font=("TkDefaultFont", 9, "bold"))

    def _redraw_shaded(self) -> None:
        c = self.canvas
        pal = self.pal
        mesh = self.mesh
        if mesh is None:
            self._center_text("Shaded view: OpenCASCADE backend not loaded.\n"
                              "Install pythonocc-core to enable it.",
                              pal.text_dim)
            return
        if mesh.empty:
            self._center_text("OpenCASCADE produced no surface mesh for this "
                              "file.", pal.text_dim)
            return
        scr = self._project(mesh.vertices)
        tris = mesh.triangles
        depth = scr[tris, 2].mean(axis=1)
        order = np.argsort(-depth)                 # far first (painter's)
        truncated = False
        if len(order) > self.MAX_TRIANGLES:
            order = order[-self.MAX_TRIANGLES:]    # keep nearest
            truncated = True
        # flat shading: two-sided headlight
        R = self._rotation()
        nv = mesh.normals @ R.T
        light = np.array([-0.35, -0.5, -0.78])
        light /= np.linalg.norm(light)
        amb = 0.34
        inten = amb + (1 - amb) * np.abs(nv @ light)
        base = np.array(pal.surface, dtype=float)
        rgb = np.clip(inten[:, None] * base, 0, 255).astype(int)
        xy = scr[:, :2]
        edge = mesh.face_of_tri is not None and len(tris) <= 4000
        for idx in order:
            t = tris[idx]
            r, g, b = rgb[idx]
            color = f"#{r:02x}{g:02x}{b:02x}"
            coords = xy[t].ravel().tolist()
            c.create_polygon(*coords, fill=color,
                             outline=(pal.surface_edge if edge else color),
                             width=1)
        if truncated:
            c.create_text(10, 10, anchor="nw", fill=pal.hud,
                          text=f"Showing nearest {self.MAX_TRIANGLES:,} of "
                               f"{len(tris):,} triangles",
                          font=("TkDefaultFont", 9, "bold"))

    def _center_text(self, text, color):
        c = self.canvas
        c.create_text(c.winfo_width() / 2, c.winfo_height() / 2,
                      text=text, fill=color, font=("TkDefaultFont", 11))

    def _axes(self) -> None:
        pal = self.pal
        b = self.model.bounds() if self.model else None
        if b:
            (x0, y0, z0), (x1, y1, z1) = b
            L = 0.25 * max(x1 - x0, y1 - y0, z1 - z0, 1e-6)
            o = np.array([x0, y0, z0])
        else:
            L, o = 1.0, np.zeros(3)
        ends = np.array([o, o + [L, 0, 0], o + [0, L, 0], o + [0, 0, L]])
        scr = self._project(ends)[:, :2]
        ox, oy = scr[0]
        for i, (color, label) in enumerate(
                ((pal.axis_x, "X"), (pal.axis_y, "Y"), (pal.axis_z, "Z")), 1):
            px, py = scr[i]
            self.canvas.create_line(ox, oy, px, py, fill=color, width=2)
            self.canvas.create_text(px + 7, py, text=label, fill=color,
                                    font=("TkDefaultFont", 9, "bold"))
