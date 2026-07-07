"""STEP Inspector — PySide6 (Qt) desktop application."""

from __future__ import annotations

import html
import os
import threading
import traceback
from collections import Counter

from PySide6 import QtCore, QtGui, QtWidgets

from .. import occ_backend as occ
from .. import steptools_bridge as sb
from ..geometry import extract_wireframe
from ..parser import (Kind, Ref, Str, Typed, audit_file, format_value)
from ..pmi import CATEGORIES as PMI_CATEGORIES, extract_pmi
from ..report import build_report, coverage_summary
from . import theme
from .theme import PALETTES
from .qt_viewer import KIND_LABELS, Viewer3D
from . import qt_occ_viewer

APP_TITLE = "STEP Inspector"

SPAN_LEGEND = [("Entity data", "entity"), ("Header", "header"),
               ("Structure", "structure"), ("Comment — review", "comment"),
               ("Orphaned — review", "orphan")]


def mono_font(size=10) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(["JetBrains Mono", "Cascadia Code", "DejaVu Sans Mono",
                   "Noto Sans Mono", "Liberation Mono", "monospace"])
    f.setPointSize(size)
    f.setStyleHint(QtGui.QFont.TypeWriter)
    return f


def qcolor(c) -> QtGui.QColor:
    return QtGui.QColor(c)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class InspectorWindow(QtWidgets.QMainWindow):
    sig_loaded = QtCore.Signal(object)
    sig_occ = QtCore.Signal(int, object)

    def __init__(self, path=None, use_steptools=True, theme_name="light"):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 860)
        self.use_steptools = use_steptools
        self.pal = PALETTES.get(theme_name, PALETTES["light"])
        self.theme_name = theme_name

        self.res = None
        self.wire = None
        self.pmi = None
        self.stinfo = None
        self.occ = None
        self._occ_token = 0

        self._build_menu()
        self._build_ui()
        self.sig_loaded.connect(self._on_loaded)
        self.sig_occ.connect(self._on_occ)
        self.apply_theme(theme_name)
        if path:
            QtCore.QTimer.singleShot(80, lambda: self.open_path(path))

    # -- construction -------------------------------------------------------
    def _build_menu(self):
        mb = self.menuBar()
        filem = mb.addMenu("File")
        act_open = filem.addAction("Open…")
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_dialog)
        act_reload = filem.addAction("Reload")
        act_reload.setShortcut("F5")
        act_reload.triggered.connect(self.reload)
        filem.addSeparator()
        filem.addAction("Export audit report…").triggered.connect(
            self.export_report)
        filem.addSeparator()
        filem.addAction("Quit").triggered.connect(self.close)

        viewm = mb.addMenu("View")
        self.theme_group = QtGui.QActionGroup(self)
        for name in ("light", "dark"):
            a = viewm.addAction(name.capitalize())
            a.setCheckable(True)
            a.setChecked(name == self.theme_name)
            a.setActionGroup(self.theme_group)
            a.triggered.connect(lambda _=False, n=name: self.apply_theme(n))

        mb.addMenu("Help").addAction("About").triggered.connect(self._about)

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # banner
        self.banner = QtWidgets.QWidget()
        self.banner.setObjectName("Banner")
        bl = QtWidgets.QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 10, 14, 10)
        self.lbl_file = QtWidgets.QLabel("No file loaded — File ▸ Open…")
        self.lbl_file.setObjectName("BannerFile")
        bl.addWidget(self.lbl_file)
        bl.addStretch(1)
        self.lbl_cov = QtWidgets.QLabel("")
        bl.addWidget(self.lbl_cov)
        outer.addWidget(self.banner)

        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tab_overview = OverviewTab(self)
        self.tab_entities = EntitiesTab(self)
        self.tab_geometry = GeometryTab(self)
        self.tab_pmi = PMITab(self)
        self.tab_audit = AuditTab(self)
        self.tab_review = ReviewTab(self)
        self.tab_strings = StringsTab(self)
        for w, label in ((self.tab_overview, "Overview"),
                         (self.tab_entities, "Entities"),
                         (self.tab_geometry, "Geometry"),
                         (self.tab_pmi, "PMI / GD&&T"),
                         (self.tab_audit, "File audit"),
                         (self.tab_review, "Comments && orphans"),
                         (self.tab_strings, "Strings")):
            self.tabs.addTab(w, label)

        self.status = self.statusBar()
        self.status.setObjectName("StatusBar")
        self.status.showMessage("Ready.")

    @property
    def all_tabs(self):
        return (self.tab_overview, self.tab_entities, self.tab_geometry,
                self.tab_pmi, self.tab_audit, self.tab_review,
                self.tab_strings)

    # -- theming ------------------------------------------------------------
    def apply_theme(self, name):
        self.theme_name = name
        self.pal = PALETTES[name]
        app = QtWidgets.QApplication.instance()
        app.setStyleSheet(theme.qss(self.pal))
        self._refresh_badge()
        for t in self.all_tabs:
            t.restyle()
        if self.res is not None:
            for t in self.all_tabs:
                t.populate()

    def _refresh_badge(self):
        pal = self.pal
        if self.res is None:
            self.lbl_cov.setStyleSheet(
                f"background:{pal.raised}; color:{pal.text_dim};")
            return
        ok = self.res.verify_coverage()
        orphans = len(self.res.orphan_spans())
        if not ok:
            bg, fg = pal.badge_danger
            txt = "✗ COVERAGE FAILURE — parser gap"
        elif orphans:
            bg, fg = pal.badge_warn
            txt = (f"▲ every byte read — {orphans} orphaned span(s) and "
                   f"{len(self.res.comments)} comment(s) need review")
        elif self.res.comments:
            bg, fg = pal.badge_caution
            txt = (f"▲ every byte read — {len(self.res.comments)} comment(s) "
                   "need review")
        else:
            bg, fg = pal.badge_good
            txt = "✓ every byte read and consumed"
        self.lbl_cov.setText(txt)
        self.lbl_cov.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:6px; "
            f"padding:5px 12px; font-weight:600;")

    # -- loading ------------------------------------------------------------
    def open_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open STEP file", "",
            "STEP files (*.step *.stp *.p21 *.STEP *.STP);;All files (*)")
        if path:
            self.open_path(path)

    def reload(self):
        if self.res is not None:
            self.open_path(self.res.path)

    def open_path(self, path):
        self.status.showMessage(f"Reading {path} …")
        self.lbl_file.setText(os.path.basename(path) + "  (loading…)")
        self.occ = None
        self._occ_token += 1
        use_st = self.use_steptools

        def work():
            try:
                res = audit_file(path)
                wire = extract_wireframe(res)
                pmi = extract_pmi(res)
                st = sb.load(path) if use_st else None
                payload = dict(ok=True, res=res, wire=wire, pmi=pmi, st=st)
            except Exception:
                payload = dict(ok=False, err=traceback.format_exc(),
                               path=path)
            self.sig_loaded.emit(payload)

        threading.Thread(target=work, daemon=True).start()

    @QtCore.Slot(object)
    def _on_loaded(self, payload):
        if not payload.get("ok"):
            self.status.showMessage("Load failed")
            QtWidgets.QMessageBox.critical(
                self, APP_TITLE, "Could not read file:\n\n" + payload["err"])
            return
        self.res = payload["res"]
        self.wire = payload["wire"]
        self.pmi = payload["pmi"]
        self.stinfo = payload["st"]
        self.lbl_file.setText(
            f"{self.res.path}   —   {len(self.res.entities)} entities, "
            f"{self.res.total_lines} lines, {self.res.total_bytes} bytes")
        self._refresh_badge()
        for t in self.all_tabs:
            t.populate()
        nrev = len([a for a in self.res.attention
                    if a.severity in ("orphan", "comment")])
        self.status.showMessage(
            f"Loaded. {nrev} item(s) flagged for proprietary-information "
            "review — see 'Comments & orphans'.")
        self._start_occ()

    # -- OpenCASCADE backend ------------------------------------------------
    def _start_occ(self):
        if self.res is None or not occ.available():
            self.tab_geometry.occ_status()
            return
        path = self.res.path
        token = self._occ_token
        self.tab_geometry.occ_status(busy=True)

        def work():
            result = occ.load_and_mesh(path)
            self.sig_occ.emit(token, result)

        threading.Thread(target=work, daemon=True).start()

    @QtCore.Slot(int, object)
    def _on_occ(self, token, result):
        if token != self._occ_token:
            return
        self.occ = result
        self.tab_geometry.set_occ(result)
        self.tab_overview.populate()

    # -- actions ------------------------------------------------------------
    def export_report(self):
        if self.res is None:
            QtWidgets.QMessageBox.information(self, APP_TITLE,
                                              "Open a STEP file first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export audit report",
            os.path.basename(self.res.path) + ".audit.txt",
            "Text report (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_report(self.res, self.stinfo, occ_result=self.occ))
        self.status.showMessage(f"Report written to {path}")

    def goto_entity(self, eid):
        self.tabs.setCurrentWidget(self.tab_entities)
        self.tab_entities.select_entity(eid)

    def goto_line(self, line):
        self.tabs.setCurrentWidget(self.tab_audit)
        self.tab_audit.goto_line(line)

    def _about(self):
        QtWidgets.QMessageBox.about(
            self, "About " + APP_TITLE,
            "<b>STEP Inspector</b><br><br>"
            "Reviews STEP (ISO 10303-21 / AP203/214/242) files for "
            "proprietary information. Every byte of the file is classified; "
            "anything not consumed into the data model is shown as orphaned "
            "data.<br><br>"
            "Geometry math is vectorized with NumPy; shaded surfaces use "
            "OpenCASCADE (pythonocc-core); schema/second-reader cross-check "
            "uses the steptools library.")


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------

class OverviewTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QtWidgets.QTextBrowser()
        self.view.setObjectName("Flat")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._anchor)
        lay.addWidget(self.view)

    def restyle(self):
        pass

    def _anchor(self, url):
        s = url.toString()
        if s.startswith("eid:"):
            self.win.goto_entity(int(s[4:]))

    def populate(self):
        res = self.win.res
        if res is None:
            return
        self.view.setHtml(self._html())

    def _html(self):
        res, st, pal = self.win.res, self.win.stinfo, self.win.pal
        E = html.escape
        out = [f"<style>body{{font-family:{theme.MONO_FONT};"
               f"color:{pal.text};font-size:13px;}}"
               f"h3{{color:{pal.accent};margin:14px 0 4px;}}"
               f".w{{color:{pal.danger};}} .g{{color:{pal.good};}}"
               f".d{{color:{pal.text_dim};}}</style><body>"]

        def h(t):
            out.append(f"<h3>{E(t)}</h3>")

        def p(t, cls=None):
            c = f' class="{cls}"' if cls else ""
            out.append(f"<div{c}>{t}</div>")

        h("File")
        p(f"&nbsp;&nbsp;Path: {E(res.path)}")
        p(f"&nbsp;&nbsp;Size: {res.total_bytes} bytes / {res.total_lines} "
          "lines")
        p(f"&nbsp;&nbsp;Schema (declared): "
          f"{E(', '.join(res.schema_names) or '—')}")

        h("Byte accounting — proof the whole file was read")
        if res.verify_coverage():
            p("✓ Classified spans tile the file exactly: every byte was read "
              "and categorized.", "g")
        else:
            p("✗ Coverage verification FAILED — do not rely on this review.",
              "w")
        for label, nbytes, pct in coverage_summary(res):
            cls = "w" if ("review" in label and nbytes) else None
            p(f"&nbsp;&nbsp;&nbsp;&nbsp;{E(label):<40} "
              f"{nbytes:>9} bytes&nbsp;&nbsp;{pct:6.2f}%".replace(" ", "&nbsp;"),
              cls)

        h("Header records (often contain proprietary metadata)")
        fn = res.header_entity("FILE_NAME")
        labels = ["name", "time stamp", "author", "organization",
                  "preprocessor version", "originating system",
                  "authorization"]
        if fn and fn.params is not None:
            for i, lbl in enumerate(labels):
                v = format_value(fn.params[i], 400) if i < len(fn.params) \
                    else "—"
                p(f"&nbsp;&nbsp;{E(lbl)}: {E(str(v))}")

        h("Contents")
        counts = Counter(e.type_name for e in res.entities.values())
        p(f"&nbsp;&nbsp;{len(res.entities)} entity instances across "
          f"{len(counts)} types; top types:")
        for name, n in counts.most_common(12):
            p(f"&nbsp;&nbsp;&nbsp;&nbsp;{n:>6}&nbsp;&nbsp;{E(name)}".replace(
                " ", "&nbsp;"))

        h("Second reader — steptools library cross-check")
        if st is None:
            p("&nbsp;&nbsp;steptools check disabled.")
        elif not st.ok:
            p("&nbsp;&nbsp;" + E(st.error), "w")
        else:
            p(f"&nbsp;&nbsp;schema recognized: {E(st.schema_name)} "
              f"({E(st.schema_type)})")
            cc = sb.cross_check(res.entities.keys(), st)
            if cc and cc.clean:
                p(f"✓ both readers saw the same {cc.common} entity instances.",
                  "g")
            elif cc:
                p("✗ entity sets differ between readers.", "w")

        h("Shaded geometry — OpenCASCADE (visualization only)")
        r = self.win.occ
        if not occ.available():
            p("&nbsp;&nbsp;pythonocc-core not installed; shaded surface view "
              "disabled. Wireframe and the audit above are unaffected.", "d")
        elif r is None:
            p("&nbsp;&nbsp;tessellating in the background…", "d")
        elif not r.ok:
            p("&nbsp;&nbsp;" + E(r.error), "d")
        else:
            a = r.acc
            p(f"&nbsp;&nbsp;OpenCASCADE meshed {a.faces_meshed}/"
              f"{a.faces_total} faces ({a.solids} solid(s), {a.shells} "
              f"shell(s)) into {a.triangles:,} triangles.")
            if a.entities_parsed == len(res.entities):
                p(f"✓ Its STEP reader independently parsed {a.entities_parsed} "
                  "entities — same count as the audit parser.", "g")
            else:
                p(f"▲ Its STEP reader parsed {a.entities_parsed} entities vs "
                  f"{len(res.entities)} found by the audit parser — audit is "
                  "authoritative.", "w")
            pr = r.props
            if pr.ok:
                bb = pr.bbox_size
                p(f"&nbsp;&nbsp;bounding box: {bb[0]:.4g} × {bb[1]:.4g} × "
                  f"{bb[2]:.4g}")
                if pr.is_solid:
                    p(f"&nbsp;&nbsp;volume: {pr.volume:.6g}&nbsp;&nbsp;&nbsp;"
                      f"surface area: {pr.area:.6g}".replace(" ", "&nbsp;"))
                else:
                    p(f"&nbsp;&nbsp;surface area: {pr.area:.6g} (no closed "
                      "solid)")
                p(f"&nbsp;&nbsp;centre of mass: ({pr.com[0]:.4g}, "
                  f"{pr.com[1]:.4g}, {pr.com[2]:.4g})")
                if len(pr.solids) > 1:
                    p(f"&nbsp;&nbsp;per-solid breakdown "
                      f"({len(pr.solids)} bodies):")
                    for s in pr.solids:
                        b = s.bbox_size
                        p("&nbsp;&nbsp;&nbsp;&nbsp;"
                          f"solid {s.index}: volume {s.volume:.6g}, area "
                          f"{s.area:.6g}, CoM ({s.com[0]:.4g}, {s.com[1]:.4g},"
                          f" {s.com[2]:.4g}), bbox {b[0]:.4g}×{b[1]:.4g}×"
                          f"{b[2]:.4g}")
            p("&nbsp;&nbsp;Note: byte-level coverage is proven by the audit "
              "parser above; OpenCASCADE only renders surfaces.", "d")

        sev = Counter(a.severity for a in res.attention)
        h("Review queue")
        p(f"&nbsp;&nbsp;orphaned data: {sev.get('orphan', 0)}&nbsp;&nbsp;&nbsp;"
          f"comments: {sev.get('comment', 0)}&nbsp;&nbsp;&nbsp;warnings: "
          f"{sev.get('warning', 0)}&nbsp;&nbsp;&nbsp;info: "
          f"{sev.get('info', 0)}")
        out.append("</body>")
        return "".join(out)


