"""STEP Inspector — Tkinter desktop application."""

from __future__ import annotations

import os
import threading
import traceback
from collections import Counter
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from .. import steptools_bridge as sb
from ..geometry import extract_wireframe
from ..parser import (AuditResult, Entity, Kind, Ref, Str, Typed,
                      audit_file, format_value)
from ..report import build_report, coverage_summary
from .viewer3d import KIND_COLORS, KIND_LABELS, Viewer3D

APP_TITLE = "STEP Inspector"

SPAN_TAG_STYLE = {
    Kind.STRUCTURE:  dict(background="#dfe7f5"),
    Kind.HEADER:     dict(background="#e8f3e3"),
    Kind.ENTITY:     dict(background="#ffffff"),
    Kind.COMMENT:    dict(background="#fff2c4"),
    Kind.WHITESPACE: dict(background="#ffffff"),
    Kind.ORPHAN:     dict(background="#ffd2d2"),
}
SPAN_LEGEND = [
    ("Entity data", "#ffffff"),
    ("Header", "#e8f3e3"),
    ("Structure", "#dfe7f5"),
    ("Comment — review", "#fff2c4"),
    ("Orphaned — review", "#ffd2d2"),
]

SEV_COLORS = {"orphan": "#c62828", "comment": "#a06a00",
              "warning": "#8a4baf", "info": "#33691e"}


