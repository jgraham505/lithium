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


if __name__ == "__main__":
    unittest.main(verbosity=2)
