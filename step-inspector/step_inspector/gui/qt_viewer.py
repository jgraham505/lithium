"""Qt (PySide6) 3D wireframe / shaded viewer.

A single QWidget that renders both the NumPy wireframe model and the
OpenCASCADE shaded mesh with QPainter.  Orthographic projection with mouse
orbit (left drag), pan (right/middle drag) and zoom (wheel).  The whole
point set is projected with one vectorized NumPy matmul per paint.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .theme import LIGHT, Palette

KIND_LABELS = {
    "line": "lines", "circle": "circles", "ellipse": "ellipses",
    "spline": "splines", "polyline": "polylines", "tess": "tessellation",
    "approx": "approximated (chord)",
}

MAX_VERTICES = 400_000     # wireframe vertex cap
MAX_TRIANGLES = 200_000    # shaded triangle cap


class Viewer3D(QtWidgets.QWidget):
    def __init__(self, palette: Palette = LIGHT, parent=None):
        super().__init__(parent)
        self.pal = palette
        self.model = None              # geometry.WireModel
        self.mesh = None               # occ_backend.ShadedMesh
        self.mode = "wire"             # "wire" | "shaded"
        self.show = {"edges": True, "vertices": True, "points": True,
                     "axes": True}
        self.yaw = math.radians(-35.0)
        self.pitch = math.radians(22.0)
        self.scale = 1.0
        self.panx = 0.0
        self.pany = 0.0
        self.center = np.zeros(3)
        self.fit_scale = 1.0
        self._drag = None
        self._truncated = False
        self.setMinimumSize(360, 280)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    # -- public API ---------------------------------------------------------
    def set_model(self, model):
        self.model = model
        self.fit()

    def set_mesh(self, mesh):
        self.mesh = mesh

    def set_mode(self, mode):
        self.mode = mode
        self.fit()

    def set_visible(self, layer, on):
        self.show[layer] = bool(on)
        self.update()

    def apply_palette(self, pal):
        self.pal = pal
        self.update()

    def _active_bounds(self):
        if self.mode == "shaded" and self.mesh is not None \
                and not self.mesh.empty:
            return self.mesh.bounds()
        return self.model.bounds() if self.model is not None else None

    def fit(self):
        self.panx = self.pany = 0.0
        self.scale = 1.0
        b = self._active_bounds()
        if b:
            (x0, y0, z0), (x1, y1, z1) = b
            self.center = np.array([(x0 + x1) / 2, (y0 + y1) / 2,
                                    (z0 + z1) / 2])
        else:
            self.center = np.zeros(3)
        self.update()

    # -- interaction --------------------------------------------------------
    def mousePressEvent(self, ev):
        self._drag = (ev.position().x(), ev.position().y())

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            return
        x, y = ev.position().x(), ev.position().y()
        dx, dy = x - self._drag[0], y - self._drag[1]
        self._drag = (x, y)
        if ev.buttons() & QtCore.Qt.LeftButton:
            self.yaw += dx * 0.01
            self.pitch = max(-1.55, min(1.55, self.pitch + dy * 0.01))
        elif ev.buttons() & (QtCore.Qt.RightButton | QtCore.Qt.MiddleButton):
            self.panx += dx
            self.pany += dy
        self.update()

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def wheelEvent(self, ev):
        f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.scale = max(1e-4, min(1e5, self.scale * f))
        self.update()

    # -- projection ---------------------------------------------------------
    def _rotation(self):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return np.array([
            [cy,       -sy,       0.0],
            [-sy * sp, -cy * sp,  -cp],
            [sy * cp,  cy * cp,   -sp],
        ])

    def _update_fit_scale(self):
        b = self._active_bounds()
        if not b:
            self.fit_scale = 1.0
            return
        (x0, y0, z0), (x1, y1, z1) = b
        diag = math.dist((x0, y0, z0), (x1, y1, z1)) or 1.0
        w = max(self.width(), 50)
        h = max(self.height(), 50)
        self.fit_scale = 0.8 * min(w, h) / diag

    def _project(self, pts):
        R = self._rotation()
        s = self.fit_scale * self.scale
        M = R * np.array([[s], [s], [1.0]])
        out = (pts - self.center) @ M.T
        out[:, 0] += self.width() / 2 + self.panx
        out[:, 1] += self.height() / 2 + self.pany
        return out

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        qp.fillRect(self.rect(), QtGui.QColor(self.pal.canvas_bg))
        self._update_fit_scale()
        if self.show["axes"]:
            self._draw_axes(qp)
        if self.mode == "shaded":
            self._paint_shaded(qp)
        else:
            self._paint_wire(qp)
        qp.end()

    def _center_text(self, qp, text):
        qp.setPen(QtGui.QColor(self.pal.text_dim))
        qp.drawText(self.rect(), QtCore.Qt.AlignCenter, text)

    def _paint_wire(self, qp):
        m = self.model
        if m is None:
            self._center_text(qp, "No geometry loaded")
            return
        kc = self.pal.kind_colors()
        segs = 0
        if self.show["edges"]:
            for pl in m.polylines:
                if len(pl.points) < 2:
                    continue
                scr = self._project(pl.points)
                pen = QtGui.QPen(QtGui.QColor(kc.get(pl.kind, "#cccccc")))
                pen.setWidthF(1.2)
                if pl.kind == "approx":
                    pen.setStyle(QtCore.Qt.DashLine)
                qp.setPen(pen)
                poly = QtGui.QPolygonF(
                    [QtCore.QPointF(x, y) for x, y in scr[:, :2]])
                qp.drawPolyline(poly)
                segs += len(pl.points)
                if segs > MAX_VERTICES:
                    break
        if self.show["vertices"] and m.vertex_points:
            pts = np.unique(np.array([p for p, _ in m.vertex_points]).round(9),
                            axis=0)
            scr = self._project(pts)
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(self.pal.vertex))
            for x, y in scr[:, :2]:
                qp.drawEllipse(QtCore.QPointF(x, y), 2.4, 2.4)
        if self.show["points"] and m.free_points:
            pts = np.array([p for p, _ in m.free_points[:20000]])
            scr = self._project(pts)
            qp.setBrush(QtGui.QColor(self.pal.free_point))
            for x, y in scr[:, :2]:
                qp.drawRect(QtCore.QRectF(x - 1.4, y - 1.4, 2.8, 2.8))
        if m.empty:
            self._center_text(qp, "No drawable wireframe geometry in this file")

    def _paint_shaded(self, qp):
        mesh = self.mesh
        if mesh is None:
            self._center_text(qp, "Shaded view: OpenCASCADE backend not "
                                  "loaded.\nInstall pythonocc-core to enable "
                                  "it.")
            return
        if mesh.empty:
            self._center_text(qp, "OpenCASCADE produced no surface mesh for "
                                  "this file.")
            return
        scr = self._project(mesh.vertices)
        tris = mesh.triangles
        depth = scr[tris, 2].mean(axis=1)
        order = np.argsort(-depth)
        truncated = False
        if len(order) > MAX_TRIANGLES:
            order = order[-MAX_TRIANGLES:]
            truncated = True
        R = self._rotation()
        nv = mesh.normals @ R.T
        light = np.array([-0.35, -0.5, -0.78])
        light /= np.linalg.norm(light)
        amb = 0.34
        inten = amb + (1 - amb) * np.abs(nv @ light)
        base = np.array(self.pal.surface, dtype=float)
        rgb = np.clip(inten[:, None] * base, 0, 255).astype(int)
        xy = scr[:, :2]
        small = len(tris) <= 6000
        edge = QtGui.QColor(self.pal.surface_edge)
        for idx in order:
            t = tris[idx]
            r, g, b = rgb[idx]
            col = QtGui.QColor(int(r), int(g), int(b))
            qp.setBrush(col)
            qp.setPen(QtGui.QPen(edge, 0.6) if small
                      else QtGui.QPen(col, 0.0))
            qp.drawConvexPolygon(QtGui.QPolygonF([
                QtCore.QPointF(xy[t[0]][0], xy[t[0]][1]),
                QtCore.QPointF(xy[t[1]][0], xy[t[1]][1]),
                QtCore.QPointF(xy[t[2]][0], xy[t[2]][1])]))
        if truncated:
            qp.setPen(QtGui.QColor(self.pal.hud))
            qp.drawText(10, 20, f"Showing nearest {MAX_TRIANGLES:,} of "
                                f"{len(tris):,} triangles")

    def _draw_axes(self, qp):
        b = self._active_bounds()
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
                ((self.pal.axis_x, "X"), (self.pal.axis_y, "Y"),
                 (self.pal.axis_z, "Z")), 1):
            px, py = scr[i]
            pen = QtGui.QPen(QtGui.QColor(color))
            pen.setWidthF(2.0)
            qp.setPen(pen)
            qp.drawLine(QtCore.QPointF(ox, oy), QtCore.QPointF(px, py))
            qp.drawText(QtCore.QPointF(px + 6, py + 4), label)
