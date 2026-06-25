"""Theme palette integrity (no display required)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step_inspector.gui.theme import DARK, LIGHT, PALETTES
from step_inspector.parser import Kind


class TestPalettes(unittest.TestCase):
    def test_both_themes_present(self):
        self.assertEqual(set(PALETTES), {"light", "dark"})
        self.assertFalse(LIGHT.is_dark)
        self.assertTrue(DARK.is_dark)

    def test_kind_colors_cover_viewer_kinds(self):
        kinds = {"line", "circle", "ellipse", "spline", "polyline",
                 "tess", "approx"}
        for pal in (LIGHT, DARK):
            self.assertEqual(set(pal.kind_colors()), kinds)

    def test_span_backgrounds_cover_classified_kinds(self):
        classified = {k.value for k in Kind if k is not Kind.WHITESPACE}
        for pal in (LIGHT, DARK):
            self.assertEqual(set(pal.span_backgrounds()), classified)

    def test_colors_are_hex(self):
        for pal in (LIGHT, DARK):
            for c in pal.kind_colors().values():
                self.assertRegex(c, r"^#[0-9a-fA-F]{6}$")

    def test_qss_builds_for_both(self):
        from step_inspector.gui import theme
        for pal in (LIGHT, DARK):
            css = theme.qss(pal)
            self.assertIsInstance(css, str)
            self.assertIn(pal.accent, css)
            self.assertIn(pal.window, css)
            self.assertIn("QTabBar::tab", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
