"""Entry point: ``python -m step_inspector [options] [file.step]``."""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="step-inspector",
        description="Audit-grade GUI viewer for STEP (ISO 10303-21) files. "
                    "Classifies every byte of the file and flags anything "
                    "not consumed into the data model as orphaned data.")
    ap.add_argument("file", nargs="?", help="STEP file to open")
    ap.add_argument("--report", action="store_true",
                    help="print a plain-text audit report to stdout instead "
                         "of opening the GUI (requires FILE)")
    ap.add_argument("--no-steptools", action="store_true",
                    help="skip the steptools second-reader cross-check")
    ap.add_argument("--no-map", action="store_true",
                    help="omit the span-by-span triage map from --report "
                         "output (keeps reports short for very large files)")
    ap.add_argument("--theme", choices=("light", "dark"), default="light",
                    help="initial GUI theme (default: light; toggle in View ▸ "
                         "Theme)")
    ap.add_argument("--shaded", action="store_true",
                    help="with --report, also tessellate with OpenCASCADE and "
                         "include the shaded-geometry accounting (needs "
                         "pythonocc-core)")
    args = ap.parse_args(argv)

    if args.report:
        if not args.file:
            ap.error("--report requires a STEP file argument")
        from .parser import audit_file
        from .report import build_report
        from . import steptools_bridge as sb
        res = audit_file(args.file)
        st = None if args.no_steptools else sb.load(args.file)
        occ_result = None
        if args.shaded:
            from . import occ_backend
            occ_result = occ_backend.load_and_mesh(args.file)
        sys.stdout.write(build_report(res, st, include_map=not args.no_map,
                                      occ_result=occ_result))
        return 0 if res.verify_coverage() else 2

    from .gui.app import run
    run(args.file, use_steptools=not args.no_steptools,
        theme_name=args.theme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
