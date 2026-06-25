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
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import (TopAbs_FACE, TopAbs_REVERSED,
                                     TopAbs_SOLID, TopAbs_SHELL)
        from OCC.Core.TopLoc import TopLoc_Location
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.TopoDS import topods
        AVAILABLE = True
        return {
            "STEPControl_Reader": STEPControl_Reader,
            "IFSelect_RetDone": IFSelect_RetDone,
            "BRepMesh_IncrementalMesh": BRepMesh_IncrementalMesh,
            "TopExp_Explorer": TopExp_Explorer,
            "TopAbs_FACE": TopAbs_FACE,
            "TopAbs_REVERSED": TopAbs_REVERSED,
            "TopAbs_SOLID": TopAbs_SOLID,
            "TopAbs_SHELL": TopAbs_SHELL,
            "TopLoc_Location": TopLoc_Location,
            "BRep_Tool": BRep_Tool,
            "topods": topods,
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
    base = 0
    fidx = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
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

    if mesh.empty:
        return OccResult(ok=False,
                         error="OpenCASCADE read the file but produced no "
                               "triangulated surfaces (no solid/shell B-rep "
                               "to mesh).", mesh=mesh, acc=acc)
    return OccResult(ok=True, mesh=mesh, acc=acc)


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
