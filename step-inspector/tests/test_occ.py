"""Tests for the optional OpenCASCADE backend.

The real-mesh tests are skipped automatically when pythonocc-core is not
installed (e.g. the default CI interpreter); the graceful-absence path is
always tested.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step_inspector import occ_backend as occ
from step_inspector.parser import audit_file

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "samples", "cube_demo.step")
HAVE_OCC = occ.available()


class TestGracefulAbsence(unittest.TestCase):
    def test_load_never_raises_and_reports(self):
        r = occ.load_and_mesh(SAMPLE)
        # Either it worked (OCC present) or it reported a clean error.
        self.assertTrue(r.ok or r.error)
        if not HAVE_OCC:
            self.assertFalse(r.ok)
            self.assertIn("pythonocc-core", r.error)
            self.assertTrue(r.mesh.empty)


@unittest.skipUnless(HAVE_OCC, "pythonocc-core not installed")
class TestRealMesh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = occ.load_and_mesh(SAMPLE)
        cls.res = audit_file(SAMPLE)

    def test_cube_meshed(self):
        self.assertTrue(self.r.ok, self.r.error)
        a = self.r.acc
        self.assertEqual(a.solids, 1)
        self.assertEqual(a.faces_total, 6)
        self.assertEqual(a.faces_meshed, 6)
        self.assertEqual(a.faces_no_triangulation, 0)
        self.assertEqual(a.triangles, 12)        # 6 quads -> 12 triangles

    def test_mesh_arrays_shape(self):
        m = self.r.mesh
        self.assertEqual(m.vertices.shape[1], 3)
        self.assertEqual(m.triangles.shape[1], 3)
        self.assertEqual(len(m.normals), len(m.triangles))
        self.assertEqual(len(m.face_of_tri), len(m.triangles))
        # triangle indices stay within the vertex array
        self.assertLess(int(m.triangles.max()), len(m.vertices))

    def test_bounds_match_cube(self):
        (lo, hi) = self.r.mesh.bounds()
        self.assertAlmostEqual(lo[0], 0.0, places=5)
        self.assertAlmostEqual(hi[0], 40.0, places=5)
        self.assertAlmostEqual(hi[2], 40.0, places=5)

    def test_entity_count_cross_checks_audit(self):
        # OCC's reader and the audit parser should agree on entity count.
        self.assertEqual(self.r.acc.entities_parsed, len(self.res.entities))


@unittest.skipUnless(HAVE_OCC, "pythonocc-core not installed")
class TestMeasurement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pm = occ.load_and_mesh(SAMPLE).pick

    def test_pick_model_built(self):
        self.assertTrue(self.pm.vertices)
        self.assertTrue(self.pm.edges)
        self.assertEqual(len(self.pm.faces), 6)       # cube + annotation faces
        self.assertIn("area", self.pm.faces[0].info)
        self.assertIn("length", self.pm.edges[0].info)

    def _vertex(self, xyz):
        import numpy as np
        for v in self.pm.vertices:
            if np.allclose(v.pts[0], xyz, atol=1e-6):
                return v
        return None

    def test_vertex_to_vertex_distance(self):
        a = self._vertex([0, 0, 0])
        b = self._vertex([40, 40, 40])
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        r = occ.measure(a, b)
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.distance, 40 * 3 ** 0.5, places=4)

    def test_parallel_faces_distance(self):
        # bottom (z=0) and top (z=40) planar faces are 40 apart
        r = occ.measure(self.pm.faces[0], self.pm.faces[1])
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.distance, 40.0, places=4)

    def test_measure_fields(self):
        a = self._vertex([0, 0, 0])
        r = occ.measure(a, self.pm.faces[1])
        self.assertEqual(r.kind_a, "vertex")
        self.assertEqual(r.kind_b, "face")
        self.assertEqual(len(r.delta), 3)

    def test_angle_between_faces(self):
        import numpy as np

        def face_normal(n):
            n = np.array(n) / np.linalg.norm(n)
            for f in self.pm.faces:
                if f.direction and np.allclose(np.abs(f.direction), np.abs(n),
                                               atol=1e-3):
                    return f
            return None
        adj = occ.measure(face_normal([0, 0, 1]), face_normal([1, 0, 0]))
        self.assertAlmostEqual(adj.angle, 90.0, places=3)
        par = occ.measure(self.pm.faces[0], self.pm.faces[1])
        self.assertAlmostEqual(par.angle, 0.0, places=3)

    def test_circular_edge_radius(self):
        circ = [e for e in self.pm.edges if e.radius]
        self.assertTrue(circ)                       # datum circle D1
        self.assertAlmostEqual(circ[0].radius, 12.5, places=3)
        self.assertIn("⌀", circ[0].info)


@unittest.skipUnless(HAVE_OCC, "pythonocc-core not installed")
class TestProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = occ.load_and_mesh(SAMPLE).props

    def test_volume_and_area(self):
        self.assertTrue(self.p.ok)
        self.assertTrue(self.p.is_solid)
        self.assertAlmostEqual(self.p.volume, 64000.0, places=1)   # 40^3
        self.assertAlmostEqual(self.p.area, 9600.0, places=1)      # 6*40^2

    def test_centre_of_mass(self):
        for c in self.p.com:
            self.assertAlmostEqual(c, 20.0, places=3)

    def test_bounding_box(self):
        # cube is 40; annotation geometry extends it — must be >= 40
        for d in self.p.bbox_size:
            self.assertGreaterEqual(d, 40.0)

    def test_single_solid_breakdown(self):
        self.assertEqual(len(self.p.solids), 1)
        s = self.p.solids[0]
        self.assertEqual(s.index, 1)
        self.assertAlmostEqual(s.volume, 64000.0, places=1)
        self.assertAlmostEqual(s.area, 9600.0, places=1)


@unittest.skipUnless(HAVE_OCC, "pythonocc-core not installed")
class TestMultiBody(unittest.TestCase):
    """Per-solid properties on a generated two-box STEP file."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.STEPControl import (STEPControl_Writer,
                                          STEPControl_AsIs)
        from OCC.Core.gp import gp_Pnt
        cls.path = tempfile.mktemp(suffix=".step")
        w = STEPControl_Writer()
        w.Transfer(BRepPrimAPI_MakeBox(10, 10, 10).Shape(), STEPControl_AsIs)
        w.Transfer(BRepPrimAPI_MakeBox(gp_Pnt(50, 0, 0), 20, 20, 20).Shape(),
                   STEPControl_AsIs)
        w.Write(cls.path)
        cls.r = occ.load_and_mesh(cls.path)

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.path)
        except OSError:
            pass

    def test_two_solids_found(self):
        self.assertTrue(self.r.ok, self.r.error)
        self.assertEqual(len(self.r.props.solids), 2)

    def test_individual_volumes_and_com(self):
        s1, s2 = self.r.props.solids
        self.assertAlmostEqual(s1.volume, 1000.0, places=2)     # 10^3
        self.assertAlmostEqual(s2.volume, 8000.0, places=2)     # 20^3
        self.assertAlmostEqual(self.r.props.volume, 9000.0, places=2)
        self.assertEqual(tuple(round(c, 1) for c in s1.com), (5.0, 5.0, 5.0))
        self.assertEqual(tuple(round(c, 1) for c in s2.com),
                         (60.0, 10.0, 10.0))

    def test_solid_bboxes(self):
        s1, s2 = self.r.props.solids
        self.assertEqual(tuple(round(b) for b in s1.bbox_size), (10, 10, 10))
        self.assertEqual(tuple(round(b) for b in s2.bbox_size), (20, 20, 20))


@unittest.skipUnless(HAVE_OCC, "pythonocc-core not installed")
class TestDescribeSubshape(unittest.TestCase):
    """describe_subshape wraps native-viewer selections for measure()."""

    @classmethod
    def setUpClass(cls):
        cls.pm = occ.load_and_mesh(SAMPLE).pick

    def test_face(self):
        d = occ.describe_subshape(self.pm.faces[0].shape)
        self.assertEqual(d.kind, "face")
        self.assertIn("area 1600", d.info)
        self.assertIsNotNone(d.direction)          # planar -> has normal

    def test_vertex_and_measure_roundtrip(self):
        dv = occ.describe_subshape(self.pm.vertices[0].shape)
        self.assertEqual(dv.kind, "vertex")
        df = occ.describe_subshape(self.pm.faces[0].shape)
        r = occ.measure(dv, df)
        self.assertTrue(r.ok)

    def test_circular_edge(self):
        circ = [e for e in self.pm.edges if e.radius][0]
        d = occ.describe_subshape(circ.shape)
        self.assertEqual(d.kind, "edge")
        self.assertAlmostEqual(d.radius, 12.5, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
