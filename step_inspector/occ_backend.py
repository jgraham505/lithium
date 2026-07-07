"""Optional OpenCASCADE (pythonocc-core) backend for shaded B-rep surfaces.

The audit parser remains the single authority on whether the *whole file*
was consumed — see :mod:`step_inspector.parser`.  OpenCASCADE is used here
only to produce a shaded triangulated view of the exact B-rep geometry, a
job the pure-Python/NumPy wireframe sampler cannot do as faithfully.

Because OpenCASCADE has its own, more permissive STEP reader (it will load a
file and merely print a "Fails Count" for syntax it does not like), it must
never be treated as proof of coverage.  Instead this module *accounts* for
what OCC did: how many STEP entities its reader parsed, how many roots it
transferred, and how many faces it actually tessellated — and that account
is cross-checked against the audit parser so any discrepancy is surfaced
rather than hidden.

pythonocc-core is an optional, heavy dependency normally installed with
conda (``conda install -c conda-forge pythonocc-core``).  This module
imports it lazily; if it is missing the rest of the application is
unaffected and the shaded view simply reports that the backend is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

#: Set on the first load() call.
AVAILABLE: Optional[bool] = None
IMPORT_ERROR = ""


def _try_import():
    """Import the OCC symbols we need; cache availability."""
    global AVAILABLE, IMPORT_ERROR
    try:
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.TopExp import TopExp_Explorer, topexp
        from OCC.Core.TopAbs import (TopAbs_FACE, TopAbs_REVERSED,
                                     TopAbs_SOLID, TopAbs_SHELL,
                                     TopAbs_EDGE, TopAbs_VERTEX)
        from OCC.Core.TopLoc import TopLoc_Location
        from OCC.Core.TopTools import TopTools_IndexedMapOfShape
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.TopoDS import topods
        from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
        from OCC.Core.BRepAdaptor import (BRepAdaptor_Curve,
                                          BRepAdaptor_Surface)
        from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection
        from OCC.Core.GProp import GProp_GProps
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.GeomAbs import (GeomAbs_Line, GeomAbs_Circle,
                                      GeomAbs_Plane, GeomAbs_Cylinder)
        AVAILABLE = True
        return {
            "STEPControl_Reader": STEPControl_Reader,
            "IFSelect_RetDone": IFSelect_RetDone,
            "BRepMesh_IncrementalMesh": BRepMesh_IncrementalMesh,
            "TopExp_Explorer": TopExp_Explorer,
            "topexp": topexp,
            "TopAbs_FACE": TopAbs_FACE,
            "TopAbs_REVERSED": TopAbs_REVERSED,
            "TopAbs_SOLID": TopAbs_SOLID,
            "TopAbs_SHELL": TopAbs_SHELL,
            "TopAbs_EDGE": TopAbs_EDGE,
            "TopAbs_VERTEX": TopAbs_VERTEX,
            "TopLoc_Location": TopLoc_Location,
            "TopTools_IndexedMapOfShape": TopTools_IndexedMapOfShape,
            "BRep_Tool": BRep_Tool,
            "topods": topods,
            "BRepExtrema_DistShapeShape": BRepExtrema_DistShapeShape,
            "BRepAdaptor_Curve": BRepAdaptor_Curve,
            "BRepAdaptor_Surface": BRepAdaptor_Surface,
            "GCPnts_QuasiUniformDeflection": GCPnts_QuasiUniformDeflection,
            "GProp_GProps": GProp_GProps,
            "brepgprop": brepgprop,
            "Bnd_Box": Bnd_Box,
            "brepbndlib": brepbndlib,
            "GeomAbs_Line": GeomAbs_Line,
            "GeomAbs_Circle": GeomAbs_Circle,
            "GeomAbs_Plane": GeomAbs_Plane,
            "GeomAbs_Cylinder": GeomAbs_Cylinder,
        }
    except Exception as e:        # pragma: no cover - depends on environment
        AVAILABLE = False
        IMPORT_ERROR = f"{type(e).__name__}: {e}"
        return None


@dataclass
class ShadedMesh:
    """Triangulated surface geometry for the shaded viewer."""
    vertices: np.ndarray = field(            # (N, 3)
        default_factory=lambda: np.empty((0, 3)))
    triangles: np.ndarray = field(           # (M, 3) int, 0-based
        default_factory=lambda: np.empty((0, 3), dtype=int))
    normals: np.ndarray = field(             # (M, 3) per-triangle
        default_factory=lambda: np.empty((0, 3)))
    face_of_tri: np.ndarray = field(         # (M,) source face index
        default_factory=lambda: np.empty((0,), dtype=int))

    @property
    def empty(self) -> bool:
        return len(self.triangles) == 0

    def bounds(self) -> Optional[tuple]:
        if not len(self.vertices):
            return None
        lo = self.vertices.min(axis=0)
        hi = self.vertices.max(axis=0)
        return ((float(lo[0]), float(lo[1]), float(lo[2])),
                (float(hi[0]), float(hi[1]), float(hi[2])))


@dataclass
class PickShape:
    """A pickable B-rep sub-shape with screen geometry and exact handle."""
    kind: str                 # "vertex" | "edge" | "face"
    index: int                # position within its kind list
    pts: np.ndarray           # (K, 3) world points used for screen picking
    shape: object             # the TopoDS sub-shape (exact distance source)
    info: str                 # human label (coords / length / area / radius)
    direction: tuple = None   # unit dir (line edge / plane normal / axis)
    radius: float = None      # circular edge or cylindrical face radius


@dataclass
class ShapeProperties:
    """Exact mass/size properties of the whole B-rep (OpenCASCADE)."""
    ok: bool = False
    is_solid: bool = False
    volume: float = 0.0
    area: float = 0.0
    com: tuple = (0.0, 0.0, 0.0)          # centre of mass
    bbox_min: tuple = (0.0, 0.0, 0.0)
    bbox_max: tuple = (0.0, 0.0, 0.0)

    @property
    def bbox_size(self) -> tuple:
        return (self.bbox_max[0] - self.bbox_min[0],
                self.bbox_max[1] - self.bbox_min[1],
                self.bbox_max[2] - self.bbox_min[2])


@dataclass
class PickModel:
    vertices: list = field(default_factory=list)   # [PickShape]
    edges: list = field(default_factory=list)
    faces: list = field(default_factory=list)      # aligned with face_of_tri
    note: str = ""

    @property
    def empty(self) -> bool:
        return not (self.vertices or self.edges or self.faces)


@dataclass
class MeasureResult:
    ok: bool
    kind_a: str = ""
    kind_b: str = ""
    info_a: str = ""
    info_b: str = ""
    distance: float = 0.0
    p1: tuple = (0.0, 0.0, 0.0)     # closest point on shape A
    p2: tuple = (0.0, 0.0, 0.0)     # closest point on shape B
    angle: float = None            # degrees, when both picks are directional
    error: str = ""

    @property
    def delta(self) -> tuple:
        return (self.p2[0] - self.p1[0], self.p2[1] - self.p1[1],
                self.p2[2] - self.p1[2])


@dataclass
class OccAccounting:
    """What OpenCASCADE's reader/mesher did with the file."""
    entities_parsed: int = 0          # STEP entities OCC's reader loaded
    roots_for_transfer: int = 0
    roots_transferred: int = 0
    solids: int = 0
    shells: int = 0
    faces_total: int = 0
    faces_meshed: int = 0
    faces_no_triangulation: int = 0
    triangles: int = 0
    nodes: int = 0
    read_warnings: str = ""

    @property
    def all_faces_meshed(self) -> bool:
        return self.faces_total > 0 and self.faces_no_triangulation == 0


