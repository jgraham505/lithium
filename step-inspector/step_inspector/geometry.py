"""Wireframe geometry extraction from a parsed STEP entity graph.

This is intentionally schema-light: it walks the raw entity instances from
the audit parser and turns recognizable geometric entities into 3D polylines
and points for the viewer.  Entities whose exact curve geometry is not
supported are still shown — as straight chords between their vertices — and
counted, so nothing silently disappears.

Geometry is represented with NumPy: every polyline is an ``(N, 3)`` float
array and every point is a length-3 array, so sampling and the viewer's
projection are vectorized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .parser import AuditResult, Entity, Ref, Typed

Vec3 = np.ndarray            # shape (3,), float64

CURVE_SAMPLES = 48           # segments used when sampling circles/splines
_EMPTY = np.empty((0, 3), dtype=float)


@dataclass
class WirePolyline:
    points: np.ndarray       # (N, 3) float array
    eid: int                 # source entity id
    kind: str                # "line"|"circle"|"ellipse"|"spline"|"polyline"
                             # |"approx"|"tess"
    closed: bool = False


@dataclass
class WireModel:
    polylines: list = field(default_factory=list)       # [WirePolyline]
    vertex_points: list = field(default_factory=list)   # [(Vec3, eid)]
    free_points: list = field(default_factory=list)     # [(Vec3, eid)]
    unsupported: dict = field(default_factory=dict)     # type name -> count
    notes: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.polylines or self.vertex_points or self.free_points)

    def _all_arrays(self) -> list:
        arrays = [pl.points for pl in self.polylines if len(pl.points)]
        if self.vertex_points:
            arrays.append(np.array([p for p, _ in self.vertex_points]))
        if self.free_points:
            arrays.append(np.array([p for p, _ in self.free_points]))
        return arrays

    def bounds(self) -> Optional[tuple]:
        arrays = self._all_arrays()
        if not arrays:
            return None
        allp = np.vstack(arrays)
        lo = allp.min(axis=0)
        hi = allp.max(axis=0)
        return ((float(lo[0]), float(lo[1]), float(lo[2])),
                (float(hi[0]), float(hi[1]), float(hi[2])))


# -- small vector helpers ----------------------------------------------------

def _v(x, y, z) -> Vec3:
    return np.array([float(x), float(y), float(z)])


def _norm(a: Vec3) -> Vec3:
    n = float(np.linalg.norm(a))
    return a / n if n else np.array([0.0, 0.0, 1.0])


class _Graph:
    """Attribute access helpers over the parsed entity dict."""

    def __init__(self, res: AuditResult):
        self.res = res
        self.ents = res.entities

    def deref(self, v) -> Optional[Entity]:
        if isinstance(v, Ref):
            return self.ents.get(v.eid)
        return None

    def params_of(self, ent: Entity, type_name: str) -> Optional[list]:
        """Parameters of the given leaf type (handles complex instances)."""
        rec = ent.record(type_name)
        if rec is not None and rec.params is not None:
            return rec.params
        return None

    def leaf_names(self, ent: Entity) -> list:
        return [r.type_name for r in ent.records]

    def has_type(self, ent: Entity, *names: str) -> bool:
        leaf = set(self.leaf_names(ent))
        return any(n in leaf for n in names)

    # -- coordinate primitives -------------------------------------------
    def _triple(self, raw) -> Optional[Vec3]:
        c = [float(x) for x in raw if isinstance(x, (int, float))]
        if not c:
            return None
        while len(c) < 3:
            c.append(0.0)
        return np.array(c[:3])

    def point(self, v) -> Optional[Vec3]:
        ent = self.deref(v)
        if ent is None:
            return None
        p = self.params_of(ent, "CARTESIAN_POINT")
        if p is None or len(p) < 2 or not isinstance(p[1], list):
            return None
        return self._triple(p[1])

    def direction(self, v) -> Optional[Vec3]:
        ent = self.deref(v)
        if ent is None:
            return None
        p = self.params_of(ent, "DIRECTION")
        if p is None or len(p) < 2 or not isinstance(p[1], list):
            return None
        t = self._triple(p[1])
        return _norm(t) if t is not None else None

    def vertex(self, v) -> Optional[Vec3]:
        ent = self.deref(v)
        if ent is None:
            return None
        p = self.params_of(ent, "VERTEX_POINT")
        if p is None or len(p) < 2:
            return None
        return self.point(p[1])

    def placement(self, v):
        """AXIS2_PLACEMENT_3D -> (origin, zaxis, xaxis, yaxis)."""
        ent = self.deref(v)
        if ent is None:
            return None
        p = self.params_of(ent, "AXIS2_PLACEMENT_3D")
        if p is None or len(p) < 2:
            return None
        origin = self.point(p[1])
        if origin is None:
            origin = _v(0, 0, 0)
        z = self.direction(p[2]) if len(p) > 2 else None
        x = self.direction(p[3]) if len(p) > 3 else None
        if z is None:
            z = _v(0, 0, 1)
        if x is None:
            seed = _v(1, 0, 0) if abs(z[0]) < 0.9 else _v(0, 1, 0)
            x = _norm(np.cross(seed, z))
        # re-orthogonalize x against z
        x = _norm(x - z * float(np.dot(x, z)))
        y = np.cross(z, x)
        return origin, z, x, y


def extract_wireframe(res: AuditResult) -> WireModel:
    g = _Graph(res)
    model = WireModel()
    used_points: set[int] = set()       # CARTESIAN_POINT eids drawn somewhere
    drawn_curves: set[int] = set()      # curve eids drawn via an EDGE_CURVE

    def mark_point_used(v) -> None:
        if isinstance(v, Ref):
            used_points.add(v.eid)

    # ---- pass 1: edges (the primary wireframe source) --------------------
    for eid, ent in res.entities.items():
        p = g.params_of(ent, "EDGE_CURVE")
        if p is None or len(p) < 4:
            continue
        v1 = g.vertex(p[1])
        v2 = g.vertex(p[2])
        curve_ent = g.deref(p[3])
        pts, kind = _sample_curve(g, curve_ent, v1, v2)
        if curve_ent is not None:
            drawn_curves.add(curve_ent.eid)
            _mark_curve_points(g, curve_ent, used_points)
        for vref in (p[1], p[2]):
            vent = g.deref(vref)
            if vent is not None:
                vp = g.params_of(vent, "VERTEX_POINT")
                if vp and len(vp) > 1:
                    mark_point_used(vp[1])
        if len(pts) >= 2:
            model.polylines.append(WirePolyline(pts, eid, kind))
        if v1 is not None:
            model.vertex_points.append((v1, eid))
        if v2 is not None:
            model.vertex_points.append((v2, eid))

    # ---- pass 2: standalone drawable curves ------------------------------
    for eid, ent in res.entities.items():
        if eid in drawn_curves:
            continue
        leafs = set(g.leaf_names(ent))
        if "POLYLINE" in leafs:
            p = g.params_of(ent, "POLYLINE")
            if p and len(p) > 1 and isinstance(p[1], list):
                pts = _stack(g.point(r) for r in p[1])
                for r in p[1]:
                    mark_point_used(r)
                if len(pts) >= 2:
                    model.polylines.append(WirePolyline(pts, eid, "polyline"))
            continue
        if "CIRCLE" in leafs or "ELLIPSE" in leafs:
            pts, kind = _sample_curve(g, ent, None, None)
            _mark_curve_points(g, ent, used_points)
            if len(pts):
                model.polylines.append(
                    WirePolyline(pts, eid, kind, closed=True))
            continue
        if "TRIMMED_CURVE" in leafs:
            pts, kind = _sample_curve(g, ent, None, None)
            _mark_curve_points(g, ent, used_points)
            if len(pts) >= 2:
                model.polylines.append(WirePolyline(pts, eid, kind))
            continue
        if leafs & {"B_SPLINE_CURVE_WITH_KNOTS", "B_SPLINE_CURVE",
                    "BEZIER_CURVE", "QUASI_UNIFORM_CURVE", "UNIFORM_CURVE"}:
            pts, kind = _sample_curve(g, ent, None, None)
            _mark_curve_points(g, ent, used_points)
            if len(pts) >= 2:
                model.polylines.append(WirePolyline(pts, eid, kind))
            continue

    # ---- pass 3: tessellated geometry ------------------------------------
    for eid, ent in res.entities.items():
        leafs = set(g.leaf_names(ent))
        tri = leafs & {"TRIANGULATED_FACE_SET", "TRIANGULATED_SURFACE_SET",
                       "COMPLEX_TRIANGULATED_FACE_SET",
                       "COMPLEX_TRIANGULATED_SURFACE_SET"}
        strips = leafs & {"TESSELLATED_CURVE_SET", "TESSELLATED_GEOMETRIC_SET",
                          "TESSELLATED_WIRE", "TESSELLATED_EDGE"}
        if not (tri or strips):
            continue
        _extract_tessellation(g, ent, model,
                              mode="strips" if strips else "triangles")

    # ---- pass 4: free points ---------------------------------------------
    for eid, ent in res.entities.items():
        if ent.record("CARTESIAN_POINT") is None or eid in used_points:
            continue
        # only show points that are not internal curve/placement machinery
        refs = res.referenced_by.get(eid, set())
        internal = False
        for r in refs:
            rent = res.entities.get(r)
            if rent and set(g.leaf_names(rent)) & {
                    "AXIS2_PLACEMENT_3D", "AXIS1_PLACEMENT", "LINE",
                    "AXIS2_PLACEMENT_2D"}:
                internal = True
                break
        if internal:
            continue
        pt = g.point(Ref(eid))
        if pt is not None:
            model.free_points.append((pt, eid))

    # ---- unsupported curve bookkeeping ------------------------------------
    if model.empty:
        model.notes.append(
            "No recognizable wireframe geometry found in this file.")
    return model


def _stack(points) -> np.ndarray:
    """Stack an iterable of optional Vec3 into an (N, 3) array."""
    rows = [p for p in points if p is not None]
    return np.array(rows) if rows else _EMPTY


def _mark_curve_points(g: _Graph, ent: Entity, used: set) -> None:
    for rec in ent.records:
        for r in _walk_refs(rec.params or []):
            tgt = g.ents.get(r.eid)
            if tgt is not None and tgt.record("CARTESIAN_POINT") is not None:
                used.add(r.eid)
            elif tgt is not None:
                for r2 in _walk_refs(_all_params(tgt)):
                    tgt2 = g.ents.get(r2.eid)
                    if tgt2 is not None and tgt2.record("CARTESIAN_POINT"):
                        used.add(r2.eid)


def _all_params(ent: Entity) -> list:
    out = []
    for rec in ent.records:
        out.extend(rec.params or [])
    return out


def _walk_refs(values):
    for v in values:
        if isinstance(v, Ref):
            yield v
        elif isinstance(v, list):
            yield from _walk_refs(v)
        elif isinstance(v, Typed):
            yield from _walk_refs([v.value])


# ---------------------------------------------------------------------------
# Curve sampling
# ---------------------------------------------------------------------------

def _sample_curve(g: _Graph, curve: Optional[Entity],
                  v1: Optional[Vec3], v2: Optional[Vec3]):
    """Return (points, kind) for a curve entity between optional vertices.

    ``points`` is always an ``(N, 3)`` array (possibly empty).
    """
    if curve is None:
        return _chord(v1, v2)
    leafs = set(r.type_name for r in curve.records)

    if "LINE" in leafs:
        if v1 is not None and v2 is not None:
            return np.array([v1, v2]), "line"
        p = g.params_of(curve, "LINE")
        if p and len(p) > 2:
            origin = g.point(p[1])
            vent = g.deref(p[2])
            if origin is not None and vent is not None:
                vp = g.params_of(vent, "VECTOR")
                if vp and len(vp) > 2:
                    d = g.direction(vp[1])
                    mag = vp[2] if isinstance(vp[2], (int, float)) else 1.0
                    if d is not None:
                        return np.array([origin, origin + d * float(mag)]), \
                            "line"
        return _EMPTY, "approx"

    if "CIRCLE" in leafs or "ELLIPSE" in leafs:
        name = "CIRCLE" if "CIRCLE" in leafs else "ELLIPSE"
        p = g.params_of(curve, name)
        if not p or len(p) < 3:
            return _chord(v1, v2)
        plc = g.placement(p[1])
        if plc is None:
            return _chord(v1, v2)
        origin, z, x, y = plc
        if name == "CIRCLE":
            r1 = r2 = float(p[2]) if isinstance(p[2], (int, float)) else 1.0
        else:
            r1 = float(p[2]) if isinstance(p[2], (int, float)) else 1.0
            r2 = float(p[3]) if len(p) > 3 and isinstance(
                p[3], (int, float)) else r1

        def angle_of(pt: Vec3) -> float:
            d = pt - origin
            return float(np.arctan2(np.dot(d, y) / r2, np.dot(d, x) / r1))

        if v1 is None or v2 is None or _close(v1, v2):
            thetas = np.linspace(0.0, 2 * np.pi, CURVE_SAMPLES + 1)
        else:
            a1, a2 = angle_of(v1), angle_of(v2)
            if a2 <= a1 + 1e-9:
                a2 += 2 * np.pi
            thetas = np.linspace(a1, a2, CURVE_SAMPLES + 1)
        # vectorized: origin + cos(t)*r1*x + sin(t)*r2*y
        pts = (origin
               + np.outer(np.cos(thetas) * r1, x)
               + np.outer(np.sin(thetas) * r2, y))
        if v1 is not None and v2 is not None and not _close(v1, v2):
            pts[0], pts[-1] = v1, v2
        return pts, name.lower()

    if "B_SPLINE_CURVE_WITH_KNOTS" in leafs or "B_SPLINE_CURVE" in leafs \
            or leafs & {"BEZIER_CURVE", "QUASI_UNIFORM_CURVE",
                        "UNIFORM_CURVE"}:
        pts = _sample_bspline(g, curve)
        if len(pts) >= 2:
            if v1 is not None and v2 is not None:
                pts[0], pts[-1] = v1, v2
            return pts, "spline"
        return _chord(v1, v2)

    if "TRIMMED_CURVE" in leafs:
        p = g.params_of(curve, "TRIMMED_CURVE")
        if p and len(p) >= 4:
            basis = g.deref(p[1])
            t1 = _trim_point(g, p[2])
            t2 = _trim_point(g, p[3])
            return _sample_curve(g, basis,
                                 t1 if t1 is not None else v1,
                                 t2 if t2 is not None else v2)
        return _chord(v1, v2)

    if "POLYLINE" in leafs:
        p = g.params_of(curve, "POLYLINE")
        if p and len(p) > 1 and isinstance(p[1], list):
            pts = _stack(g.point(r) for r in p[1])
            if len(pts) >= 2:
                return pts, "polyline"
        return _chord(v1, v2)

    if "SURFACE_CURVE" in leafs or "SEAM_CURVE" in leafs:
        p = g.params_of(curve, "SURFACE_CURVE") or \
            g.params_of(curve, "SEAM_CURVE")
        if p and len(p) > 1:
            return _sample_curve(g, g.deref(p[1]), v1, v2)

    return _chord(v1, v2)


def _chord(v1, v2):
    if v1 is not None and v2 is not None:
        return np.array([v1, v2]), "approx"
    return _EMPTY, "approx"


def _close(a: Vec3, b: Vec3, tol: float = 1e-9) -> bool:
    return bool(np.allclose(a, b, atol=tol))


def _trim_point(g: _Graph, trim) -> Optional[Vec3]:
    if isinstance(trim, list):
        for t in trim:
            if isinstance(t, Ref):
                pt = g.point(t)
                if pt is not None:
                    return pt
    return None


def _sample_bspline(g: _Graph, curve: Entity) -> np.ndarray:
    """Sample a (possibly rational) B-spline curve with de Boor's algorithm."""
    rec = None
    for name in ("B_SPLINE_CURVE_WITH_KNOTS", "B_SPLINE_CURVE",
                 "BEZIER_CURVE", "QUASI_UNIFORM_CURVE", "UNIFORM_CURVE"):
        p = g.params_of(curve, name)
        if p is not None:
            rec = (name, p)
            break
    if rec is None:
        return _EMPTY
    name, p = rec
    if len(p) < 3 or not isinstance(p[1], (int, float)) \
            or not isinstance(p[2], list):
        return _EMPTY
    degree = int(p[1])
    ctrl = [g.point(r) for r in p[2]]
    ctrl = [c for c in ctrl if c is not None]
    n_ctrl = len(ctrl)
    if n_ctrl < 2:
        return _EMPTY
    if degree < 1 or degree >= n_ctrl:
        degree = max(1, min(3, n_ctrl - 1))

    # knot vector
    knots: list[float] = []
    if name == "B_SPLINE_CURVE_WITH_KNOTS" and len(p) >= 8 \
            and isinstance(p[6], list) and isinstance(p[7], list):
        mults = [int(m) for m in p[6] if isinstance(m, (int, float))]
        kvals = [float(k) for k in p[7] if isinstance(k, (int, float))]
        for m, k in zip(mults, kvals):
            knots.extend([k] * m)
    if len(knots) != n_ctrl + degree + 1:
        # uniform clamped fallback
        inner = n_ctrl - degree
        knots = [0.0] * (degree + 1) + \
                [i / inner for i in range(1, inner)] + \
                [1.0] * (degree + 1)
    knots = np.asarray(knots, dtype=float)
    t0, t1 = knots[degree], knots[n_ctrl]

    # control points as homogeneous coordinates (x, y, z, w)
    weights = None
    wp = g.params_of(curve, "RATIONAL_B_SPLINE_CURVE")
    if wp:
        for v in wp:
            if isinstance(v, list) and len(v) == n_ctrl and \
                    all(isinstance(w, (int, float)) for w in v):
                weights = np.array([float(w) for w in v])
                break
    homo = np.ones((n_ctrl, 4))
    homo[:, :3] = np.array(ctrl)
    if weights is not None:
        homo[:, :3] *= weights[:, None]
        homo[:, 3] = weights

    def de_boor(t: float) -> np.ndarray:
        k = degree
        span = int(np.searchsorted(knots, t, side="right") - 1)
        span = min(max(span, degree), n_ctrl - 1)
        d = homo[span - k:span + 1].copy()
        for r in range(1, k + 1):
            for j in range(k, r - 1, -1):
                i = span - k + j
                den = knots[i + k - r + 1] - knots[i]
                alpha = 0.0 if den == 0 else (t - knots[i]) / den
                d[j] = d[j - 1] * (1 - alpha) + d[j] * alpha
        pt = d[k]
        return pt[:3] / pt[3] if pt[3] else pt[:3]

    ts = np.linspace(t0, t1, CURVE_SAMPLES + 1)
    return np.array([de_boor(float(t)) for t in ts])