class InspectorApp(tk.Tk):
    def __init__(self, path: str | None = None, use_steptools: bool = True):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x840")
        self.use_steptools = use_steptools
        self.res: AuditResult | None = None
        self.stinfo: sb.SteptoolsInfo | None = None
        self.wire = None
        self._build_menu()
        self._build_layout()
        if path:
            self.after(100, lambda: self.open_path(path))

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        m = tk.Menu(self)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="Open…", accelerator="Ctrl+O",
                          command=self.open_dialog)
        filem.add_command(label="Reload", accelerator="F5",
                          command=self.reload)
        filem.add_separator()
        filem.add_command(label="Export audit report…",
                          command=self.export_report)
        filem.add_separator()
        filem.add_command(label="Quit", command=self.destroy)
        m.add_cascade(label="File", menu=filem)
        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="About", command=self._about)
        m.add_cascade(label="Help", menu=helpm)
        self.config(menu=m)
        self.bind("<Control-o>", lambda e: self.open_dialog())
        self.bind("<F5>", lambda e: self.reload())

    def _build_layout(self):
        # summary banner
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        self.lbl_file = ttk.Label(top, text="No file loaded — File ▸ Open…",
                                  font=("TkDefaultFont", 10, "bold"))
        self.lbl_file.pack(side="left")
        self.lbl_cov = tk.Label(top, text="", padx=10, pady=2)
        self.lbl_cov.pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=4, pady=4)
        self.tab_overview = OverviewTab(self.nb, self)
        self.tab_entities = EntitiesTab(self.nb, self)
        self.tab_geometry = GeometryTab(self.nb, self)
        self.tab_audit = AuditTab(self.nb, self)
        self.tab_review = ReviewTab(self.nb, self)
        self.tab_strings = StringsTab(self.nb, self)
        self.nb.add(self.tab_overview, text=" Overview ")
        self.nb.add(self.tab_entities, text=" Entities ")
        self.nb.add(self.tab_geometry, text=" Geometry ")
        self.nb.add(self.tab_audit, text=" File audit ")
        self.nb.add(self.tab_review, text=" Comments & orphans ")
        self.nb.add(self.tab_strings, text=" Strings ")

        self.status = ttk.Label(self, anchor="w", padding=(8, 2))
        self.status.pack(fill="x", side="bottom")

    def _about(self):
        messagebox.showinfo(
            "About " + APP_TITLE,
            "STEP Inspector\n\n"
            "Reviews STEP (ISO 10303-21) files for proprietary information.\n"
            "Every byte of the file is classified; anything not consumed\n"
            "into the data model is shown as orphaned data.\n\n"
            "Second-reader cross-check powered by the steptools library\n"
            "(STEP Tools, Inc.) when a license is available.")

    # ------------------------------------------------------------- loading
    def open_dialog(self):
        path = filedialog.askopenfilename(
            title="Open STEP file",
            filetypes=[("STEP files", "*.step *.stp *.p21 *.STEP *.STP"),
                       ("All files", "*.*")])
        if path:
            self.open_path(path)

    def reload(self):
        if self.res:
            self.open_path(self.res.path)

    def open_path(self, path: str):
        self.status.config(text=f"Reading {path} …")
        self.lbl_file.config(text=os.path.basename(path) + "  (loading…)")

        def work():
            try:
                res = audit_file(path)
                wire = extract_wireframe(res)
                st = sb.load(path) if self.use_steptools else None
            except Exception:
                err = traceback.format_exc()
                self.after(0, lambda: self._load_failed(path, err))
                return
            self.after(0, lambda: self._loaded(res, wire, st))

        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, path, err):
        self.status.config(text="Load failed")
        self.lbl_file.config(text=os.path.basename(path))
        messagebox.showerror(APP_TITLE, f"Could not read {path}:\n\n{err}")

    def _loaded(self, res: AuditResult, wire, st):
        self.res = res
        self.wire = wire
        self.stinfo = st
        nrev = len([a for a in res.attention
                    if a.severity in ("orphan", "comment")])
        self.lbl_file.config(
            text=f"{res.path}   —   {len(res.entities)} entities, "
                 f"{res.total_lines} lines, {res.total_bytes} bytes")
        ok = res.verify_coverage()
        orphans = len(res.orphan_spans())
        if not ok:
            self.lbl_cov.config(text="✗ COVERAGE FAILURE — parser gap",
                                bg="#c62828", fg="white")
        elif orphans:
            self.lbl_cov.config(
                text=f"⚠ every byte read — {orphans} orphaned span(s) and "
                     f"{len(res.comments)} comment(s) need review",
                bg="#ef6c00", fg="white")
        elif res.comments:
            self.lbl_cov.config(
                text=f"⚠ every byte read — {len(res.comments)} comment(s) "
                     "need review", bg="#f9a825", fg="black")
        else:
            self.lbl_cov.config(text="✓ every byte read and consumed",
                                bg="#2e7d32", fg="white")
        for tab in (self.tab_overview, self.tab_entities, self.tab_geometry,
                    self.tab_audit, self.tab_review, self.tab_strings):
            tab.populate()
        self.status.config(
            text=f"Loaded. {nrev} item(s) flagged for proprietary-information"
                 f" review — see 'Comments & orphans'.")

    # ------------------------------------------------------------- actions
    def export_report(self):
        if not self.res:
            messagebox.showinfo(APP_TITLE, "Open a STEP file first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export audit report",
            defaultextension=".txt",
            initialfile=os.path.basename(self.res.path) + ".audit.txt",
            filetypes=[("Text report", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_report(self.res, self.stinfo))
        self.status.config(text=f"Report written to {path}")

    def goto_entity(self, eid: int):
        self.nb.select(self.tab_entities)
        self.tab_entities.select_entity(eid)

    def goto_line(self, line: int):
        self.nb.select(self.tab_audit)
        self.tab_audit.goto_line(line)


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------

class OverviewTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master, padding=8)
        self.app = app
        self.text = tk.Text(self, wrap="word", state="disabled",
                            font=("TkFixedFont", 10), relief="flat",
                            background=self.winfo_toplevel()["background"])
        ys = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("h", font=("TkDefaultFont", 11, "bold"),
                                spacing1=10, spacing3=4)
        self.text.tag_configure("k", font=("TkFixedFont", 10, "bold"))
        self.text.tag_configure("warn", foreground="#c62828")
        self.text.tag_configure("good", foreground="#2e7d32")

    def populate(self):
        res, st = self.app.res, self.app.stinfo
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")

        def w(s="", tag=None):
            t.insert("end", s + "\n", tag)

        w("File", "h")
        w(f"  Path:  {res.path}")
        w(f"  Size:  {res.total_bytes} bytes / {res.total_lines} lines")
        w(f"  Schema (declared): {', '.join(res.schema_names) or '—'}")

        w("Byte accounting — proof the whole file was read", "h")
        ok = res.verify_coverage()
        if ok:
            w("  ✓ Classified spans tile the file exactly: every byte was "
              "read and categorized.", "good")
        else:
            w("  ✗ Coverage verification FAILED — do not rely on this "
              "review.", "warn")
        for label, nbytes, pct in coverage_summary(res):
            tag = "warn" if ("review" in label and nbytes) else None
            w(f"    {label:<38} {nbytes:>10} bytes  {pct:6.2f}%", tag)

        w("Header records (often contain proprietary metadata)", "h")
        fn = res.header_entity("FILE_NAME")
        labels = ["name", "time stamp", "author", "organization",
                  "preprocessor version", "originating system",
                  "authorization"]
        if fn and fn.params is not None:
            for i, lbl in enumerate(labels):
                v = format_value(fn.params[i], 400) if i < len(fn.params) else "—"
                w(f"  {lbl:<22} {v}")
        fd = res.header_entity("FILE_DESCRIPTION")
        if fd and fd.params is not None and fd.params:
            w(f"  {'description':<22} {format_value(fd.params[0], 400)}")
        for h in res.header:
            if h.type_name not in ("FILE_NAME", "FILE_DESCRIPTION",
                                   "FILE_SCHEMA"):
                w(f"  {h.type_name}({h.raw_params})", "warn")

        w("Contents", "h")
        counts = Counter(e.type_name for e in res.entities.values())
        w(f"  {len(res.entities)} entity instances across "
          f"{len(counts)} types; top types:")
        for name, n in counts.most_common(12):
            w(f"    {n:>6}  {name}")

        w("Second reader — steptools library cross-check", "h")
        if st is None:
            w("  steptools check disabled.")
        elif not st.ok:
            w(f"  {st.error}", "warn")
            w("  The audit above is independent of steptools and remains "
              "complete.")
        else:
            w(f"  schema recognized: {st.schema_name} ({st.schema_type})")
            cc = sb.cross_check(res.entities.keys(), st)
            if cc and cc.clean:
                w(f"  ✓ both readers saw the same {cc.common} entity "
                  "instances.", "good")
            elif cc:
                w("  ✗ entity sets differ:", "warn")
                if cc.only_in_audit:
                    w("    only audit parser: " +
                      ", ".join(f"#{i}" for i in cc.only_in_audit[:30]), "warn")
                if cc.only_in_steptools:
                    w("    only steptools: " +
                      ", ".join(f"#{i}" for i in cc.only_in_steptools[:30]),
                      "warn")

        sev = Counter(a.severity for a in res.attention)
        w("Review queue", "h")
        w(f"  orphaned data: {sev.get('orphan', 0)}   comments: "
          f"{sev.get('comment', 0)}   warnings: {sev.get('warning', 0)}   "
          f"info: {sev.get('info', 0)}")
        w("  Open the 'Comments & orphans' tab to review each item.")
        t.configure(state="disabled")


# ---------------------------------------------------------------------------
# Entities tab
# ---------------------------------------------------------------------------

class EntitiesTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master)
        self.app = app
        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True)

        # ---- left: type/instance tree
        left = ttk.Frame(pan)
        pan.add(left, weight=1)
        fl = ttk.Frame(left)
        fl.pack(fill="x", padx=2, pady=2)
        ttk.Label(fl, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        ent = ttk.Entry(fl, textvariable=self.filter_var)
        ent.pack(side="left", fill="x", expand=True, padx=4)
        self.filter_var.trace_add("write", lambda *_: self._rebuild_tree())
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        ys = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_open)

        # ---- right: detail panes
        right = ttk.PanedWindow(pan, orient="vertical")
        pan.add(right, weight=3)

        attrf = ttk.LabelFrame(right, text="Attributes (double-click a #ref "
                                           "to follow it)")
        self.attrs = ttk.Treeview(attrf, columns=("value",), show="tree headings")
        self.attrs.heading("#0", text="attribute")
        self.attrs.heading("value", text="value")
        self.attrs.column("#0", width=240, stretch=False)
        ysa = ttk.Scrollbar(attrf, command=self.attrs.yview)
        self.attrs.configure(yscrollcommand=ysa.set)
        ysa.pack(side="right", fill="y")
        self.attrs.pack(fill="both", expand=True)
        self.attrs.bind("<Double-1>", self._follow_ref)
        right.add(attrf, weight=3)

        midf = ttk.PanedWindow(right, orient="horizontal")
        usedf = ttk.LabelFrame(midf, text="Referenced by")
        self.usedby = tk.Listbox(usedf, font=("TkFixedFont", 9))
        self.usedby.pack(fill="both", expand=True)
        self.usedby.bind("<Double-1>", self._follow_usedby)
        midf.add(usedf, weight=1)
        rawf = ttk.LabelFrame(midf, text="Raw source text")
        self.raw = tk.Text(rawf, height=6, wrap="word",
                           font=("TkFixedFont", 9), state="disabled")
        self.raw.pack(fill="both", expand=True)
        midf.add(rawf, weight=2)
        right.add(midf, weight=2)

        stf = ttk.LabelFrame(right, text="steptools (EXPRESS/ARM) view")
        self.stview = tk.Text(stf, height=6, wrap="word",
                              font=("TkFixedFont", 9), state="disabled")
        self.stview.pack(fill="both", expand=True)
        right.add(stf, weight=2)

        self._type_nodes: dict[str, str] = {}
        self._placeholders: set[str] = set()

    # -- tree construction ---------------------------------------------------
    def populate(self):
        self._rebuild_tree()
        self._show_entity(None)

    def _rebuild_tree(self):
        res = self.app.res
        self.tree.delete(*self.tree.get_children())
        self._type_nodes.clear()
        self._placeholders.clear()
        if not res:
            return
        flt = self.filter_var.get().strip().upper()
        groups: dict[str, list[int]] = {}
        for eid in res.entity_order:
            e = res.entities[eid]
            if flt and flt not in e.type_name and flt != f"#{eid}" \
                    and flt not in str(eid):
                continue
            groups.setdefault(e.type_name, []).append(eid)
        for name in sorted(groups):
            node = self.tree.insert("", "end",
                                    text=f"{name}  ({len(groups[name])})",
                                    values=(), open=False,
                                    tags=("type",))
            self._type_nodes[name] = node
            ph = self.tree.insert(node, "end", text="…")
            self._placeholders.add(ph)
            self.tree.item(ph, tags=("placeholder", name))

    def _on_open(self, _ev):
        node = self.tree.focus()
        children = self.tree.get_children(node)
        if len(children) == 1 and children[0] in self._placeholders:
            ph = children[0]
            tags = self.tree.item(ph, "tags")
            type_name = tags[1] if len(tags) > 1 else ""
            self.tree.delete(ph)
            self._placeholders.discard(ph)
            res = self.app.res
            flt = self.filter_var.get().strip().upper()
            count = 0
            for eid in res.entity_order:
                e = res.entities[eid]
                if e.type_name != type_name:
                    continue
                if flt and flt not in e.type_name and flt != f"#{eid}" \
                        and flt not in str(eid):
                    continue
                label = f"#{eid}  {self._summary(e)}"
                self.tree.insert(node, "end", text=label,
                                 tags=("inst", str(eid)))
                count += 1
                if count >= 5000:
                    self.tree.insert(node, "end",
                                     text=f"… ({type_name}: list truncated)")
                    break

    @staticmethod
    def _summary(e: Entity) -> str:
        p = e.params
        if p:
            first = p[0]
            if isinstance(first, Str) and first.decoded:
                return repr(first.decoded[:40])
        return ""

    def _on_select(self, _ev):
        node = self.tree.focus()
        tags = self.tree.item(node, "tags")
        if tags and tags[0] == "inst":
            self._show_entity(int(tags[1]))

    def select_entity(self, eid: int):
        res = self.app.res
        e = res.entities.get(eid)
        if not e:
            return
        node = self._type_nodes.get(e.type_name)
        if node:
            self.tree.item(node, open=True)
            self.tree.focus(node)
            self._on_open(None)
            for child in self.tree.get_children(node):
                tags = self.tree.item(child, "tags")
                if tags and tags[0] == "inst" and int(tags[1]) == eid:
                    self.tree.selection_set(child)
                    self.tree.focus(child)
                    self.tree.see(child)
                    break
        self._show_entity(eid)

    # -- detail panes ------------------------------------------------------
    def _show_entity(self, eid):
        self.attrs.delete(*self.attrs.get_children())
        self.usedby.delete(0, "end")
        for txt in (self.raw, self.stview):
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.configure(state="disabled")
        if eid is None or not self.app.res:
            return
        res = self.app.res
        e = res.entities.get(eid)
        if not e:
            return
        # attributes
        root = self.attrs.insert("", "end", text=f"#{eid} {e.type_name}",
                                 values=("",), open=True)
        if e.parse_error:
            self.attrs.insert(root, "end", text="⚠ parse error",
                              values=(e.parse_error,))
        for rec in e.records:
            parent = root
            if e.is_complex:
                parent = self.attrs.insert(root, "end", text=rec.type_name,
                                           values=("",), open=True)
            if rec.params is None:
                self.attrs.insert(parent, "end", text="(raw)",
                                  values=(rec.raw_params,))
            else:
                for i, v in enumerate(rec.params):
                    self._add_value(parent, f"[{i}]", v)
        # referenced by
        for src in sorted(res.referenced_by.get(eid, ())):
            se = res.entities.get(src)
            self.usedby.insert("end",
                               f"#{src} {se.type_name if se else '?'}")
        # raw text
        self.raw.configure(state="normal")
        self.raw.insert("1.0",
                        f"(line {res.line_of(e.span.start)})\n"
                        + e.span.text(res.source))
        self.raw.configure(state="disabled")
        # steptools view
        self.stview.configure(state="normal")
        st = self.app.stinfo
        if st is None:
            self.stview.insert("1.0", "steptools check disabled")
        elif not st.ok:
            self.stview.insert("1.0", st.error)
        else:
            attrs = st.attributes_of(eid)
            if not attrs:
                self.stview.insert("1.0",
                                   "entity not visible to steptools")
            else:
                self.stview.insert(
                    "1.0", "\n".join(f"{k} = {v}" for k, v in attrs.items()))
        self.stview.configure(state="disabled")

    def _add_value(self, parent, label, v, depth=0):
        res = self.app.res
        if isinstance(v, Ref):
            t = res.entities.get(v.eid)
            tname = t.type_name if t else "MISSING!"
            self.attrs.insert(parent, "end", text=label,
                              values=(f"#{v.eid} → {tname}",),
                              tags=("ref", str(v.eid)))
        elif isinstance(v, list):
            node = self.attrs.insert(parent, "end", text=label,
                                     values=(f"list of {len(v)}",),
                                     open=depth < 1 and len(v) <= 16)
            for i, x in enumerate(v[:2000]):
                self._add_value(node, f"[{i}]", x, depth + 1)
        elif isinstance(v, Typed):
            node = self.attrs.insert(parent, "end", text=label,
                                     values=(v.name,), open=True)
            self._add_value(node, "value", v.value, depth + 1)
        elif isinstance(v, Str):
            extra = "" if v.clean else "   ⚠ raw: " + v.raw
            self.attrs.insert(parent, "end", text=label,
                              values=(f"'{v.decoded}'{extra}",))
        else:
            self.attrs.insert(parent, "end", text=label,
                              values=(format_value(v, 200),))

    def _follow_ref(self, _ev):
        node = self.attrs.focus()
        tags = self.attrs.item(node, "tags")
        if tags and tags[0] == "ref":
            self.select_entity(int(tags[1]))

    def _follow_usedby(self, _ev):
        sel = self.usedby.curselection()
        if sel:
            txt = self.usedby.get(sel[0])
            eid = int(txt.split()[0][1:])
            self.select_entity(eid)