@dataclass
class OccResult:
    ok: bool
    error: str = ""
    mesh: ShadedMesh = field(default_factory=ShadedMesh)
    acc: OccAccounting = field(default_factory=OccAccounting)
    pick: PickModel = field(default_factory=PickModel)
    props: ShapeProperties = field(default_factory=ShapeProperties)


def available() -> bool:
    if AVAILABLE is None:
        _try_import()
    return bool(AVAILABLE)


def load_and_mesh(path: str, lin_deflection: float = 0.0,
                  ang_deflection: float = 0.5) -> OccResult:
    """Read ``path`` with OpenCASCADE and tessellate its B-rep.

    ``lin_deflection`` of 0 means "choose from the model size".  Never
    raises; failures are reported in the returned result.
    """
    m = _try_import()
    if not AVAILABLE:
        return OccResult(ok=False, error=(
            "pythonocc-core (OpenCASCADE) is not installed. Install it with:"
            "  conda install -c conda-forge pythonocc-core   "
            f"(import error: {IMPORT_ERROR})"))

    STEPControl_Reader = m["STEPControl_Reader"]
    IFSelect_RetDone = m["IFSelect_RetDone"]
    BRepMesh_IncrementalMesh = m["BRepMesh_IncrementalMesh"]
    TopExp_Explorer = m["TopExp_Explorer"]
    TopAbs_FACE = m["TopAbs_FACE"]
    TopAbs_REVERSED = m["TopAbs_REVERSED"]
    TopAbs_SOLID = m["TopAbs_SOLID"]
    TopAbs_SHELL = m["TopAbs_SHELL"]
    TopLoc_Location = m["TopLoc_Location"]
    BRep_Tool = m["BRep_Tool"]
    topods = m["topods"]

    acc = OccAccounting()
    try:
        reader = STEPControl_Reader()
        status = reader.ReadFile(path)
        if status != IFSelect_RetDone:
            return OccResult(ok=False,
                             error=f"OpenCASCADE could not read the file "
                                   f"(status {status}).", acc=acc)
        try:
            acc.entities_parsed = int(reader.StepModel().NbEntities())
        except Exception:
            acc.entities_parsed = 0
        acc.roots_for_transfer = int(reader.NbRootsForTransfer())
        acc.roots_transferred = int(reader.TransferRoots())
        shape = reader.OneShape()
        if shape.IsNull():
            return OccResult(ok=False,
                             error="OpenCASCADE produced no shape from the "
                                   "file (no transferable B-rep geometry).",
                             acc=acc)
    except Exception as e:
        return OccResult(ok=False,
                         error=f"OpenCASCADE read/transfer failed: {e}",
                         acc=acc)

    # bounding-box driven default deflection keeps small and large parts sane
    if lin_deflection <= 0:
        lin_deflection = _auto_deflection(shape, m)

    try:
        BRepMesh_IncrementalMesh(shape, lin_deflection, False,
                                 ang_deflection, True)
    except Exception as e:
        return OccResult(ok=False,
                         error=f"OpenCASCADE meshing failed: {e}", acc=acc)

    acc.solids = _count(shape, TopAbs_SOLID, TopExp_Explorer)
    acc.shells = _count(shape, TopAbs_SHELL, TopExp_Explorer)

    verts: list = []
    tris: list = []
    face_ids: list = []
    face_shapes: list = []          # TopoDS_Face per fidx (aligned to face_ids)
    base = 0
    fidx = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        face_shapes.append(face)
        acc.faces_total += 1
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation(face, loc)
        if tri is None:
            acc.faces_no_triangulation += 1
            exp.Next()
            fidx += 1
            continue
        acc.faces_meshed += 1
        trsf = loc.Transformation()
        reverse = face.Orientation() == TopAbs_REVERSED
        nb_nodes = tri.NbNodes()
        fverts = np.empty((nb_nodes, 3))
        for i in range(1, nb_nodes + 1):
            p = tri.Node(i).Transformed(trsf)
            fverts[i - 1] = (p.X(), p.Y(), p.Z())
        verts.append(fverts)
        for i in range(1, tri.NbTriangles() + 1):
            n1, n2, n3 = tri.Triangle(i).Get()
            if reverse:
                n1, n3 = n3, n1
            a, b, c = base + n1 - 1, base + n2 - 1, base + n3 - 1
            tris.append((a, b, c))
            face_ids.append(fidx)
        base += nb_nodes
        fidx += 1
        exp.Next()

    if verts:
        vertices = np.vstack(verts)
        triangles = np.array(tris, dtype=int)
        normals = _triangle_normals(vertices, triangles)
        mesh = ShadedMesh(vertices, triangles, normals,
                          np.array(face_ids, dtype=int))
    else:
        mesh = ShadedMesh()
    acc.triangles = len(mesh.triangles)
    acc.nodes = len(mesh.vertices)

    pick = _build_pick(shape, face_shapes, m)
    props = _properties(shape, m)

    if mesh.empty:
        return OccResult(ok=False,
                         error="OpenCASCADE read the file but produced no "
                               "triangulated surfaces (no solid/shell B-rep "
                               "to mesh).", mesh=mesh, acc=acc, props=props)
    return OccResult(ok=True, mesh=mesh, acc=acc, pick=pick, props=props)