# ---------------------------------------------------------------------------
# Entities tab
# ---------------------------------------------------------------------------

class EntitiesTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(split)

        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(QtWidgets.QLabel("Filter:"))
        self.filter = QtWidgets.QLineEdit()
        self.filter.textChanged.connect(self._rebuild_tree)
        fr.addWidget(self.filter)
        ll.addLayout(fr)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self._on_select)
        ll.addWidget(self.tree)
        split.addWidget(left)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.attrs = QtWidgets.QTreeWidget()
        self.attrs.setHeaderLabels(["attribute", "value"])
        self.attrs.setColumnWidth(0, 240)
        self.attrs.itemDoubleClicked.connect(self._follow_ref)
        right.addWidget(self._group("Attributes (double-click a #ref to "
                                    "follow it)", self.attrs))

        mid = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.usedby = QtWidgets.QListWidget()
        self.usedby.itemDoubleClicked.connect(self._follow_usedby)
        mid.addWidget(self._group("Referenced by", self.usedby))
        self.raw = QtWidgets.QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(mono_font(9))
        mid.addWidget(self._group("Raw source text", self.raw))
        mid.setSizes([200, 500])
        right.addWidget(mid)

        self.stview = QtWidgets.QPlainTextEdit()
        self.stview.setReadOnly(True)
        self.stview.setFont(mono_font(9))
        right.addWidget(self._group("steptools (EXPRESS/ARM) view",
                                    self.stview))
        right.setSizes([360, 200, 160])
        split.addWidget(right)
        split.setSizes([320, 1000])
        self._type_items = {}

    @staticmethod
    def _group(title, widget):
        box = QtWidgets.QGroupBox(title)
        gl = QtWidgets.QVBoxLayout(box)
        gl.setContentsMargins(6, 6, 6, 6)
        gl.addWidget(widget)
        return box

    def restyle(self):
        self.raw.setFont(mono_font(9))
        self.stview.setFont(mono_font(9))

    # -- tree ---------------------------------------------------------------
    def populate(self):
        self._rebuild_tree()
        self._show_entity(None)

    def _match(self, e, eid, flt):
        return (not flt or flt in e.type_name or flt == f"#{eid}"
                or flt in str(eid))

    def _rebuild_tree(self):
        res = self.win.res
        self.tree.clear()
        self._type_items.clear()
        if res is None:
            return
        flt = self.filter.text().strip().upper()
        groups = {}
        for eid in res.entity_order:
            e = res.entities[eid]
            if self._match(e, eid, flt):
                groups.setdefault(e.type_name, []).append(eid)
        for name in sorted(groups):
            it = QtWidgets.QTreeWidgetItem(
                self.tree, [f"{name}  ({len(groups[name])})"])
            it.setData(0, QtCore.Qt.UserRole, ("type", name))
            it.setForeground(0, qcolor(self.win.pal.text))
            it.addChild(QtWidgets.QTreeWidgetItem(["…"]))   # placeholder
            self._type_items[name] = it

    def _expand(self, item):
        data = item.data(0, QtCore.Qt.UserRole)
        if not data or data[0] != "type":
            return
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChildren()
            res = self.win.res
            flt = self.filter.text().strip().upper()
            n = 0
            for eid in res.entity_order:
                e = res.entities[eid]
                if e.type_name != data[1] or not self._match(e, eid, flt):
                    continue
                child = QtWidgets.QTreeWidgetItem(
                    item, [f"#{eid}  {self._summary(e)}"])
                child.setData(0, QtCore.Qt.UserRole, ("inst", eid))
                n += 1
                if n >= 5000:
                    QtWidgets.QTreeWidgetItem(item, ["… (truncated)"])
                    break

    @staticmethod
    def _summary(e):
        p = e.params
        if p and isinstance(p[0], Str) and p[0].decoded:
            return repr(p[0].decoded[:40])
        return ""

    def _on_select(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, QtCore.Qt.UserRole)
        if data and data[0] == "inst":
            self._show_entity(data[1])

    def select_entity(self, eid):
        res = self.win.res
        e = res.entities.get(eid)
        if not e:
            return
        ti = self._type_items.get(e.type_name)
        if ti:
            ti.setExpanded(True)
            for i in range(ti.childCount()):
                ch = ti.child(i)
                d = ch.data(0, QtCore.Qt.UserRole)
                if d and d[0] == "inst" and d[1] == eid:
                    self.tree.setCurrentItem(ch)
                    self.tree.scrollToItem(ch)
                    break
        self._show_entity(eid)

    # -- detail -------------------------------------------------------------
    def _show_entity(self, eid):
        self.attrs.clear()
        self.usedby.clear()
        self.raw.clear()
        self.stview.clear()
        res = self.win.res
        if eid is None or res is None:
            return
        e = res.entities.get(eid)
        if not e:
            return
        root = QtWidgets.QTreeWidgetItem(
            self.attrs, [f"#{eid} {e.type_name}", ""])
        root.setExpanded(True)
        if e.parse_error:
            QtWidgets.QTreeWidgetItem(root, ["▲ parse error", e.parse_error])
        for rec in e.records:
            parent = root
            if e.is_complex:
                parent = QtWidgets.QTreeWidgetItem(root, [rec.type_name, ""])
                parent.setExpanded(True)
            if rec.params is None:
                QtWidgets.QTreeWidgetItem(parent, ["(raw)", rec.raw_params])
            else:
                for i, v in enumerate(rec.params):
                    self._add_value(parent, f"[{i}]", v)
        for src in sorted(res.referenced_by.get(eid, ())):
            se = res.entities.get(src)
            self.usedby.addItem(f"#{src} {se.type_name if se else '?'}")
        self.raw.setPlainText(f"(line {res.line_of(e.span.start)})\n"
                              + e.span.text(res.source))
        st = self.win.stinfo
        if st is None:
            self.stview.setPlainText("steptools check disabled")
        elif not st.ok:
            self.stview.setPlainText(st.error)
        else:
            attrs = st.attributes_of(eid)
            self.stview.setPlainText(
                "\n".join(f"{k} = {v}" for k, v in attrs.items())
                if attrs else "entity not visible to steptools")

    def _add_value(self, parent, label, v, depth=0):
        res = self.win.res
        if isinstance(v, Ref):
            t = res.entities.get(v.eid)
            tname = t.type_name if t else "MISSING!"
            it = QtWidgets.QTreeWidgetItem(parent,
                                           [label, f"#{v.eid} → {tname}"])
            it.setData(0, QtCore.Qt.UserRole, ("ref", v.eid))
            it.setForeground(1, qcolor(self.win.pal.accent if t
                                       else self.win.pal.danger))
        elif isinstance(v, list):
            it = QtWidgets.QTreeWidgetItem(parent,
                                           [label, f"list of {len(v)}"])
            if depth < 1 and len(v) <= 16:
                it.setExpanded(True)
            for i, x in enumerate(v[:2000]):
                self._add_value(it, f"[{i}]", x, depth + 1)
        elif isinstance(v, Typed):
            it = QtWidgets.QTreeWidgetItem(parent, [label, v.name])
            it.setExpanded(True)
            self._add_value(it, "value", v.value, depth + 1)
        elif isinstance(v, Str):
            extra = "" if v.clean else "   ▲ raw: " + v.raw
            QtWidgets.QTreeWidgetItem(parent,
                                      [label, f"'{v.decoded}'{extra}"])
        else:
            QtWidgets.QTreeWidgetItem(parent, [label, format_value(v, 200)])

    def _follow_ref(self, item, _col):
        data = item.data(0, QtCore.Qt.UserRole)
        if data and data[0] == "ref":
            self.select_entity(data[1])

    def _follow_usedby(self, item):
        eid = int(item.text().split()[0][1:])
        self.select_entity(eid)