# ---------------------------------------------------------------------------
# Geometry tab
# ---------------------------------------------------------------------------

class GeometryTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="Fit view",
                   command=lambda: self.viewer.fit()).pack(side="left")
        self.vars = {}
        for layer, label in (("edges", "Edges/curves"),
                             ("vertices", "Vertices"),
                             ("points", "Free points"),
                             ("axes", "Axes")):
            v = tk.BooleanVar(value=True)
            self.vars[layer] = v
            ttk.Checkbutton(
                bar, text=label, variable=v,
                command=lambda l=layer, v=v: self.viewer.set_visible(
                    l, v.get())).pack(side="left", padx=6)
        self.stats = ttk.Label(bar, text="")
        self.stats.pack(side="right")
        self.viewer = Viewer3D(self)
        self.viewer.pack(fill="both", expand=True)
        legend = ttk.Frame(self, padding=(6, 2))
        legend.pack(fill="x")
        ttk.Label(legend, text="Drag: rotate   Right-drag: pan   "
                               "Wheel: zoom    ").pack(side="left")
        for kind, color in KIND_COLORS.items():
            f = tk.Frame(legend, width=10, height=10, bg=color)
            f.pack(side="left", padx=(8, 2))
            ttk.Label(legend, text=KIND_LABELS[kind]).pack(side="left")

    def populate(self):
        wire = self.app.wire
        self.viewer.set_model(wire)
        if wire is None:
            self.stats.config(text="")
            return
        kinds = Counter(pl.kind for pl in wire.polylines)
        parts = [f"{n} {KIND_LABELS.get(k, k)}" for k, n in
                 kinds.most_common()]
        if wire.free_points:
            parts.append(f"{len(wire.free_points)} free points")
        approx = kinds.get("approx", 0)
        note = ("   ⚠ some curves drawn as straight chords"
                if approx else "")
        self.stats.config(text=", ".join(parts) + note if parts
                          else "no wireframe geometry found")