def _properties(shape, m) -> ShapeProperties:
    """Exact volume / area / centre-of-mass / bounding box of the shape."""
    p = ShapeProperties()
    try:
        gs = m["GProp_GProps"]()
        m["brepgprop"].SurfaceProperties(shape, gs)
        p.area = float(gs.Mass())
        n_solids = _count(shape, m["TopAbs_SOLID"], m["TopExp_Explorer"])
        p.is_solid = n_solids > 0
        gv = m["GProp_GProps"]()
        m["brepgprop"].VolumeProperties(shape, gv)
        p.volume = float(gv.Mass())
        c = gv.CentreOfMass() if p.is_solid else gs.CentreOfMass()
        p.com = (c.X(), c.Y(), c.Z())
        box = m["Bnd_Box"]()
        m["brepbndlib"].Add(shape, box)
        x0, y0, z0, x1, y1, z1 = box.Get()
        p.bbox_min = (x0, y0, z0)
        p.bbox_max = (x1, y1, z1)
        p.ok = True
    except Exception:
        p.ok = False
    return p


# ---------------------------------------------------------------------------
# Pickable sub-shapes and exact measurement
# ---------------------------------------------------------------------------

MAX_PICK_FACES = 8000          # skip building the pick model above this
MAX_PICK_EDGES = 40000