# ---------------------------------------------------------------------------
# Geometry tab
# ---------------------------------------------------------------------------

class GeometryTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QtWidgets.QWidget()
        bar.setObjectName("Toolbar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(8, 6, 8, 6)
        fit = QtWidgets.QPushButton("Fit view")
        fit.setObjectName("Accent")
        fit.clicked.connect(self._fit_active)
        bl.addWidget(fit)
        self.rb_wire = QtWidgets.QRadioButton("Wireframe")
        self.rb_wire.setChecked(True)
        self.rb_wire.toggled.connect(self._on_mode)
        self.rb_shaded = QtWidgets.QRadioButton("Shaded solids")
        self.rb_shaded.setEnabled(False)
        bl.addSpacing(10)
        bl.addWidget(self.rb_wire)
        bl.addWidget(self.rb_shaded)
        bl.addSpacing(10)
        self.layer_boxes = {}
        for layer, label in (("edges", "Edges/curves"), ("vertices",
                              "Vertices"), ("points", "Free points"),
                             ("axes", "Axes")):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(True)
            cb.toggled.connect(lambda on, l=layer: self.viewer.set_visible(
                l, on))
            bl.addWidget(cb)
            self.layer_boxes[layer] = cb
        bl.addSpacing(12)
        self.btn_measure = QtWidgets.QPushButton("Measure")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setEnabled(False)
        self.btn_measure.toggled.connect(self._on_measure)
        bl.addWidget(self.btn_measure)
        bl.addStretch(1)
        self.stats = QtWidgets.QLabel("")
        bl.addWidget(self.stats)
        lay.addWidget(bar)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.stack = QtWidgets.QStackedWidget()
        self.viewer = Viewer3D(win.pal)
        self.viewer.pick_changed.connect(self._update_measure_panel)
        self.stack.addWidget(self.viewer)
        # native OpenCASCADE OpenGL viewer for shaded mode, when importable
        self.native = None
        self.native_sel = []
        self.native_res = None
        if occ.available() and qt_occ_viewer.available():
            self.native = qt_occ_viewer.NativeViewer(win.pal)
            self.native.selection_made.connect(self._on_native_pick)
            self.stack.addWidget(self.native)
        split.addWidget(self.stack)
        self.measure_panel = self._build_measure_panel()
        split.addWidget(self.measure_panel)
        split.setSizes([1040, 280])
        self.measure_panel.setVisible(False)
        lay.addWidget(split, 1)

        foot = QtWidgets.QWidget()
        foot.setObjectName("Toolbar")
        self.foot_lay = QtWidgets.QHBoxLayout(foot)
        self.foot_lay.setContentsMargins(8, 5, 8, 5)
        self.occ_lbl = QtWidgets.QLabel("")
        lay.addWidget(foot)
        self._build_legend()

    # -- measurement panel --------------------------------------------------
    def _build_measure_panel(self):
        box = QtWidgets.QGroupBox("Measure")
        v = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Pick:"))
        self.pick_combo = QtWidgets.QComboBox()
        self.pick_combo.addItems(["Auto", "Points", "Edges", "Faces"])
        self.pick_combo.currentTextChanged.connect(self._on_pick_filter)
        row.addWidget(self.pick_combo, 1)
        v.addLayout(row)
        hint = QtWidgets.QLabel(
            "Click two items — point, edge or face — to measure the exact "
            "minimum distance between them (OpenCASCADE). Drag still rotates.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        v.addWidget(hint)
        self.measure_text = QtWidgets.QLabel("Measurement mode off.")
        self.measure_text.setWordWrap(True)
        self.measure_text.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse)
        self.measure_text.setAlignment(QtCore.Qt.AlignTop)
        v.addWidget(self.measure_text, 1)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self._clear_measure)
        v.addWidget(clear)
        return box

    # -- viewer-agnostic helpers ---------------------------------------------
    def _native_active(self):
        return self.native is not None \
            and self.stack.currentWidget() is self.native

    def _fit_active(self):
        if self._native_active():
            self.native.fit()
        else:
            self.viewer.fit()

    def _on_pick_filter(self, t):
        f = {"Auto": "auto", "Points": "vertex", "Edges": "edge",
             "Faces": "face"}[t]
        self.viewer.set_pick_filter(f)
        if self.native is not None:
            self.native.set_pick_filter(f)

    def _clear_measure(self):
        self.viewer.clear_measure()
        self.native_sel = []
        self.native_res = None
        if self.native is not None:
            self.native.clear_overlay()
        self._update_measure_panel()

    def _on_measure(self, on):
        self.viewer.set_measure_mode(on)
        if self.native is not None:
            self.native.set_measuring(on)
            if not on:
                self.native_sel = []
                self.native_res = None
                self.native.clear_overlay()
        self.measure_panel.setVisible(on)
        self.btn_measure.setText("Measure: ON" if on else "Measure")
        self._update_measure_panel()

    def _on_native_pick(self, topo_shape):
        ps = occ.describe_subshape(topo_shape)
        if ps is None:
            return
        if len(self.native_sel) >= 2:
            self.native_sel = []
            self.native_res = None
            self.native.clear_overlay()
        self.native_sel.append(ps)
        if len(self.native_sel) == 2:
            self.native_res = occ.measure(self.native_sel[0],
                                          self.native_sel[1])
            if self.native_res.ok:
                self.native.show_measurement(self.native_res)
        self._update_measure_panel()

    def _update_measure_panel(self):
        pal = self.win.pal
        if self._native_active():
            sel = self.native_sel
            r = self.native_res
        else:
            sel = self.viewer.selection
            r = self.viewer.measure_result
        rows = []
        for i, s in enumerate(sel, 1):
            rows.append(f"<b>{i}. {s.kind}</b> — {s.info}")
        if not sel:
            rows.append(f"<span style='color:{pal.text_dim}'>click an item…"
                        "</span>")
        elif len(sel) == 1:
            rows.append(f"<span style='color:{pal.text_dim}'>click a second "
                        "item…</span>")
        if r is not None:
            if r.ok:
                dx, dy, dz = r.delta
                rows.append("<hr>")
                rows.append(f"<b style='font-size:16px;color:{pal.accent}'>"
                            f"distance {r.distance:.6g}</b>")
                if r.angle is not None:
                    rows.append(f"<b>angle {r.angle:.4g}°</b> "
                                f"<span style='color:{pal.text_dim}'>(between "
                                "directions)</span>")
                rows.append(f"Δ = ({dx:.4g}, {dy:.4g}, {dz:.4g})")
                rows.append(f"<span style='color:{pal.text_dim}'>from "
                            f"({r.p1[0]:.4g}, {r.p1[1]:.4g}, {r.p1[2]:.4g})"
                            f"<br>to ({r.p2[0]:.4g}, {r.p2[1]:.4g}, "
                            f"{r.p2[2]:.4g})</span>")
            else:
                rows.append(f"<span style='color:{pal.danger}'>measure failed: "
                            f"{r.error}</span>")
        self.measure_text.setText("<br>".join(rows))

    def _build_legend(self):
        while self.foot_lay.count():
            item = self.foot_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        pal = self.win.pal
        self.foot_lay.addWidget(QtWidgets.QLabel(
            "Drag rotate · Right-drag pan · Wheel zoom    "))
        if self.rb_shaded.isChecked():
            self.foot_lay.addWidget(self._swatch("#%02x%02x%02x" % pal.surface))
            label = ("OpenCASCADE native OpenGL viewer (exact B-rep)"
                     if self._native_active()
                     else "OpenCASCADE shaded B-rep surfaces")
            self.foot_lay.addWidget(QtWidgets.QLabel(label))
        else:
            for kind, color in pal.kind_colors().items():
                if kind == "ellipse":
                    continue
                self.foot_lay.addWidget(self._swatch(color))
                self.foot_lay.addWidget(QtWidgets.QLabel(KIND_LABELS[kind]))
        self.foot_lay.addStretch(1)
        self.occ_lbl = QtWidgets.QLabel(self.occ_lbl.text())
        self.foot_lay.addWidget(self.occ_lbl)

    @staticmethod
    def _swatch(color):
        s = QtWidgets.QLabel()
        s.setFixedSize(12, 12)
        s.setStyleSheet(f"background:{color}; border-radius:2px;")
        return s

    def restyle(self):
        self.viewer.apply_palette(self.win.pal)
        if self.native is not None:
            self.native.apply_palette(self.win.pal)
        self._build_legend()
        self.occ_status()

    def set_occ(self, result):
        if result is not None and result.ok and not result.mesh.empty:
            self.viewer.set_mesh(result.mesh)
            self.rb_shaded.setEnabled(True)
        else:
            self.viewer.set_mesh(None)
            self.rb_shaded.setEnabled(False)
            if self.rb_shaded.isChecked():
                self.rb_wire.setChecked(True)
        can_measure = (result is not None and result.ok
                       and not result.pick.empty)
        self.viewer.set_pick_model(result.pick if can_measure else None)
        self.btn_measure.setEnabled(can_measure)
        if not can_measure and self.btn_measure.isChecked():
            self.btn_measure.setChecked(False)
        self.btn_measure.setToolTip(
            "" if can_measure else
            "Measurement needs OpenCASCADE surface data (pythonocc-core).")
        # refresh the native viewer if shaded mode is already active
        if self.rb_shaded.isChecked():
            self._on_mode()
        self.occ_status()

    def occ_status(self, busy=False):
        if not occ.available():
            self.occ_lbl.setText("Shaded: install pythonocc-core (conda) for "
                                 "OpenCASCADE surfaces")
            return
        if busy:
            self.occ_lbl.setText("Shaded: tessellating with OpenCASCADE …")
            return
        r = self.win.occ
        if r is None:
            self.occ_lbl.setText("")
        elif not r.ok:
            self.occ_lbl.setText(f"Shaded: {r.error[:90]}")
        else:
            a = r.acc
            self.occ_lbl.setText(
                f"OpenCASCADE: {a.faces_meshed}/{a.faces_total} faces · "
                f"{a.triangles:,} triangles · parsed {a.entities_parsed} "
                "entities")

    def _on_mode(self):
        shaded = self.rb_shaded.isChecked()
        use_native = False
        if shaded and self.native is not None and self.win.occ is not None \
                and self.win.occ.shape is not None:
            use_native = self.native.show_shape(self.win.occ.shape)
        if use_native:
            self.stack.setCurrentWidget(self.native)
            self.native.set_measuring(self.btn_measure.isChecked())
        else:
            self.stack.setCurrentWidget(self.viewer)
            self.viewer.set_mode("shaded" if shaded else "wire")
        self._build_legend()
        self.populate()
        self._update_measure_panel()

    def populate(self):
        self.viewer.set_model(self.win.wire)
        if self.rb_shaded.isChecked():
            r = self.win.occ
            if r is not None and r.ok:
                a = r.acc
                self.stats.setText(f"{a.triangles:,} triangles · "
                                   f"{a.faces_meshed} faces · {a.solids} "
                                   "solid(s)")
            else:
                self.stats.setText("")
            return
        wire = self.win.wire
        if wire is None:
            self.stats.setText("")
            return
        kinds = Counter(pl.kind for pl in wire.polylines)
        parts = [f"{n} {KIND_LABELS.get(k, k)}" for k, n in kinds.most_common()]
        if wire.free_points:
            parts.append(f"{len(wire.free_points)} free points")
        self.stats.setText(", ".join(parts) if parts
                           else "no wireframe geometry found")


