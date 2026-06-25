"""Integration with the official ``steptools`` Python library.

The audit parser in :mod:`step_inspector.parser` is the authority on byte
coverage.  The steptools library (STEP Tools, Inc.) provides an independent,
schema-aware second reading of the same file: the recognized schema, the
header objects, every entity instance with its EXPRESS attribute values, and
ARM (application model) recognition.

Cross-checking the two readers gives extra confidence that nothing in the
file was missed: an entity seen by only one of them is reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    from steptools import step as _step
    STEPTOOLS_AVAILABLE = True
    STEPTOOLS_ERROR = ""
except Exception as _e:        # pragma: no cover - import-time environment
    _step = None
    STEPTOOLS_AVAILABLE = False
    STEPTOOLS_ERROR = str(_e)

#: Environment variables checked for a STEP Tools license key string.
LICENSE_ENV_VARS = ("STEPTOOLS_LICENSE", "STEPTOOLS_KEY")


def _ensure_license() -> tuple[bool, str]:
    """Apply a license key from the environment if reading is locked.

    Returns (can_read, detail message).
    """
    try:
        if _step.key_can_read():
            return True, ""
    except Exception:
        pass
    for var in LICENSE_ENV_VARS:
        key = os.environ.get(var, "").strip()
        if key:
            try:
                _step.key_string(key)
            except Exception as e:
                return False, f"license key from ${var} rejected: {e}"
            try:
                if _step.key_can_read():
                    return True, f"license key applied from ${var}"
            except Exception:
                pass
    try:
        hostid = _step.key_hostid()
    except Exception:
        hostid = "?"
    return False, (
        "the installed steptools library has no read license. Request a "
        f"free key from steptools.com for host id {hostid} and set it in "
        f"the {LICENSE_ENV_VARS[0]} environment variable")


@dataclass
class SteptoolsInfo:
    ok: bool
    error: str = ""
    schema_name: str = ""
    schema_type: str = ""
    design_name: str = ""
    arm_count: int = 0
    entity_ids: set = field(default_factory=set)
    type_counts: dict = field(default_factory=dict)   # AIM type -> count
    header_name: dict = field(default_factory=dict)
    header_description: dict = field(default_factory=dict)
    _design: object = None

    def attributes_of(self, eid: int) -> Optional[dict]:
        """EXPRESS attribute name -> printable value for entity ``#eid``."""
        if not self.ok or self._design is None:
            return None
        try:
            obj = self._design.find(f"#{eid}")
        except Exception:
            return None
        if obj is None:
            return None
        out = {}
        try:
            out["(EXPRESS type)"] = _step.type(obj)
            arm = _step.arm_type(obj)
            if arm:
                out["(ARM type)"] = arm
        except Exception:
            pass
        try:
            keys = sorted(obj.keys())
        except Exception:
            return out
        for k in keys:
            try:
                out[k] = _fmt(getattr(obj, k))
            except Exception as e:
                out[k] = f"<error: {e}>"
        return out


def _fmt(v, depth: int = 0) -> str:
    if v is None:
        return "$"
    if isinstance(v, (str, int, float, bool)):
        return repr(v)
    if isinstance(v, (list, tuple, set)):
        if depth >= 2:
            return f"<{len(v)} items>"
        return "(" + ", ".join(_fmt(x, depth + 1) for x in v) + ")"
    # steptools Object
    try:
        eid = v.entity_id()
        t = _step.type(v)
        return f"#{eid} {t}"
    except Exception:
        return str(v)


def _obj_to_dict(obj) -> dict:
    out = {}
    if obj is None:
        return out
    try:
        for k in sorted(obj.keys()):
            try:
                out[k] = _fmt(getattr(obj, k))
            except Exception:
                pass
    except Exception:
        pass
    return out


def load(path: str) -> SteptoolsInfo:
    """Read ``path`` with steptools; never raises."""
    if not STEPTOOLS_AVAILABLE:
        return SteptoolsInfo(ok=False, error=(
            "steptools library not installed: " + STEPTOOLS_ERROR))
    try:
        _step.verbose(False)
    except Exception:
        pass
    can_read, detail = _ensure_license()
    if not can_read:
        return SteptoolsInfo(ok=False, error=f"steptools unavailable: {detail}")
    try:
        design = _step.find_design(path)
    except Exception as e:
        return SteptoolsInfo(ok=False, error=f"steptools could not read the "
                             f"file: {e}")
    if design is None:
        return SteptoolsInfo(ok=False,
                             error="steptools returned no design object")
    info = SteptoolsInfo(ok=True, _design=design)
    # ARM (application model) recognition so the entity view can show ARM
    # types/attributes in addition to the raw AIM (EXPRESS) ones.
    try:
        info.arm_count = int(design.arm_recognize())
    except Exception:
        info.arm_count = 0
    try:
        info.schema_name = design.schema_name() or ""
        info.schema_type = str(design.schema_type().name)
        info.design_name = design.name() or ""
    except Exception:
        pass
    try:
        info.header_name = _obj_to_dict(design.header_name())
        info.header_description = _obj_to_dict(design.header_description())
    except Exception:
        pass
    try:
        for obj in _step.DesignCursor(design):
            try:
                eid = obj.entity_id()
            except Exception:
                continue
            if not eid:
                continue
            info.entity_ids.add(eid)
            try:
                t = _step.type(obj)
            except Exception:
                t = "?"
            info.type_counts[t] = info.type_counts.get(t, 0) + 1
    except Exception as e:
        info.error = f"entity iteration stopped early: {e}"
    return info


@dataclass
class CrossCheck:
    only_in_audit: list      # eids parsed here but not seen by steptools
    only_in_steptools: list  # eids seen by steptools but not parsed here
    common: int

    @property
    def clean(self) -> bool:
        return not self.only_in_audit and not self.only_in_steptools


def cross_check(audit_ids, st: SteptoolsInfo) -> Optional[CrossCheck]:
    if not st.ok or not st.entity_ids:
        return None
    a = set(audit_ids)
    b = set(st.entity_ids)
    return CrossCheck(sorted(a - b), sorted(b - a), len(a & b))