def _build_pick(shape, face_shapes, m) -> PickModel:
    """Index vertices/edges/faces with screen geometry and exact handles."""
    pm = PickModel()
    topexp = m["topexp"]
    BRep_Tool = m["BRep_Tool"]
    topods = m["topods"]
    IndexedMap = m["TopTools_IndexedMapOfShape"]
    TopAbs_EDGE = m["TopAbs_EDGE"]
    TopAbs_VERTEX = m["TopAbs_VERTEX"]
    GProp = m["GProp_GProps"]
    brepgprop = m["brepgprop"]

    # faces (aligned with mesh.face_of_tri indexing)
    if len(face_shapes) > MAX_PICK_FACES:
        pm.note = (f"model has {len(face_shapes)} faces; measurement index "
                   "skipped to stay responsive")
        return pm
    for i, face in enumerate(face_shapes):
        try:
            g = GProp()
            brepgprop.SurfaceProperties(face, g)
            info = f"area {g.Mass():.4g}"
        except Exception:
            info = "face"
        direction, radius, extra = _surface_dir_radius(face, m)
        pm.faces.append(PickShape("face", i, np.empty((0, 3)), face,
                                  info + extra, direction, radius))

    # unique vertices
    vmap = IndexedMap()
    topexp.MapShapes(shape, TopAbs_VERTEX, vmap)
    for i in range(1, vmap.Size() + 1):
        v = topods.Vertex(vmap.FindKey(i))
        try:
            p = BRep_Tool.Pnt(v)
            xyz = np.array([[p.X(), p.Y(), p.Z()]])
            info = f"({p.X():.4g}, {p.Y():.4g}, {p.Z():.4g})"
        except Exception:
            continue
        pm.vertices.append(PickShape("vertex", len(pm.vertices), xyz, v, info))

    # unique edges (discretized for screen picking)
    emap = IndexedMap()
    topexp.MapShapes(shape, TopAbs_EDGE, emap)
    if emap.Size() <= MAX_PICK_EDGES:
        for i in range(1, emap.Size() + 1):
            edge = topods.Edge(emap.FindKey(i))
            pts = _discretize_edge(edge, m)
            if pts is None or len(pts) < 2:
                continue
            try:
                g = GProp()
                brepgprop.LinearProperties(edge, g)
                length = g.Mass()
            except Exception:
                length = 0.0
            direction, radius, info = _curve_dir_radius(edge, length, m)
            pm.edges.append(PickShape("edge", len(pm.edges), pts, edge, info,
                                      direction, radius))
    return pm


