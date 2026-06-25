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


if __name__ == "__main__":
    unittest.main(verbosity=2)
