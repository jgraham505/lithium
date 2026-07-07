"""Native OpenCASCADE OpenGL viewer (pythonocc's qtViewer3d) wrapper.

Pure Python: ``qtViewer3d`` is pythonocc-core's Qt widget around the OCC
C++ viewer, driven here through its Python API only.  Gives hardware-
accelerated shading, exact B-rep rendering (no app-side tessellation), and
kernel-native sub-shape selection used for measurement picking.

Import and driver initialization can both fail (no pythonocc, no usable
OpenGL); every entry point degrades gracefully so the caller can fall back
to the QPainter viewer.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

_qtViewer3d = None
_IMPORT_ERROR = ""


def _load_viewer_class():
    """Import qtViewer3d with the PySide6 backend; cache the class."""
    global _qtViewer3d, _IMPORT_ERROR
    if _qtViewer3d is not None or _IMPORT_ERROR:
        return _qtViewer3d
    try:
        from OCC.Display import backend
        try:
            backend.load_backend("pyside6")
        except Exception:
            pass                      # already loaded is fine
        from OCC.Display.qtDisplay import qtViewer3d
        _qtViewer3d = qtViewer3d
    except Exception as e:            # pragma: no cover - env dependent
        _IMPORT_ERROR = f"{type(e).__name__}: {e}"
    return _qtViewer3d


def available() -> bool:
    return _load_viewer_class() is not None


def import_error() -> str:
    _load_viewer_class()
    return _IMPORT_ERROR


def _hex_rgb(color: str):
    c = color.lstrip("#")
    return [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)]


def _qcolor(color) -> "object":
    """Palette color (hex str or 0-255 tuple) -> OCC Quantity_Color.

    Uses the sRGB color space: the viewer renders into an sRGB framebuffer,
    so linear-RGB input would come out washed out.
    """
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_sRGB
    rgb = _hex_rgb(color) if isinstance(color, str) else list(color)
    return Quantity_Color(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0,
                          Quantity_TOC_sRGB)


class NativeViewer(QtWidgets.QWidget):
    """Embeds qtViewer3d; emits picked TopoDS sub-shapes for measurement."""

    selection_made = QtCore.Signal(object)      # TopoDS_Shape

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.pal = palette
        self.ready = False
        self.failed = ""
        self._ais_shapes = []          # displayed model AIS handles
        self._overlay = []             # measurement overlay AIS handles
        self._shape = None
        self._measuring = False
        self._pick_filter = "auto"
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        cls = _load_viewer_class()
        if cls is None:
            self.failed = import_error()
            lay.addWidget(QtWidgets.QLabel(
                "native viewer unavailable: " + self.failed))
            return
        self.canvas = cls(self)
        lay.addWidget(self.canvas)

    # -- lifecycle ------------------------------------------------------------
    def ensure_ready(self) -> bool:
        """Initialize the GL driver on first use; returns success."""
        if self.ready or self.failed:
            return self.ready
        try:
            self.canvas.InitDriver()
            self.display = self.canvas._display
            self.display.register_select_callback(self._on_select)
            self.ready = True
            self.apply_palette(self.pal)
        except Exception as e:         # no GL context available
            self.failed = str(e)
        return self.ready

    # -- content ----------------------------------------------------------------
    def show_shape(self, shape) -> bool:
        if not self.ensure_ready():
            return False
        self._shape = shape
        self.display.EraseAll()
        self._overlay.clear()
        try:
            ais = self.display.DisplayShape(
                shape, color=_qcolor(self.pal.surface), update=False)
            self._ais_shapes = ais if isinstance(ais, list) else [ais]
        except Exception as e:
            self.failed = f"display failed: {e}"
            return False
        self.display.View_Iso()
        self.display.FitAll()
        self._apply_pick_modes()
        return True

    def fit(self):
        if self.ready:
            self.display.FitAll()

    def apply_palette(self, pal):
        self.pal = pal
        if not self.ready:
            return
        try:
            c = _qcolor(pal.canvas_bg)
            try:
                from OCC.Core.Aspect import Aspect_GradientFillMethod_Vertical
                fill = Aspect_GradientFillMethod_Vertical
            except ImportError:      # older enum spelling
                from OCC.Core.Aspect import Aspect_GFM_VER as fill
            self.display.View.SetBgGradientColors(c, c, fill, False)
            # re-color the displayed model to the theme surface tone
            if self._shape is not None and self._ais_shapes:
                ctx = self.display.Context
                col = _qcolor(pal.surface)
                for ais in self._ais_shapes:
                    ctx.SetColor(ais, col, False)
            self.display.Repaint()
        except Exception:
            pass

    # -- measurement picking -----------------------------------------------------
    def set_measuring(self, on: bool):
        self._measuring = bool(on)
        self._apply_pick_modes()

    def set_pick_filter(self, f: str):
        self._pick_filter = f
        self._apply_pick_modes()

    def _apply_pick_modes(self):
        if not self.ready:
            return
        try:
            from OCC.Core.AIS import AIS_Shape
            from OCC.Core.TopAbs import (TopAbs_VERTEX, TopAbs_EDGE,
                                         TopAbs_FACE)
            ctx = self.display.Context
            wanted = {"auto": (TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE),
                      "vertex": (TopAbs_VERTEX,),
                      "edge": (TopAbs_EDGE,),
                      "face": (TopAbs_FACE,)}.get(self._pick_filter, ())
            for ais in self._ais_shapes:
                ctx.Deactivate(ais)
                if self._measuring:
                    for t in wanted:
                        ctx.Activate(ais, AIS_Shape.SelectionMode(t))
                else:
                    ctx.Activate(ais)          # whole-shape (default)
        except Exception:
            pass

    def _on_select(self, shapes, *_xy):
        if self._measuring and shapes:
            self.selection_made.emit(shapes[0])

    # -- measurement overlay -------------------------------------------------------
    def show_measurement(self, result):
        """Draw the closest-point connector + distance label in-scene."""
        if not self.ready:
            return
        self.clear_overlay(repaint=False)
        try:
            from OCC.Core.gp import gp_Pnt
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
            p1 = gp_Pnt(*result.p1)
            p2 = gp_Pnt(*result.p2)
            if result.distance > 1e-9:
                edge = BRepBuilderAPI_MakeEdge(p1, p2).Edge()
                ais = self.display.DisplayShape(
                    edge, color=_qcolor(self.pal.accent), update=False)
                self._overlay.extend(ais if isinstance(ais, list) else [ais])
            mid = gp_Pnt((result.p1[0] + result.p2[0]) / 2,
                         (result.p1[1] + result.p2[1]) / 2,
                         (result.p1[2] + result.p2[2]) / 2)
            label = f"{result.distance:.4g}"
            if result.angle is not None:
                label += f"  ∠{result.angle:.4g}°"
            # pre-compensate for the sRGB framebuffer (message uses linear)
            lin = tuple((v / 255.0) ** 2.2 for v in _hex_rgb(self.pal.accent))
            self.display.DisplayMessage(mid, label, height=22,
                                        message_color=lin)
            self.display.Repaint()
        except Exception:
            pass

    def clear_overlay(self, repaint=True):
        if not self.ready:
            return
        try:
            ctx = self.display.Context
            for ais in self._overlay:
                ctx.Remove(ais, False)
            self._overlay.clear()
            # DisplayMessage labels are unmanaged; wipe + redisplay the model
            self.display.EraseAll()
            if self._shape is not None:
                ais = self.display.DisplayShape(
                    self._shape, color=_qcolor(self.pal.surface),
                    update=False)
                self._ais_shapes = ais if isinstance(ais, list) else [ais]
                self._apply_pick_modes()
            if repaint:
                self.display.Repaint()
        except Exception:
            pass

    def screenshot(self, path: str) -> bool:
        if not self.ready:
            return False
        try:
            self.display.View.Dump(path)
            return True
        except Exception:
            return False
