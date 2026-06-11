"""Tests for PMI / GD&T extraction (AP242 / AP214 content)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step_inspector.parser import audit_bytes, audit_file
from step_inspector.pmi import extract_pmi
from step_inspector.geometry import extract_wireframe

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "samples", "cube_demo.step")


def labels(model, cat):
    return [it.label for it in model.categories[cat]]


class TestSamplePMI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = audit_file(SAMPLE)
        cls.pmi = extract_pmi(cls.res)

    def test_geometric_tolerances(self):
        gdt = labels(self.pmi, "gdt")
        self.assertEqual(len(gdt), 2)
        flatness = next(l for l in gdt if l.startswith("Flatness"))
        self.assertIn("0.05 mm", flatness)
        self.assertIn("top mating face", flatness)
        position = next(l for l in gdt if l.startswith("Position"))
        self.assertIn("0.2 mm", position)
        self.assertIn("datums A-B", position)

    def test_dimension_with_plus_minus(self):
        dims = labels(self.pmi, "dimension")
        self.assertEqual(len(dims), 1)
        self.assertIn("width", dims[0])
        self.assertIn("40 mm", dims[0])
        self.assertIn("-0.1 mm", dims[0])
        self.assertIn("+0.1 mm", dims[0])

    def test_datums(self):
        d = labels(self.pmi, "datum")
        self.assertIn("Datum A", d)
        self.assertIn("Datum B", d)
        self.assertEqual(sum(1 for l in d if "Feature" in l), 2)

    def test_annotation_text(self):
        ann = " | ".join(labels(self.pmi, "annotation"))
        self.assertIn("CRITICAL MATING FACE", ann)

    def test_saved_views(self):
        views = " | ".join(labels(self.pmi, "view"))
        self.assertIn("Default PMI view", views)
        self.assertIn("MBD view: tolerances", views)

    def test_notes(self):
        notes = " | ".join(labels(self.pmi, "note"))
        self.assertIn("17-4PH", notes)
        self.assertIn("material spec", notes)

    def test_nothing_pmi_left_unlisted(self):
        # every PMI-pattern entity is either an item, support machinery
        # (visited), or style — the 'other' bucket is the only fallthrough,
        # and for the sample everything should be interpreted
        self.assertEqual(labels(self.pmi, "other"), [])


class TestPMIFallback(unittest.TestCase):
    def test_unknown_pmi_type_listed_as_other(self):
        res = audit_bytes(
            b"ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            b"#1=EXOTIC_NEW_TOLERANCE_ZONE('zone Z1','per spec QQ-1');\n"
            b"ENDSEC;END-ISO-10303-21;")
        m = extract_pmi(res)
        other = m.categories["other"]
        self.assertEqual(len(other), 1)
        self.assertFalse(other[0].interpreted)
        self.assertIn("zone Z1", other[0].label)

    def test_no_pmi_is_empty(self):
        res = audit_bytes(
            b"ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            b"#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
            b"ENDSEC;END-ISO-10303-21;")
        self.assertTrue(extract_pmi(res).empty)


class TestTessellatedPMI(unittest.TestCase):
    def test_tessellated_curve_set_rendered(self):
        res = audit_bytes(
            b"ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            b"#1=CARTESIAN_POINT_LIST_3D('',((0.,0.,0.),(1.,0.,0.),"
            b"(1.,1.,0.),(0.,1.,0.)));\n"
            b"#2=TESSELLATED_CURVE_SET('frame',#1,((1,2,3,4,1),(1,3)));\n"
            b"ENDSEC;END-ISO-10303-21;")
        m = extract_wireframe(res)
        tess = [pl for pl in m.polylines if pl.kind == "tess"]
        self.assertEqual(len(tess), 2)
        self.assertEqual(len(tess[0].points), 5)   # closed strip
        self.assertEqual(len(tess[1].points), 2)   # diagonal

    def test_triangulated_face_set_still_works(self):
        res = audit_bytes(
            b"ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            b"#1=COORDINATES_LIST('',3,((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)));\n"
            b"#2=TRIANGULATED_FACE_SET('',#1,1,$,$,((1,2,3)));\n"
            b"ENDSEC;END-ISO-10303-21;")
        m = extract_wireframe(res)
        tess = [pl for pl in m.polylines if pl.kind == "tess"]
        self.assertEqual(len(tess), 3)   # three unique triangle edges


class TestReportPMI(unittest.TestCase):
    def test_report_contains_pmi_section(self):
        from step_inspector.report import build_report
        res = audit_file(SAMPLE)
        rpt = build_report(res, include_map=False)
        self.assertIn("PMI / GD&T", rpt)
        self.assertIn("Flatness 0.05 mm", rpt)
        self.assertIn("datums A-B", rpt)
        self.assertIn("Datum A", rpt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
