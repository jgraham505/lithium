"""Plain-text audit report generation (GUI export and headless CLI)."""

from __future__ import annotations

import datetime
from collections import Counter

from .parser import AuditResult, HeaderEntity, Kind, format_value
from .steptools_bridge import SteptoolsInfo, cross_check

RULE = "=" * 72

#: Where each span category is surfaced in the application.
KIND_DESTINATION = {
    Kind.ENTITY: "consumed: data model (Entities tab)",
    Kind.HEADER: "consumed: header metadata (Overview tab)",
    Kind.STRUCTURE: "consumed: file structure",
    Kind.COMMENT: "NOT consumed — review queue (Comments & orphans tab)",
    Kind.ORPHAN: "NOT consumed — ORPHANED, review queue",
    Kind.WHITESPACE: "formatting only",
}


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


def _map_row(res: AuditResult, s) -> str:
    """One triage-map line: byte range, line(s), category, identity, fate."""
    l1 = res.line_of(s.start)
    l2 = res.line_of(max(s.end - 1, s.start))
    lines = f"line {l1}" if l1 == l2 else f"lines {l1}-{l2}"
    if s.kind is Kind.ENTITY and s.ref is not None:
        what = f"#{s.ref.eid} {s.ref.type_name}"
        if s.ref.parse_error:
            what += "  [params unparsed — flagged]"
    elif s.kind is Kind.HEADER and isinstance(s.ref, HeaderEntity):
        what = s.ref.type_name
    elif s.kind is Kind.COMMENT:
        text = (s.ref or "").strip().replace("\n", " ")
        what = f"comment: {text[:60]!r}"
    elif s.kind is Kind.ORPHAN:
        what = s.note
    else:
        what = s.note
    return (f"{s.start:>9}-{s.end:<9} {lines:<16} {s.kind.value:<10} "
            f"{what:<46} -> {KIND_DESTINATION[s.kind]}")


def build_report(res: AuditResult, st: SteptoolsInfo | None = None,
                 include_map: bool = True) -> str:
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

    # ---- triage map -----------------------------------------------------------
    if include_map:
        w("")
        w("TRIAGE MAP — where every byte of the file went")
        w("  Byte offsets are 0-based, end-exclusive.  Whitespace spans are")
        w("  omitted as rows; together with the listed spans they tile the")
        w("  file with no gaps (see the totals below the map).")
        ws_spans = ws_bytes = 0
        for s in res.spans:
            if s.kind is Kind.WHITESPACE:
                ws_spans += 1
                ws_bytes += s.end - s.start
                continue
            w("  " + _map_row(res, s))
        w(f"  (whitespace omitted: {ws_spans} span(s), {ws_bytes} bytes)")
        listed = sum(s.end - s.start for s in res.spans
                     if s.kind is not Kind.WHITESPACE)
        w(f"  listed {listed} bytes + whitespace {ws_bytes} bytes = "
          f"{listed + ws_bytes} of {res.total_bytes} file bytes"
          + ("  ✓ complete" if listed + ws_bytes == res.total_bytes
             else "  ✗ INCOMPLETE"))

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