# ---------------------------------------------------------------------------
# PMI / GD&T tab
# ---------------------------------------------------------------------------

class PMITab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.hint = QtWidgets.QLabel(
            "Product Manufacturing Information: geometric tolerances, "
            "dimensions, datums, annotation text, saved views, and properties "
            "(AP242/AP214). Items the extractor cannot interpret are listed "
            "under 'Other PMI-related items' — nothing is hidden. Double-click "
            "to open in the Entities tab.")
        self.hint.setObjectName("SectionHint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.tree)
        self.footer = QtWidgets.QLabel("")
        self.footer.setObjectName("Hint")
        lay.addWidget(self.footer)

    def restyle(self):
        pass

    def populate(self):
        self.tree.clear()
        m = self.win.pmi
        if m is None:
            return
        if m.empty:
            QtWidgets.QTreeWidgetItem(self.tree,
                                      ["No PMI / GD&T content in this file."])
            self.footer.setText("")
            return
        pal = self.win.pal
        for key, label in PMI_CATEGORIES:
            items = m.categories.get(key, [])
            if not items:
                continue
            cat = QtWidgets.QTreeWidgetItem(self.tree,
                                            [f"{label}  ({len(items)})"])
            cat.setExpanded(True)
            f = cat.font(0)
            f.setBold(True)
            cat.setFont(0, f)
            cat.setForeground(0, qcolor(pal.accent))
            for it in items:
                text = f"#{it.eid}   {it.label}"
                if it.detail:
                    text += f"   —   {it.detail}"
                node = QtWidgets.QTreeWidgetItem(cat, [text])
                node.setData(0, QtCore.Qt.UserRole, it.eid)
                if not it.interpreted:
                    node.setForeground(0, qcolor(pal.warn))
        self.footer.setText(
            f"{m.total} PMI item(s).   Presentation/style machinery not shown "
            f"here: {m.style_count} instance(s).   All instances remain "
            "visible in the Entities tab.")

    def _open(self, item, _col):
        eid = item.data(0, QtCore.Qt.UserRole)
        if eid is not None:
            self.win.goto_entity(int(eid))


# ---------------------------------------------------------------------------
# File audit tab
# ---------------------------------------------------------------------------

class _LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.lineno_width(), 0)

    def paintEvent(self, ev):
        self.editor.paint_linenos(ev)