def _curve_dir_radius(edge, length, m):
    """Return (direction, radius, info) for an edge from its curve type."""
    try:
        ad = m["BRepAdaptor_Curve"](edge)
        t = ad.GetType()
        if t == m["GeomAbs_Line"]:
            d = ad.Line().Direction()
            return ((d.X(), d.Y(), d.Z()), None,
                    f"line, length {length:.4g}")
        if t == m["GeomAbs_Circle"]:
            circ = ad.Circle()
            r = circ.Radius()
            ax = circ.Axis().Direction()
            return ((ax.X(), ax.Y(), ax.Z()), r,
                    f"circle r={r:.4g} (⌀{2 * r:.4g}), len {length:.4g}")
    except Exception:
        pass
    return (None, None, f"length {length:.4g}")


def _surface_dir_radius(face, m):
    """Return (direction, radius, extra_info) for a face from its surface."""
    try:
        ad = m["BRepAdaptor_Surface"](face)
        t = ad.GetType()
        if t == m["GeomAbs_Plane"]:
            n = ad.Plane().Axis().Direction()
            return ((n.X(), n.Y(), n.Z()), None, ", planar")
        if t == m["GeomAbs_Cylinder"]:
            cyl = ad.Cylinder()
            r = cyl.Radius()
            ax = cyl.Axis().Direction()
            return ((ax.X(), ax.Y(), ax.Z()), r,
                    f", cylinder r={r:.4g} (⌀{2 * r:.4g})")
    except Exception:
        pass
    return (None, None, "")


def _discretize_edge(edge, m):
    """Return an (N, 3) polyline approximating an edge, or None."""
    try:
        adaptor = m["BRepAdaptor_Curve"](edge)
        disc = m["GCPnts_QuasiUniformDeflection"](adaptor, 0.2)
        if not disc.IsDone() or disc.NbPoints() < 2:
            return None
        pts = np.empty((disc.NbPoints(), 3))
        for i in range(1, disc.NbPoints() + 1):
            p = disc.Value(i)
            pts[i - 1] = (p.X(), p.Y(), p.Z())
        return pts
    except Exception:
        return None


def measure(pa: PickShape, pb: PickShape) -> MeasureResult:
    """Exact minimum distance between two picked sub-shapes (OpenCASCADE)."""
    mods = _try_import()
    if not AVAILABLE:
        return MeasureResult(False, error="OpenCASCADE not available")
    try:
        ext = mods["BRepExtrema_DistShapeShape"](pa.shape, pb.shape)
        if not ext.IsDone():
            return MeasureResult(False, error="distance computation failed")
        p1 = ext.PointOnShape1(1)
        p2 = ext.PointOnShape2(1)
        return MeasureResult(
            True, kind_a=pa.kind, kind_b=pb.kind, info_a=pa.info,
            info_b=pb.info, distance=float(ext.Value()),
            p1=(p1.X(), p1.Y(), p1.Z()), p2=(p2.X(), p2.Y(), p2.Z()),
            angle=_angle_between(pa.direction, pb.direction))
    except Exception as e:
        return MeasureResult(False, error=str(e))


def _angle_between(da, db):
    """Angle in degrees (0-90) between two directions, or None."""
    if not da or not db:
        return None
    a = np.array(da, dtype=float)
    b = np.array(db, dtype=float)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return None
    cos = abs(float(np.dot(a, b)) / (na * nb))
    return float(np.degrees(np.arccos(min(1.0, max(0.0, cos)))))


def _count(shape, typ, TopExp_Explorer) -> int:
    e = TopExp_Explorer(shape, typ)
    n = 0
    while e.More():
        n += 1
        e.Next()
    return n


def _auto_deflection(shape, m) -> float:
    try:
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        box = Bnd_Box()
        brepbndlib.Add(shape, box)
        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
        diag = ((xmax - xmin) ** 2 + (ymax - ymin) ** 2
                + (zmax - zmin) ** 2) ** 0.5
        return max(diag * 0.001, 1e-4) if diag else 0.1
    except Exception:
        return 0.1


def _triangle_normals(vertices: np.ndarray,
                      triangles: np.ndarray) -> np.ndarray:
    v0 = vertices[triangles[:, 0]]
    v1 = vertices[triangles[:, 1]]
    v2 = vertices[triangles[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    lens = np.linalg.norm(n, axis=1, keepdims=True)
    lens[lens == 0] = 1.0
    return n / lens
