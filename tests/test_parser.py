"""Tests for the audit parser: coverage guarantees, orphan detection,
string decoding, and geometry extraction."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step_inspector.parser import (Binary, EnumVal, Kind, Ref, Typed,
                                   audit_bytes, audit_file, decode_p21_string,
                                   parse_params)
from step_inspector.geometry import extract_wireframe

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "samples", "cube_demo.step")


def make(body: str) -> bytes:
    return body.encode("latin-1")


class TestParamParsing(unittest.TestCase):
    def test_scalars(self):
        vals = parse_params("1,2.5,-3,1.0E-6,'hi',.STEEL.,#42,$,*")
        self.assertEqual(vals[0], 1)
        self.assertEqual(vals[1], 2.5)
        self.assertEqual(vals[2], -3)
        self.assertAlmostEqual(vals[3], 1e-6)
        self.assertEqual(vals[4].decoded, "hi")
        self.assertEqual(vals[5], EnumVal("STEEL"))
        self.assertEqual(vals[6], Ref(42))
        self.assertEqual(repr(vals[7]), "$")
        self.assertEqual(repr(vals[8]), "*")

    def test_nested_lists_and_typed(self):
        vals = parse_params("('a',(1,(2,3)),PARAMETER_VALUE(0.5))")
        outer = vals[0]
        self.assertEqual(outer[1], [1, [2, 3]])
        self.assertEqual(outer[2], Typed("PARAMETER_VALUE", 0.5))

    def test_string_with_quotes_and_semicolons(self):
        vals = parse_params("'it''s; tricky, (really)'")
        self.assertEqual(vals[0].decoded, "it's; tricky, (really)")

    def test_binary(self):
        vals = parse_params('"0FF1CE"')
        self.assertEqual(vals[0], Binary("0FF1CE"))

    def test_empty_list(self):
        self.assertEqual(parse_params("()"), [[]])


class TestStringDecoding(unittest.TestCase):
    def test_x2_utf16(self):
        s = decode_p21_string(r"caf\X2\00E9\X0\ 25\X2\00B0\X0\C")
        self.assertEqual(s.decoded, "café 25°C")
        self.assertTrue(s.clean)

    def test_s_directive(self):
        s = decode_p21_string(r"\S\d")          # 0x64+0x80 = 0xE4 = ä
        self.assertEqual(s.decoded, "ä")

    def test_x_directive(self):
        self.assertEqual(decode_p21_string(r"\X\E4").decoded, "ä")

    def test_unknown_escape_flagged(self):
        s = decode_p21_string(r"weird \Q\ thing")
        self.assertFalse(s.clean)


class TestCoverage(unittest.TestCase):
    def test_sample_full_coverage(self):
        res = audit_file(SAMPLE)
        self.assertTrue(res.verify_coverage())
        self.assertEqual(sum(res.bytes_by_kind().values()), res.total_bytes)

    def test_sample_known_orphans(self):
        res = audit_file(SAMPLE)
        notes = [s.note for s in res.orphan_spans()]
        self.assertEqual(len(notes), 2)
        self.assertIn("unrecognized statement (DATA)", notes)
        self.assertIn("data after END-ISO-10303-21", notes)

    def test_sample_comments_found(self):
        res = audit_file(SAMPLE)
        texts = " ".join(c.text for c in res.comments)
        self.assertIn("INTERNAL BUILD", texts)
        self.assertEqual(len(res.comments), 3)

    def test_sample_x2_string_decoded(self):
        res = audit_file(SAMPLE)
        joined = " ".join(s.value.decoded for s in res.strings)
        self.assertIn("µm Ra 0.8", joined)
        self.assertIn("482°C", joined)

    def test_every_entity_has_span(self):
        res = audit_file(SAMPLE)
        ids_from_spans = {s.ref.eid for s in res.spans
                          if s.kind is Kind.ENTITY}
        self.assertEqual(ids_from_spans, set(res.entities))

    def test_no_dangling_refs_in_sample(self):
        res = audit_file(SAMPLE)
        for target in res.referenced_by:
            self.assertIn(target, res.entities)


class TestTrickyInputs(unittest.TestCase):
    def test_semicolon_inside_string(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "#1=PRODUCT('a;b','x','y',());\n"
            "ENDSEC;END-ISO-10303-21;\n"))
        self.assertTrue(res.verify_coverage())
        self.assertEqual(res.entities[1].params[0].decoded, "a;b")
        self.assertFalse(res.orphan_spans())

    def test_comment_containing_fake_entity(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "/* #99=SECRET('do not parse'); */\n"
            "#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
            "ENDSEC;END-ISO-10303-21;\n"))
        self.assertTrue(res.verify_coverage())
        self.assertEqual(set(res.entities), {1})
        self.assertEqual(len(res.comments), 1)
        self.assertIn("SECRET", res.comments[0].text)

    def test_unterminated_comment_is_orphan(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n/* runs off the end"))
        self.assertTrue(res.verify_coverage())
        self.assertTrue(any("unterminated comment" in s.note
                            for s in res.orphan_spans()))

    def test_garbage_is_orphaned_not_lost(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "this is just prose, not step data;\n"
            "#1=CARTESIAN_POINT('',(1.,2.,3.));\n"
            "ENDSEC;END-ISO-10303-21;"))
        self.assertTrue(res.verify_coverage())
        orphans = res.orphan_spans()
        self.assertEqual(len(orphans), 1)
        self.assertIn("prose", orphans[0].text(res.source))
        self.assertIn(1, res.entities)

    def test_missing_terminator_at_eof(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n#1=THING(1"))
        self.assertTrue(res.verify_coverage())
        self.assertTrue(res.orphan_spans())

    def test_complex_instance(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "#7=(NAMED_UNIT(*)SI_UNIT($,.METRE.)LENGTH_UNIT());\n"
            "ENDSEC;END-ISO-10303-21;"))
        e = res.entities[7]
        self.assertTrue(e.is_complex)
        self.assertEqual([r.type_name for r in e.records],
                         ["NAMED_UNIT", "SI_UNIT", "LENGTH_UNIT"])
        self.assertEqual(e.records[1].params[1], EnumVal("METRE"))

    def test_unparsable_params_kept_with_error(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "#1=WIDGET(1,&&&,2);\n"
            "ENDSEC;END-ISO-10303-21;"))
        self.assertTrue(res.verify_coverage())
        e = res.entities[1]
        self.assertTrue(e.parse_error)
        self.assertIn("&&&", e.records[0].raw_params)
        self.assertTrue(any("could not be fully parsed" in a.message
                            for a in res.attention))

    def test_dangling_reference_reported(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "#1=VERTEX_POINT('',#999);\n"
            "ENDSEC;END-ISO-10303-21;"))
        self.assertTrue(any("missing entity #999" in a.message
                            for a in res.attention))

    def test_empty_file(self):
        res = audit_bytes(b"")
        self.assertTrue(res.verify_coverage())
        self.assertEqual(res.total_bytes, 0)


class TestGeometry(unittest.TestCase):
    def test_cube_wireframe(self):
        res = audit_file(SAMPLE)
        m = extract_wireframe(res)
        kinds = {}
        for pl in m.polylines:
            kinds[pl.kind] = kinds.get(pl.kind, 0) + 1
        self.assertEqual(kinds.get("line"), 12)     # 12 cube edges
        self.assertEqual(kinds.get("circle"), 1)    # datum circle
        self.assertEqual(kinds.get("polyline"), 1)  # probe path
        (lo, hi) = m.bounds()
        self.assertEqual(hi[2], 50.0)

    def test_bspline_sampling(self):
        res = audit_bytes(make(
            "ISO-10303-21;HEADER;ENDSEC;DATA;\n"
            "#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
            "#2=CARTESIAN_POINT('',(1.,2.,0.));\n"
            "#3=CARTESIAN_POINT('',(2.,-2.,0.));\n"
            "#4=CARTESIAN_POINT('',(3.,0.,0.));\n"
            "#5=B_SPLINE_CURVE_WITH_KNOTS('',3,(#1,#2,#3,#4),.UNSPECIFIED.,"
            ".F.,.F.,(4,4),(0.,1.),.UNSPECIFIED.);\n"
            "ENDSEC;END-ISO-10303-21;"))
        m = extract_wireframe(res)
        spl = [pl for pl in m.polylines if pl.kind == "spline"]
        self.assertEqual(len(spl), 1)
        pts = spl[0].points
        # clamped curve interpolates its end control points
        self.assertAlmostEqual(pts[0][0], 0.0, places=6)
        self.assertAlmostEqual(pts[-1][0], 3.0, places=6)


class TestReport(unittest.TestCase):
    def test_report_sections(self):
        from step_inspector.report import build_report
        res = audit_file(SAMPLE)
        rpt = build_report(res)
        for needle in ("BYTE ACCOUNTING", "ORPHANED DATA",
                       "ALL TEXT STRINGS", "ENTITY INVENTORY",
                       "Every byte classified: YES"):
            self.assertIn(needle, rpt)
        self.assertIn("CUSTOM_TOOL_STATE", rpt)

    def test_triage_map_complete_and_optional(self):
        from step_inspector.parser import Kind
        from step_inspector.report import build_report
        res = audit_file(SAMPLE)
        rpt = build_report(res)
        self.assertIn("TRIAGE MAP", rpt)
        self.assertIn("✓ complete", rpt)
        # one row per non-whitespace span
        map_section = rpt.split("TRIAGE MAP")[1].split("ALL TEXT STRINGS")[0]
        rows = [l for l in map_section.splitlines() if " -> " in l]
        expected = sum(1 for s in res.spans if s.kind is not Kind.WHITESPACE)
        self.assertEqual(len(rows), expected)
        # orphans and comments are marked as not consumed
        self.assertIn("NOT consumed — ORPHANED", map_section)
        self.assertIn("NOT consumed — review queue", map_section)
        # map can be omitted for very large files
        self.assertNotIn("TRIAGE MAP", build_report(res, include_map=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