class CodeView(QtWidgets.QPlainTextEdit):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setReadOnly(True)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.setFont(mono_font(10))
        self.lna = _LineNumberArea(self)
        self.blockCountChanged.connect(lambda _: self._upd_margin())
        self.updateRequest.connect(self._on_update)
        self._upd_margin()

    def lineno_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _upd_margin(self):
        self.setViewportMargins(self.lineno_width(), 0, 0, 0)

    def _on_update(self, rect, dy):
        if dy:
            self.lna.scroll(0, dy)
        else:
            self.lna.update(0, rect.y(), self.lna.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._upd_margin()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self.lna.setGeometry(QtCore.QRect(cr.left(), cr.top(),
                                          self.lineno_width(), cr.height()))

    def paint_linenos(self, ev):
        qp = QtGui.QPainter(self.lna)
        pal = self.win.pal
        qp.fillRect(ev.rect(), qcolor(pal.panel_alt))
        qp.setPen(qcolor(pal.text_dim))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        geo = self.blockBoundingGeometry(block).translated(
            self.contentOffset())
        top = geo.top()
        bottom = top + self.blockBoundingRect(block).height()
        fh = self.fontMetrics().height()
        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible() and bottom >= ev.rect().top():
                qp.drawText(0, int(top), self.lna.width() - 5, fh,
                            QtCore.Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            num += 1

    def mouseDoubleClickEvent(self, ev):
        cur = self.cursorForPosition(ev.position().toPoint())
        self.win.tab_audit.on_double_click(cur.position())
        super().mouseDoubleClickEvent(ev)


class AuditTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QtWidgets.QWidget()
        bar.setObjectName("Toolbar")
        self.bar_lay = QtWidgets.QHBoxLayout(bar)
        self.bar_lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(bar)
        self.view = CodeView(win)
        lay.addWidget(self.view, 1)
        self._review_marks = []
        self._review_pos = 0
        self._build_bar()

    def _build_bar(self):
        while self.bar_lay.count():
            item = self.bar_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        nxt = QtWidgets.QPushButton("Next review item ▼")
        nxt.setObjectName("Accent")
        nxt.clicked.connect(self.next_review)
        self.bar_lay.addWidget(nxt)
        pal = self.win.pal
        bgs = pal.span_backgrounds()
        for label, key in SPAN_LEGEND:
            sw = QtWidgets.QLabel()
            sw.setFixedSize(13, 13)
            sw.setStyleSheet(f"background:{bgs[key]}; "
                             f"border:1px solid {pal.border};")
            self.bar_lay.addSpacing(8)
            self.bar_lay.addWidget(sw)
            self.bar_lay.addWidget(QtWidgets.QLabel(label))
        self.bar_lay.addStretch(1)

    def restyle(self):
        self.view.setFont(mono_font(10))
        self._build_bar()
        if self.win.res is not None:
            self._apply_spans()

    def populate(self):
        res = self.win.res
        if res is None:
            return
        self.view.setPlainText(res.source)
        self._apply_spans()
        self._review_marks = sorted(
            {res.line_of(s.start) for s in res.spans
             if s.kind in (Kind.ORPHAN, Kind.COMMENT)})
        self._review_pos = 0

    def _apply_spans(self):
        res = self.win.res
        pal = self.win.pal
        bgs = pal.span_backgrounds()
        doc = self.view.document()
        sels = []
        n = 0
        for s in res.spans:
            if s.kind is Kind.WHITESPACE:
                continue
            bg = bgs.get(s.kind.value)
            if bg is None:
                continue
            sel = QtWidgets.QTextEdit.ExtraSelection()
            fmt = QtGui.QTextCharFormat()
            fmt.setBackground(qcolor(bg))
            if s.kind is Kind.ORPHAN:
                fmt.setForeground(qcolor(pal.danger))
            elif s.kind is Kind.COMMENT:
                fmt.setForeground(qcolor(pal.comment))
            sel.format = fmt
            cur = QtGui.QTextCursor(doc)
            cur.setPosition(s.start)
            cur.setPosition(min(s.end, doc.characterCount() - 1),
                            QtGui.QTextCursor.KeepAnchor)
            sel.cursor = cur
            sels.append(sel)
            n += 1
            if n >= 20000:
                break
        self.view.setExtraSelections(sels)

    def next_review(self):
        if not self._review_marks:
            return
        line = self._review_marks[self._review_pos % len(self._review_marks)]
        self._review_pos += 1
        self.goto_line(line)

    def goto_line(self, line):
        doc = self.view.document()
        block = doc.findBlockByNumber(max(0, line - 1))
        cur = QtGui.QTextCursor(block)
        self.view.setTextCursor(cur)
        self.view.centerCursor()

    def on_double_click(self, pos):
        res = self.win.res
        if res is None:
            return
        for s in res.spans:
            if s.start <= pos < s.end and s.kind is Kind.ENTITY \
                    and s.ref is not None:
                self.win.goto_entity(s.ref.eid)
                return


# ---------------------------------------------------------------------------
# Comments & orphans tab
# ---------------------------------------------------------------------------

class ReviewTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.hint = QtWidgets.QLabel(
            "Everything below was read from the file but is NOT part of the "
            "consumed product data — or needs human eyes for another reason. "
            "Review each item for proprietary information. Double-click to see "
            "it in place in the file.")
        self.hint.setObjectName("SectionHint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["line", "kind", "item"])
        self.tree.setColumnWidth(0, 60)
        self.tree.setColumnWidth(1, 90)
        self.tree.itemDoubleClicked.connect(self._jump)
        self.tree.itemSelectionChanged.connect(self._detail)
        lay.addWidget(self.tree, 1)
        self.detailv = QtWidgets.QPlainTextEdit()
        self.detailv.setReadOnly(True)
        self.detailv.setFont(mono_font(9))
        self.detailv.setMaximumHeight(150)
        lay.addWidget(self.detailv)

    def restyle(self):
        self.detailv.setFont(mono_font(9))

    def populate(self):
        self.tree.clear()
        res = self.win.res
        if res is None:
            return
        pal = self.win.pal
        sev_colors = pal.severity_colors()
        order = {"orphan": 0, "comment": 1, "warning": 2, "info": 3}
        items = sorted(res.attention,
                       key=lambda a: (order.get(a.severity, 9), a.line))
        for a in items:
            txt = a.message + (f"   |   {a.excerpt}" if a.excerpt else "")
            node = QtWidgets.QTreeWidgetItem(
                self.tree, [str(a.line), a.severity, txt])
            col = sev_colors.get(a.severity)
            if col:
                for c in range(3):
                    node.setForeground(c, qcolor(col))

    def _detail(self):
        items = self.tree.selectedItems()
        if not items:
            return
        v = items[0]
        res = self.win.res
        line = int(v.text(0))
        lo = res.line_starts[line - 1]
        hi = res.line_starts[line + 3] if line + 3 <= len(res.line_starts) \
            else len(res.source)
        self.detailv.setPlainText(
            f"[{v.text(1)}] line {v.text(0)}: {v.text(2)}\n\nfile context:\n"
            + res.source[lo:hi])

    def _jump(self, item, _col):
        self.win.goto_line(int(item.text(0)))


# ---------------------------------------------------------------------------
# Strings tab
# ---------------------------------------------------------------------------

class StringsTab(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel(
            "Every string literal in the file — where proprietary text lives. "
            "Search:"))
        self.q = QtWidgets.QLineEdit()
        self.q.textChanged.connect(self.populate)
        top.addWidget(self.q)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["line", "location", "decoded text"])
        self.tree.setColumnWidth(0, 60)
        self.tree.setColumnWidth(1, 280)
        self.tree.itemDoubleClicked.connect(self._jump)
        lay.addWidget(self.tree)

    def restyle(self):
        pass

    def populate(self):
        self.tree.clear()
        res = self.win.res
        if res is None:
            return
        pal = self.win.pal
        q = self.q.text().strip().lower()
        shown = 0
        for s in res.strings:
            if q and q not in s.value.decoded.lower() \
                    and q not in s.location.lower():
                continue
            text = s.value.decoded
            if not s.value.clean:
                text += f"   [raw: {s.value.raw}]"
            node = QtWidgets.QTreeWidgetItem(
                self.tree, [str(s.line), s.location, text])
            if not s.value.clean:
                for c in range(3):
                    node.setForeground(c, qcolor(pal.danger))
            shown += 1
            if shown >= 20000:
                QtWidgets.QTreeWidgetItem(self.tree, ["", "", "… truncated …"])
                break

    def _jump(self, item, _col):
        if item.text(0):
            self.win.goto_line(int(item.text(0)))


def run(path=None, use_steptools=True, theme_name="light"):
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = InspectorWindow(path, use_steptools, theme_name)
    win.show()
    app.exec()