# ---------------------------------------------------------------------------
# Tessellated geometry
# ---------------------------------------------------------------------------

def _extract_tessellation(g: _Graph, ent: Entity, model: WireModel,
                          mode: str = "triangles") -> None:
    params = _all_params(ent)
    coords_rows: list = []
    for r in _walk_refs(params):
        tgt = g.ents.get(r.eid)
        if tgt is None:
            continue
        cp = (tgt.record("COORDINATES_LIST")
              or tgt.record("CARTESIAN_POINT_LIST_3D"))
        if cp is not None and cp.params:
            for v in cp.params:
                if isinstance(v, list) and v and isinstance(v[0], list):
                    for trip in v:
                        nums = [x for x in trip
                                if isinstance(x, (int, float))]
                        if len(nums) >= 3:
                            coords_rows.append(
                                (float(nums[0]), float(nums[1]),
                                 float(nums[2])))
            if coords_rows:
                break
    if not coords_rows:
        return
    coords = np.array(coords_rows)
    n = len(coords)
    # index lists: last parameter that is a list of integer lists
    # (triangles for face sets, line strips for curve sets)
    minlen = 3 if mode == "triangles" else 2
    indexed: list = []
    for v in reversed(params):
        if isinstance(v, list) and v and isinstance(v[0], list) and \
                v[0] and all(isinstance(x, (int, float)) for x in v[0]):
            ok = True
            for grp in v:
                if len(grp) < minlen or any(
                        not isinstance(x, (int, float))
                        or int(x) < 1 or int(x) > n for x in grp):
                    ok = False
                    break
            if ok:
                indexed = v
                break
    if mode == "strips" and indexed:
        for strip in indexed:
            idx = np.array([int(i) - 1 for i in strip])
            model.polylines.append(
                WirePolyline(coords[idx], ent.eid, "tess"))
        return
    if indexed:                       # triangles -> unique edges
        seen = set()
        for tri in indexed:
            a, b, c = int(tri[0]) - 1, int(tri[1]) - 1, int(tri[2]) - 1
            for e in ((a, b), (b, c), (c, a)):
                key = (min(e), max(e))
                if key in seen:
                    continue
                seen.add(key)
                model.polylines.append(WirePolyline(
                    coords[list(e)], ent.eid, "tess"))
    else:
        for row in coords:
            model.free_points.append((row, ent.eid))
