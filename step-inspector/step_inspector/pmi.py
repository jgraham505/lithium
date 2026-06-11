"""PMI / GD&T extraction (AP242 and AP214-style files).

Walks the parsed entity graph and presents Product Manufacturing
Information in readable form: geometric tolerances with magnitudes and
datum references, dimensions with plus/minus tolerances, datum systems,
annotation text, saved views, and notes/properties.

Same philosophy as the rest of the tool: nothing is silently dropped.
Every entity whose type matches a PMI-related pattern is either rendered
as a structured item or listed under "Other PMI-related" so the reviewer
sees it exists.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .parser import AuditResult, Entity, EnumVal, Ref, Str, Typed

# Display order of categories.
CATEGORIES = [
    ("gdt", "Geometric tolerances (GD&T)"),
    ("dimension", "Dimensions & dimensional tolerances"),
    ("datum", "Datums"),
    ("annotation", "Annotations & 3D text"),
    ("view", "Saved views"),
    ("note", "Notes & properties"),
    ("other", "Other PMI-related items"),
]

# Type-name patterns that mark an entity as PMI-related.  First match wins.
_PATTERNS = [
    ("dimension", re.compile(
        r"DIMENSIONAL_(SIZE|LOCATION)|ANGULAR_(SIZE|LOCATION)"
        r"|SHAPE_DIMENSION_REPRESENTATION"
        r"|DIMENSIONAL_CHARACTERISTIC_REPRESENTATION"
        r"|^PLUS_MINUS_TOLERANCE$|^TOLERANCE_VALUE$|LIMITS_AND_FITS")),
    ("gdt", re.compile(
        r"_TOLERANCE$|^GEOMETRIC_TOLERANCE|TOLERANCE_ZONE"
        r"|UNEQUALLY_DISPOSED")),
    ("datum", re.compile(r"^DATUM$|^DATUM_|^PLACED_DATUM_TARGET")),
    ("annotation", re.compile(
        r"TEXT_LITERAL|COMPOSITE_TEXT|^ANNOTATION_|DRAUGHTING_CALLOUT"
        r"|^CALLOUT_|TESSELLATED_ANNOTATION")),
    ("view", re.compile(
        r"CAMERA_MODEL|DRAUGHTING_MODEL|SAVED_VIEW"
        r"|MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION")),
    ("note", re.compile(
        r"DESCRIPTIVE_REPRESENTATION_ITEM|VALUE_REPRESENTATION_ITEM"
        r"|^PROPERTY_DEFINITION$|SURFACE_TEXTURE")),
    ("style", re.compile(
        r"_STYLE|^COLOUR|^PRESENTATION_|CURVE_FONT|FILL_AREA_STYLE"
        r"|TEXT_FONT|^DRAUGHTING_PRE_DEFINED")),
]

_SI_PREFIX = {"EXA": "E", "PETA": "P", "TERA": "T", "GIGA": "G", "MEGA": "M",
              "KILO": "k", "HECTO": "h", "DECA": "da", "DECI": "d",
              "CENTI": "c", "MILLI": "m", "MICRO": "µ", "NANO": "n",
              "PICO": "p", "FEMTO": "f", "ATTO": "a"}
_SI_NAME = {"METRE": "m", "GRAM": "g", "SECOND": "s", "AMPERE": "A",
            "KELVIN": "K", "MOLE": "mol", "CANDELA": "cd", "RADIAN": "rad",
            "STERADIAN": "sr", "HERTZ": "Hz", "NEWTON": "N", "PASCAL": "Pa",
            "DEGREE_CELSIUS": "°C"}


@dataclass
class PMIItem:
    eid: int
    label: str                  # one-line readable summary
    detail: str = ""            # extra lines for the detail pane / report
    interpreted: bool = True    # False = listed raw, semantics not decoded


@dataclass
class PMIModel:
    categories: dict = field(default_factory=dict)   # cat key -> [PMIItem]
    style_count: int = 0          # presentation/style machinery instances
    style_types: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.categories.values())

    @property
    def empty(self) -> bool:
        return self.total == 0 and self.style_count == 0


# ---------------------------------------------------------------------------
# small graph helpers
# ---------------------------------------------------------------------------

class _G:
    def __init__(self, res: AuditResult):
        self.res = res
        self.ents = res.entities

    def deref(self, v) -> Optional[Entity]:
        return self.ents.get(v.eid) if isinstance(v, Ref) else None

    @staticmethod
    def leafs(ent: Entity) -> set:
        return {r.type_name for r in ent.records}

    @staticmethod
    def all_params(ent: Entity) -> list:
        out = []
        for r in ent.records:
            out.extend(r.params or [])
        return out

    @staticmethod
    def first_str(params, skip: int = 0) -> str:
        n = 0
        for v in params or []:
            if isinstance(v, Str):
                if n >= skip:
                    return v.decoded
                n += 1
        return ""

    def name_of(self, ent: Optional[Entity]) -> str:
        """Best human label for an entity: its name attr, else type."""
        if ent is None:
            return "?"
        for r in ent.records:
            if r.params and isinstance(r.params[0], Str) \
                    and r.params[0].decoded:
                return r.params[0].decoded
        return ent.type_name.lower()

    def refs_in(self, values) -> list:
        out = []
        def walk(vs):
            for v in vs:
                if isinstance(v, Ref):
                    out.append(v)
                elif isinstance(v, list):
                    walk(v)
                elif isinstance(v, Typed):
                    walk([v.value])
        walk(values or [])
        return out


def _measure(g: _G, v) -> tuple[Optional[float], str]:
    """Resolve a measure_with_unit ref to (value, unit label)."""
    ent = g.deref(v)
    if ent is None:
        return None, ""
    val = None
    unit = ""
    for p in g.all_params(ent):
        if isinstance(p, Typed) and isinstance(p.value, (int, float)) \
                and val is None:
            val = float(p.value)
        elif isinstance(p, (int, float)) and val is None:
            val = float(p)
        elif isinstance(p, Ref) and not unit:
            unit = _unit_label(g, p)
    return val, unit


def _unit_label(g: _G, v) -> str:
    ent = g.deref(v)
    if ent is None:
        return ""
    si = ent.record("SI_UNIT")
    if si is not None and si.params is not None:
        enums = [x.name for x in si.params if isinstance(x, EnumVal)]
        prefix = name = ""
        for e in enums:
            if e in _SI_PREFIX:
                prefix = _SI_PREFIX[e]
            elif e in _SI_NAME:
                name = _SI_NAME[e]
        return prefix + name if name else " ".join(enums).lower()
    cbu = ent.record("CONVERSION_BASED_UNIT")
    if cbu is not None and cbu.params is not None:
        return g.first_str(cbu.params)
    return ""


def _fmt_val(val: Optional[float], unit: str) -> str:
    if val is None:
        return "?"
    return f"{val:g}{(' ' + unit) if unit else ''}"


def _datum_letters(g: _G, seeds: list, visited: set,
                   max_depth: int = 4) -> list:
    """Follow references from datum_system values down to DATUM letters."""
    letters = []
    seen: set[int] = set()
    frontier = [(r, 0) for r in g.refs_in(seeds)]
    while frontier:
        ref, depth = frontier.pop(0)
        if ref.eid in seen or depth > max_depth:
            continue
        seen.add(ref.eid)
        ent = g.deref(ref)
        if ent is None:
            continue
        visited.add(ent.eid)
        rec = ent.record("DATUM")
        if rec is not None and rec.params:
            ident = ""
            for p in reversed(rec.params):
                if isinstance(p, Str) and p.decoded:
                    ident = p.decoded
                    break
            letters.append(ident or f"#{ent.eid}")
            continue
        frontier.extend((r, depth + 1)
                        for r in g.refs_in(g.all_params(ent)))
    # de-duplicate, keep order
    out = []
    for l in letters:
        if l not in out:
            out.append(l)
    return out


def _tolerance_label(leafs: set) -> str:
    """Most specific *_TOLERANCE leaf, prettified."""
    best = ""
    for name in leafs:
        if name.endswith("_TOLERANCE") and not name.startswith(
                ("GEOMETRIC_TOLERANCE", "PLUS_MINUS")):
            best = name
            break
    if not best:
        for name in leafs:
            if "TOLERANCE" in name:
                best = name
                break
    return best.replace("_TOLERANCE", "").replace("_", " ").title() \
        or "Geometric Tolerance"


# ---------------------------------------------------------------------------
# main extraction
# ---------------------------------------------------------------------------

def category_of(ent: Entity) -> Optional[str]:
    for cat, pat in _PATTERNS:
        for leaf in (r.type_name for r in ent.records):
            if pat.search(leaf):
                return cat
    return None


def extract_pmi(res: AuditResult) -> PMIModel:
    g = _G(res)
    model = PMIModel(categories={k: [] for k, _ in CATEGORIES})
    visited: set[int] = set()       # support entities consumed by items

    pmi_ents: dict[int, str] = {}
    for eid, ent in res.entities.items():
        cat = category_of(ent)
        if cat == "style":
            model.style_count += 1
            model.style_types[ent.type_name] += 1
            visited.add(eid)
        elif cat is not None:
            pmi_ents[eid] = cat

    # ---- dimensions -------------------------------------------------------
    # value representations linked via DIMENSIONAL_CHARACTERISTIC_REPRESENTATION
    dim_values: dict[int, str] = {}
    for eid, ent in res.entities.items():
        rec = ent.record("DIMENSIONAL_CHARACTERISTIC_REPRESENTATION")
        if rec is None or not rec.params:
            continue
        visited.add(eid)
        refs = g.refs_in(rec.params)
        if not refs:
            continue
        dim_ref = refs[0]
        vals = []
        for rep_ref in refs[1:]:
            rep = g.deref(rep_ref)
            if rep is None:
                continue
            visited.add(rep.eid)
            # only the representation's items list, not its context
            item_lists = [v for v in g.all_params(rep) if isinstance(v, list)]
            for item_ref in g.refs_in(item_lists):
                item = g.deref(item_ref)
                if item is None:
                    continue
                v, u = _measure(g, item_ref)
                if v is not None:
                    visited.add(item.eid)
                    iname = g.first_str(g.all_params(item)) or "value"
                    vals.append(f"{iname} {_fmt_val(v, u)}")
        if vals:
            dim_values[dim_ref.eid] = ", ".join(vals)

    # plus/minus tolerances attached to dimensions
    dim_pm: dict[int, str] = {}
    for eid, ent in res.entities.items():
        rec = ent.record("PLUS_MINUS_TOLERANCE")
        if rec is None or not rec.params:
            continue
        visited.add(eid)
        refs = g.refs_in(rec.params)
        rng = ""
        target = None
        for r in refs:
            tent = g.deref(r)
            if tent is None:
                continue
            tv = tent.record("TOLERANCE_VALUE")
            if tv is not None and tv.params:
                visited.add(tent.eid)
                bounds = []
                for br in g.refs_in(tv.params):
                    bent = g.deref(br)
                    if bent is not None:
                        visited.add(bent.eid)
                    v, u = _measure(g, br)
                    if v is not None:
                        bounds.append(f"{v:+g}{(' ' + u) if u else ''}")
                rng = " / ".join(bounds)
            else:
                target = r.eid
        if target is not None and rng:
            dim_pm[target] = rng

    for eid, ent in sorted(res.entities.items()):
        for tname in ("DIMENSIONAL_SIZE", "ANGULAR_SIZE",
                      "DIMENSIONAL_LOCATION", "ANGULAR_LOCATION",
                      "DIMENSIONAL_SIZE_WITH_PATH",
                      "DIMENSIONAL_LOCATION_WITH_PATH"):
            rec = ent.record(tname)
            if rec is None or rec.params is None:
                continue
            visited.add(eid)
            refs = g.refs_in(rec.params)
            targets = []
            for r in refs:
                tgt = g.deref(r)
                if tgt is not None:
                    visited.add(tgt.eid)
                    targets.append(g.name_of(tgt))
            kind = g.first_str(rec.params) or tname.replace("_", " ").lower()
            label = f"{kind} on {', '.join(repr(t) for t in targets) or '?'}"
            parts = []
            if eid in dim_values:
                parts.append(dim_values[eid])
            if eid in dim_pm:
                parts.append(f"tolerance {dim_pm[eid]}")
            if parts:
                label += " = " + "; ".join(parts)
            model.categories["dimension"].append(PMIItem(eid, label))
            break

    # ---- geometric tolerances ---------------------------------------------
    for eid, ent in sorted(res.entities.items()):
        leafs = g.leafs(ent)
        is_gt = any(l.startswith("GEOMETRIC_TOLERANCE") for l in leafs) or \
            any(l.endswith("_TOLERANCE") and l != "PLUS_MINUS_TOLERANCE"
                and l != "TOLERANCE_VALUE" for l in leafs)
        if not is_gt or pmi_ents.get(eid) != "gdt":
            continue
        visited.add(eid)
        base = ent.record("GEOMETRIC_TOLERANCE")
        if base is None or base.params is None:
            # simple instance: the single record carries inherited attrs
            base = ent.records[0]
        params = base.params or []
        name = g.first_str(params)
        desc = g.first_str(params, skip=1)
        mag_txt = ""
        target_txt = ""
        for r in g.refs_in(params):
            tgt = g.deref(r)
            if tgt is None:
                continue
            if any("MEASURE_WITH_UNIT" in l for l in g.leafs(tgt)) \
                    and not mag_txt:
                visited.add(tgt.eid)
                v, u = _measure(g, r)
                mag_txt = _fmt_val(v, u)
            elif not target_txt:
                visited.add(tgt.eid)
                target_txt = g.name_of(tgt)
        # datum references from the WITH_DATUM_REFERENCE leaf (complex) or
        # from trailing params (simple instance)
        datum_seeds = []
        wdr = ent.record("GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE")
        if wdr is not None and wdr.params is not None:
            datum_seeds = wdr.params
        elif len(ent.records) == 1 and base.params:
            datum_seeds = base.params[4:]
        letters = _datum_letters(g, datum_seeds, visited)
        modifiers = []
        for rname in ("GEOMETRIC_TOLERANCE_WITH_MODIFIERS",
                      "GEOMETRIC_TOLERANCE_WITH_MAXIMUM_TOLERANCE"):
            mrec = ent.record(rname)
            if mrec is not None and mrec.params:
                modifiers += [x.name for x in mrec.params
                              if isinstance(x, EnumVal)]
                modifiers += [x.name for sub in mrec.params
                              if isinstance(sub, list) for x in sub
                              if isinstance(x, EnumVal)]
        label = f"{_tolerance_label(leafs)} {mag_txt or '?'}"
        if target_txt:
            label += f" on '{target_txt}'"
        if letters:
            label += f"  | datums {'-'.join(letters)}"
        if modifiers:
            label += f"  | modifiers: {', '.join(m.lower() for m in modifiers)}"
        detail = " / ".join(x for x in (name, desc) if x)
        model.categories["gdt"].append(PMIItem(eid, label, detail))

    # ---- datums --------------------------------------------------------------
    for eid, ent in sorted(res.entities.items()):
        rec = ent.record("DATUM")
        if rec is not None and rec.params is not None:
            visited.add(eid)
            ident = ""
            for p in reversed(rec.params):
                if isinstance(p, Str) and p.decoded:
                    ident = p.decoded
                    break
            model.categories["datum"].append(PMIItem(
                eid, f"Datum {ident or '?'}",
                g.first_str(rec.params, skip=0)))
            continue
        for tname in ("DATUM_FEATURE", "DATUM_TARGET",
                      "PLACED_DATUM_TARGET_FEATURE"):
            rec = ent.record(tname)
            if rec is not None and rec.params is not None:
                visited.add(eid)
                nm = g.first_str(rec.params) or g.first_str(rec.params, 1)
                model.categories["datum"].append(PMIItem(
                    eid, f"{tname.replace('_', ' ').title()}"
                         f"{(': ' + nm) if nm else ''}"))
                break

    # ---- annotations & text -----------------------------------------------------
    for eid, ent in sorted(res.entities.items()):
        leafs = g.leafs(ent)
        if any("TEXT_LITERAL" in l for l in leafs):
            visited.add(eid)
            params = g.all_params(ent)
            name = g.first_str(params)
            literal = g.first_str(params, skip=1) or name
            model.categories["annotation"].append(PMIItem(
                eid, f"3D text: {literal!r}", name))
            continue
        if any("COMPOSITE_TEXT" in l for l in leafs):
            visited.add(eid)
            texts = []
            for r in g.refs_in(g.all_params(ent)):
                sub = g.deref(r)
                if sub is not None and any("TEXT_LITERAL" in l
                                           for l in g.leafs(sub)):
                    visited.add(sub.eid)
                    texts.append(g.first_str(g.all_params(sub), skip=1))
            model.categories["annotation"].append(PMIItem(
                eid, f"Composite text: {' | '.join(t for t in texts if t)!r}"))
            continue
        if "DRAUGHTING_CALLOUT" in leafs or any(
                l.startswith("ANNOTATION_") or "TESSELLATED_ANNOTATION" in l
                for l in leafs):
            visited.add(eid)
            nm = g.first_str(g.all_params(ent))
            kind = ent.type_name.replace("_", " ").lower()
            model.categories["annotation"].append(PMIItem(
                eid, f"{kind}{(': ' + repr(nm)) if nm else ''}"))

    # ---- saved views ---------------------------------------------------------
    for eid, ent in sorted(res.entities.items()):
        leafs = g.leafs(ent)
        if any("CAMERA_MODEL" in l for l in leafs):
            visited.add(eid)
            nm = g.first_str(g.all_params(ent))
            model.categories["view"].append(PMIItem(
                eid, f"Camera / view: {nm!r}"))
        elif "DRAUGHTING_MODEL" in leafs or any(
                "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION" in l
                for l in leafs):
            visited.add(eid)
            nm = g.first_str(g.all_params(ent))
            nitems = sum(len(v) for v in g.all_params(ent)
                         if isinstance(v, list))
            model.categories["view"].append(PMIItem(
                eid, f"Presentation model {nm!r} ({nitems} item(s))"))

    # ---- notes & properties ----------------------------------------------------
    for eid, ent in sorted(res.entities.items()):
        rec = ent.record("DESCRIPTIVE_REPRESENTATION_ITEM")
        if rec is not None and rec.params is not None:
            visited.add(eid)
            model.categories["note"].append(PMIItem(
                eid, f"{g.first_str(rec.params) or 'note'}: "
                     f"{g.first_str(rec.params, skip=1)!r}"))
            continue
        rec = ent.record("PROPERTY_DEFINITION")
        if rec is not None and rec.params is not None \
                and len(ent.records) == 1:
            visited.add(eid)
            nm = g.first_str(rec.params)
            ds = g.first_str(rec.params, skip=1)
            model.categories["note"].append(PMIItem(
                eid, f"Property {nm!r}" + (f" — {ds!r}" if ds else "")))
            continue
        if any("SURFACE_TEXTURE" in l for l in g.leafs(ent)):
            visited.add(eid)
            model.categories["note"].append(PMIItem(
                eid, f"Surface texture: "
                     f"{g.first_str(g.all_params(ent)) or ent.type_name}"))

    # ---- leftovers: PMI-related but not interpreted above ----------------------
    for eid, cat in sorted(pmi_ents.items()):
        if eid in visited:
            continue
        ent = res.entities[eid]
        strs = [v.decoded for v in g.all_params(ent)
                if isinstance(v, Str) and v.decoded][:3]
        label = ent.type_name
        if strs:
            label += "  " + " / ".join(repr(s) for s in strs)
        model.categories["other"].append(PMIItem(
            eid, label, f"(category: {cat})", interpreted=False))

    return model
