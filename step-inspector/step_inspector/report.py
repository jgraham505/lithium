"""Plain-text audit report generation (GUI export and headless CLI)."""

from __future__ import annotations

import datetime
from collections import Counter

from .parser import AuditResult, Kind, format_value
from .steptools_bridge import SteptoolsInfo, cross_check

RULE = "=" * 72


def coverage_summary(res: AuditResult) -> list:
    by = res.bytes_by_kind()
    total = res.total_bytes or 1
    rows = []
    labels = {
        Kind.ENTITY: "Data entities (#id = ...)",
        Kind.HEADER: "Header records",
        Kind.STRUCTURE: "File structure markers",
        Kind.COMMENT: "Comments (review!)",
        Kind.WHITESPACE: "Whitespace / formatting",
        Kind.ORPHAN: "ORPHANED / unrecognized (review!)",
    }
    for kind in (Kind.ENTITY, Kind.HEADER, Kind.STRUCTURE, Kind.COMMENT,
                 Kind.WHITESPACE, Kind.ORPHAN):
        rows.append((labels[kind], by[kind], 100.0 * by[kind] / total))
    return rows


def build_report(res: AuditResult, st: SteptoolsInfo | None = None) -> str:
    out: list[str] = []
    w = out.append
    w(RULE)
    w("STEP FILE AUDIT REPORT")
    w(f"File:      {res.path}")
    w(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    w(RULE)

    # ---- coverage ----------------------------------------------------------
    ok = res.verify_coverage()
    w("")
    w("BYTE ACCOUNTING")
    w(f"  Total size: {res.total_bytes} bytes, {res.total_lines} lines")
    w(f"  Every byte classified: {'YES — spans tile the file exactly' if ok else 'NO (parser defect — treat file as unreviewed)'}")
    for label, nbytes, pct in coverage_summary(res):
        w(f"    {label:<38} {nbytes:>10} bytes  {pct:6.2f}%")
    orphans = res.orphan_spans()
    w(f"  Orphaned spans: {len(orphans)}")
    w(f"  Comments:       {len(res.comments)}")

    # ---- header -------------------------------------------------------------
    w("")
    w("HEADER")
    if not res.header:
        w("  (no header records found)")
    for h in res.header:
        if h.params is not None:
            args = ", ".join(format_value(v, 200) for v in h.params)
        else:
            args = f"<unparsed: {h.raw_params}>"
        w(f"  {h.type_name}({args})")

    # ---- entity inventory ----------------------------------------------------
    w("")
    w("ENTITY INVENTORY")
    counts = Counter(e.type_name for e in res.entities.values())
    w(f"  {len(res.entities)} entity instances, {len(counts)} distinct types")
    for name, n in counts.most_common():
        w(f"    {n:>6}  {name}")

    # ---- steptools cross-check -------------------------------------------------
    w("")
    w("SECOND READER (steptools library)")
    if st is None:
        w("  not run")
    elif not st.ok:
        w(f"  unavailable: {st.error}")
    else:
        w(f"  schema: {st.schema_name} ({st.schema_type})")
        cc = cross_check(res.entities.keys(), st)
        if cc is None:
            w("  no entity list returned")
        elif cc.clean:
            w(f"  entity sets MATCH: both readers saw the same "
              f"{cc.common} instances")
        else:
            w(f"  MISMATCH — common: {cc.common}")
            if cc.only_in_audit:
                w(f"    seen only by audit parser: "
                  f"{', '.join('#%d' % i for i in cc.only_in_audit[:50])}")
            if cc.only_in_steptools:
                w(f"    seen only by steptools: "
                  f"{', '.join('#%d' % i for i in cc.only_in_steptools[:50])}")

    # ---- review items --------------------------------------------------------
    w("")
    w("ITEMS REQUIRING REVIEW")
    flagged = [a for a in res.attention]
    if not flagged:
        w("  none")
    for a in flagged:
        w(f"  [{a.severity.upper():<7}] {a.where}: {a.message}")
        if a.excerpt:
            w(f"            > {a.excerpt}")

    # ---- orphan dump ------------------------------------------------------------
    if orphans:
        w("")
        w("ORPHANED DATA (full text)")
        for s in orphans:
            w(f"  -- line {res.line_of(s.start)} ({s.note}) " + "-" * 20)
            for line in s.text(res.source).splitlines():
                w(f"  | {line}")

    # ---- strings ------------------------------------------------------------------
    w("")
    w(f"ALL TEXT STRINGS IN FILE ({len(res.strings)})")
    for s in res.strings:
        flag = "" if s.value.clean else "  [escape-decode incomplete!]"
        w(f"  line {s.line:>5}  {s.location}: {s.value.decoded!r}{flag}")

    w("")
    w(RULE)
    w("End of report")
    return "\n".join(out) + "\n"