# ---------------------------------------------------------------------------
# File audit tab (full classified source listing)
# ---------------------------------------------------------------------------

class AuditTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="Next review item ▼",
                   command=self.next_review).pack(side="left")
        for label, color in SPAN_LEGEND:
            f = tk.Frame(bar, width=12, height=12, bg=color,
                         relief="solid", borderwidth=1)
            f.pack(side="left", padx=(10, 3))
            ttk.Label(bar, text=label).pack(side="left")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="none", font=("TkFixedFont", 9),
                            state="disabled")
        ys = ttk.Scrollbar(body, command=self.text.yview)
        xs = ttk.Scrollbar(body, orient="horizontal",
                           command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        for kind, style in SPAN_TAG_STYLE.items():
            self.text.tag_configure("k_" + kind.value, **style)
        self.text.tag_configure("k_orphan", background="#ffd2d2",
                                foreground="#7f0000")
        self.text.tag_configure("lineno", foreground="#999999",
                                background="#f4f4f4")
        self.text.bind("<Double-1>", self._jump_entity)
        self._review_marks: list[str] = []
        self._review_pos = 0

    def populate(self):
        res = self.app.res
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if not res:
            t.configure(state="disabled")
            return
        src = res.source
        lines = src.split("\n")
        width = len(str(len(lines)))
        # insert with line number gutter
        for i, line in enumerate(lines, 1):
            t.insert("end", f"{i:>{width}} ", "lineno")
            t.insert("end", line + "\n")
        # apply span tags; offsets must account for the gutter per line
        gut = width + 1

        def idx(offset: int) -> str:
            line = res.line_of(offset)
            col = offset - res.line_starts[line - 1]
            return f"{line}.{col + gut}"

        for s in res.spans:
            if s.kind is Kind.WHITESPACE:
                continue
            # tag may span lines; tag each line segment so the gutter stays
            start_line = res.line_of(s.start)
            end_line = res.line_of(max(s.end - 1, s.start))
            if start_line == end_line:
                t.tag_add("k_" + s.kind.value, idx(s.start), idx(s.end))
            else:
                t.tag_add("k_" + s.kind.value, idx(s.start),
                          f"{start_line}.end")
                for ln in range(start_line + 1, end_line):
                    t.tag_add("k_" + s.kind.value, f"{ln}.{gut}",
                              f"{ln}.end")
                t.tag_add("k_" + s.kind.value, f"{end_line}.{gut}",
                          idx(s.end))
        self._review_marks = sorted(
            {res.line_of(s.start) for s in res.spans
             if s.kind in (Kind.ORPHAN, Kind.COMMENT)})
        self._review_pos = 0
        t.configure(state="disabled")

    def next_review(self):
        if not self._review_marks:
            return
        line = self._review_marks[self._review_pos % len(self._review_marks)]
        self._review_pos += 1
        self.goto_line(line)

    def goto_line(self, line: int):
        self.text.see(f"{line}.0")
        self.text.configure(state="normal")
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", f"{line}.0", f"{line}.end")
        self.text.configure(state="disabled")

    def _jump_entity(self, ev):
        index = self.text.index(f"@{ev.x},{ev.y}")
        line = int(index.split(".")[0])
        res = self.app.res
        if not res:
            return
        offset = res.line_starts[line - 1]
        for s in res.spans:
            if s.start <= offset < s.end and s.kind is Kind.ENTITY \
                    and s.ref is not None:
                self.app.goto_entity(s.ref.eid)
                return


# ---------------------------------------------------------------------------
# Review tab (comments, orphans, warnings)
# ---------------------------------------------------------------------------

class ReviewTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master)
        self.app = app
        top = ttk.Label(self, padding=6, wraplength=1100, justify="left",
                        text=("Everything below was read from the file but is "
                              "NOT part of the consumed product data — or "
                              "needs human eyes for another reason. Review "
                              "each item for proprietary information. "
                              "Double-click an item to see it in place in "
                              "the file."))
        top.pack(fill="x")
        cols = ("line", "kind", "what")
        self.tv = ttk.Treeview(self, columns=cols, show="headings")
        self.tv.heading("line", text="line")
        self.tv.heading("kind", text="kind")
        self.tv.heading("what", text="item")
        self.tv.column("line", width=70, stretch=False, anchor="e")
        self.tv.column("kind", width=90, stretch=False)
        ys = ttk.Scrollbar(self, command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        for sev, color in SEV_COLORS.items():
            self.tv.tag_configure(sev, foreground=color)
        self.tv.bind("<Double-1>", self._jump)
        self.detail = tk.Text(self, height=7, wrap="word",
                              font=("TkFixedFont", 9), state="disabled")
        self.detail.pack(fill="x", side="bottom")
        self.tv.bind("<<TreeviewSelect>>", self._show_detail)

    def populate(self):
        self.tv.delete(*self.tv.get_children())
        res = self.app.res
        if not res:
            return
        order = {"orphan": 0, "comment": 1, "warning": 2, "info": 3}
        items = sorted(res.attention,
                       key=lambda a: (order.get(a.severity, 9), a.line))
        for a in items:
            txt = a.message + (f"   |   {a.excerpt}" if a.excerpt else "")
            self.tv.insert("", "end", values=(a.line, a.severity, txt),
                           tags=(a.severity,))

    def _selected(self):
        sel = self.tv.selection()
        if not sel:
            return None
        return self.tv.item(sel[0], "values")

    def _show_detail(self, _ev):
        v = self._selected()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if v:
            res = self.app.res
            line = int(v[0])
            lo = res.line_starts[line - 1]
            hi = res.line_starts[line + 3] if line + 3 <= len(
                res.line_starts) else len(res.source)
            self.detail.insert(
                "1.0", f"[{v[1]}] line {v[0]}: {v[2]}\n\nfile context:\n"
                + res.source[lo:hi])
        self.detail.configure(state="disabled")

    def _jump(self, _ev):
        v = self._selected()
        if v:
            self.app.goto_line(int(v[0]))


# ---------------------------------------------------------------------------
# Strings tab
# ---------------------------------------------------------------------------

class StringsTab(ttk.Frame):
    def __init__(self, master, app: InspectorApp):
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Label(bar, text=("Every string literal in the file — the place "
                             "proprietary text lives.  Search:")).pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.q, width=40)
        e.pack(side="left", padx=6)
        self.q.trace_add("write", lambda *_: self.populate())
        cols = ("line", "where", "text")
        self.tv = ttk.Treeview(self, columns=cols, show="headings")
        for cid, label, wpx, anchor in (("line", "line", 70, "e"),
                                        ("where", "location", 280, "w"),
                                        ("text", "decoded text", 700, "w")):
            self.tv.heading(cid, text=label)
            self.tv.column(cid, width=wpx, anchor=anchor,
                           stretch=(cid == "text"))
        ys = ttk.Scrollbar(self, command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("dirty", foreground="#c62828")
        self.tv.bind("<Double-1>", self._jump)

    def populate(self):
        self.tv.delete(*self.tv.get_children())
        res = self.app.res
        if not res:
            return
        q = self.q.get().strip().lower()
        shown = 0
        for s in res.strings:
            if q and q not in s.value.decoded.lower() \
                    and q not in s.location.lower():
                continue
            tags = () if s.value.clean else ("dirty",)
            text = s.value.decoded
            if not s.value.clean:
                text += f"   [raw: {s.value.raw}]"
            self.tv.insert("", "end", values=(s.line, s.location, text),
                           tags=tags)
            shown += 1
            if shown >= 20000:
                self.tv.insert("", "end",
                               values=("", "", "… list truncated …"))
                break

    def _jump(self, _ev):
        sel = self.tv.selection()
        if sel:
            v = self.tv.item(sel[0], "values")
            if v and v[0]:
                self.app.goto_line(int(v[0]))


def run(path: str | None = None, use_steptools: bool = True) -> None:
    app = InspectorApp(path, use_steptools)
    app.mainloop()
