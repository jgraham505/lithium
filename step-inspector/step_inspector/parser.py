"""Byte-exact ISO 10303-21 (STEP Part 21) audit parser.

The goal of this module is *accountability*, not just parsing: every byte of
the input file is assigned to exactly one classified span.  The list of spans
is guaranteed to tile the file with no gaps and no overlaps, so the claim
"every line of this file was read" can be verified, not assumed.

Anything that is not consumed into the structured model (comments, stray
text, unterminated constructs, data outside sections, ...) is classified as
ORPHAN or COMMENT and surfaced to the user for proprietary-information
review.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Optional


# ---------------------------------------------------------------------------
# Span / classification model
# ---------------------------------------------------------------------------

class Kind(Enum):
    STRUCTURE = "structure"        # ISO markers, section keywords, ENDSEC
    HEADER = "header"              # header section entities
    ENTITY = "entity"              # #id=... data records
    COMMENT = "comment"            # /* ... */ between statements
    WHITESPACE = "whitespace"      # blanks between statements
    ORPHAN = "orphan"              # anything not consumed into the model


@dataclass
class Span:
    start: int                     # byte offset, inclusive
    end: int                       # byte offset, exclusive
    kind: Kind
    note: str = ""                 # human-readable classification detail
    ref: Any = None                # Entity / HeaderEntity / str payload

    def text(self, source: str) -> str:
        return self.source_slice(source)

    def source_slice(self, source: str) -> str:
        return source[self.start:self.end]


# ---------------------------------------------------------------------------
# Value model for entity parameters
# ---------------------------------------------------------------------------

class Unset:
    """The ``$`` token (attribute not provided)."""
    __slots__ = ()
    def __repr__(self) -> str: return "$"

class Derived:
    """The ``*`` token (attribute derived/redeclared)."""
    __slots__ = ()
    def __repr__(self) -> str: return "*"

UNSET = Unset()
DERIVED = Derived()


@dataclass(frozen=True)
class Ref:
    """Reference to another entity instance, ``#123``."""
    eid: int
    def __repr__(self) -> str: return f"#{self.eid}"


@dataclass(frozen=True)
class EnumVal:
    """Enumeration value, ``.STEEL.`` (booleans are .T./.F.)."""
    name: str
    def __repr__(self) -> str: return f".{self.name}."


@dataclass(frozen=True)
class Str:
    """String literal with both the raw source form and the decoded text."""
    raw: str                       # contents between the quotes, verbatim
    decoded: str                   # after '' and \X..\ escape decoding
    clean: bool = True             # False if escapes could not be decoded
    def __repr__(self) -> str: return repr(self.decoded)


@dataclass(frozen=True)
class Binary:
    raw: str
    def __repr__(self) -> str: return f'"{self.raw}"'


@dataclass(frozen=True)
class Typed:
    """Typed parameter, ``PARAMETER_VALUE(1.5)``."""
    name: str
    value: Any
    def __repr__(self) -> str: return f"{self.name}({self.value!r})"


# ---------------------------------------------------------------------------
# Structured records
# ---------------------------------------------------------------------------

@dataclass
class SimpleRecord:
    """One ``TYPE(arg, arg, ...)`` unit."""
    type_name: str
    params: Optional[list] = None        # None when parameters failed to parse
    raw_params: str = ""


@dataclass
class Entity:
    eid: int
    records: list                       # [SimpleRecord]; >1 => complex instance
    span: Span
    parse_error: str = ""

    @property
    def is_complex(self) -> bool:
        return len(self.records) > 1

    @property
    def type_name(self) -> str:
        if self.is_complex:
            return "(" + " ".join(r.type_name for r in self.records) + ")"
        return self.records[0].type_name if self.records else "?"

    def record(self, type_name: str) -> Optional[SimpleRecord]:
        for r in self.records:
            if r.type_name == type_name.upper():
                return r
        return None

    @property
    def params(self) -> Optional[list]:
        """Parameters of a simple instance (None for complex/failed)."""
        if len(self.records) == 1:
            return self.records[0].params
        return None


@dataclass
class HeaderEntity:
    type_name: str
    params: Optional[list]
    raw_params: str
    span: Span
    parse_error: str = ""


@dataclass
class CommentRec:
    start: int
    end: int
    text: str                           # contents without the /* */ delimiters
    embedded_in: str = ""               # "" = standalone, else statement desc


@dataclass
class AttentionItem:
    """Something the reviewer should look at."""
    severity: str                       # "orphan" | "warning" | "info"
    where: str                          # human location, e.g. "line 12"
    line: int
    message: str
    excerpt: str = ""


@dataclass
class StringOcc:
    """A string literal found anywhere in the file."""
    location: str                       # "#52 PRODUCT arg 1" / "FILE_NAME arg 2"
    line: int
    value: Str


# ---------------------------------------------------------------------------
# String escape decoding (Part 21 control directives)
# ---------------------------------------------------------------------------

def decode_p21_string(raw: str) -> Str:
    """Decode a Part 21 string body (text between the apostrophes)."""
    out: list[str] = []
    i, n = 0, len(raw)
    clean = True
    while i < n:
        c = raw[i]
        if c == "'":
            # '' is an escaped apostrophe
            out.append("'")
            i += 2
            continue
        if c != "\\":
            out.append(c)
            i += 1
            continue
        # control directive
        m = re.match(r"\\S\\(.)", raw[i:], re.S)
        if m:
            out.append(chr((ord(m.group(1)) + 128) & 0xFF))
            i += len(m.group(0))
            continue
        m = re.match(r"\\X\\([0-9A-Fa-f]{2})", raw[i:])
        if m:
            out.append(chr(int(m.group(1), 16)))
            i += len(m.group(0))
            continue
        m = re.match(r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\", raw[i:])
        if m:
            h = m.group(1)
            out.extend(chr(int(h[j:j + 4], 16)) for j in range(0, len(h), 4))
            i += len(m.group(0))
            continue
        m = re.match(r"\\X4\\((?:[0-9A-Fa-f]{8})+)\\X0\\", raw[i:])
        if m:
            h = m.group(1)
            out.extend(chr(int(h[j:j + 8], 16)) for j in range(0, len(h), 8))
            i += len(m.group(0))
            continue
        m = re.match(r"\\P[A-I]\\", raw[i:])
        if m:
            # alternate codepage selector for subsequent \S\; rare -- keep
            # decoding \S\ as latin-1 but flag the string as not fully clean
            clean = False
            i += len(m.group(0))
            continue
        if raw.startswith("\\\\", i):
            out.append("\\")
            i += 2
            continue
        if raw.startswith("\\N\\", i):
            out.append("\n")
            i += 3
            continue
        # Unknown backslash sequence: keep verbatim, flag it
        clean = False
        out.append(c)
        i += 1
    return Str(raw=raw, decoded="".join(out), clean=clean)


# ---------------------------------------------------------------------------
# Parameter (argument list) parser
# ---------------------------------------------------------------------------

class ParamError(ValueError):
    pass


class _ParamParser:
    """Recursive-descent parser for a Part 21 parameter list."""

    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.n = len(text)

    def parse_list(self) -> list:
        """Parse a comma-separated value sequence up to end of text."""
        vals: list = []
        self._ws()
        if self.i >= self.n:
            return vals
        while True:
            vals.append(self.parse_value())
            self._ws()
            if self.i >= self.n:
                return vals
            if self.text[self.i] == ",":
                self.i += 1
                self._ws()
                continue
            raise ParamError(
                f"unexpected character {self.text[self.i]!r} at offset {self.i}")

    def parse_value(self) -> Any:
        self._ws()
        if self.i >= self.n:
            raise ParamError("unexpected end of parameters")
        c = self.text[self.i]
        if c == "$":
            self.i += 1
            return UNSET
        if c == "*":
            self.i += 1
            return DERIVED
        if c == "#":
            m = re.match(r"#(\d+)", self.text[self.i:])
            if not m:
                raise ParamError(f"bad entity reference at offset {self.i}")
            self.i += len(m.group(0))
            return Ref(int(m.group(1)))
        if c == "'":
            return self._string()
        if c == '"':
            m = re.match(r'"([0-9A-Fa-f]*)"', self.text[self.i:])
            if not m:
                raise ParamError(f"bad binary literal at offset {self.i}")
            self.i += len(m.group(0))
            return Binary(m.group(1))
        if c == ".":
            m = re.match(r"\.([A-Za-z0-9_]+)\.", self.text[self.i:])
            if not m:
                raise ParamError(f"bad enumeration at offset {self.i}")
            self.i += len(m.group(0))
            return EnumVal(m.group(1).upper())
        if c == "(":
            self.i += 1
            self._ws()
            if self.i < self.n and self.text[self.i] == ")":
                self.i += 1
                return []
            vals = [self.parse_value()]
            while True:
                self._ws()
                if self.i >= self.n:
                    raise ParamError("unterminated list")
                if self.text[self.i] == ",":
                    self.i += 1
                    vals.append(self.parse_value())
                    continue
                if self.text[self.i] == ")":
                    self.i += 1
                    return vals
                raise ParamError(
                    f"unexpected character {self.text[self.i]!r} in list "
                    f"at offset {self.i}")
        m = re.match(r"[+-]?(\d+\.\d*([Ee][+-]?\d+)?|\d+[Ee][+-]?\d+|\.\d+([Ee][+-]?\d+)?)",
                     self.text[self.i:])
        if m:
            self.i += len(m.group(0))
            return float(m.group(0))
        m = re.match(r"[+-]?\d+", self.text[self.i:])
        if m:
            self.i += len(m.group(0))
            return int(m.group(0))
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", self.text[self.i:])
        if m:
            name = m.group(0).upper()
            self.i += len(m.group(0))
            self._ws()
            if self.i < self.n and self.text[self.i] == "(":
                self.i += 1
                inner_start = self.i
                # typed parameter holds exactly one value
                val = self.parse_value()
                self._ws()
                if self.i >= self.n or self.text[self.i] != ")":
                    raise ParamError(
                        f"unterminated typed parameter {name} at offset {inner_start}")
                self.i += 1
                return Typed(name, val)
            raise ParamError(f"bare keyword {name!r} at offset {self.i}")
        raise ParamError(f"unexpected character {c!r} at offset {self.i}")

    def _string(self) -> Str:
        assert self.text[self.i] == "'"
        j = self.i + 1
        while True:
            k = self.text.find("'", j)
            if k < 0:
                raise ParamError("unterminated string literal")
            if k + 1 < self.n and self.text[k + 1] == "'":
                j = k + 2
                continue
            body = self.text[self.i + 1:k]
            self.i = k + 1
            return decode_p21_string(body)

    def _ws(self) -> None:
        while self.i < self.n and self.text[self.i] in " \t\r\n":
            self.i += 1


def parse_params(text: str) -> list:
    """Parse a parameter list string; raises ParamError on failure."""
    p = _ParamParser(text)
    vals = p.parse_list()
    return vals


# ---------------------------------------------------------------------------
# File-level scanner
# ---------------------------------------------------------------------------

_SECTION_KEYWORDS = {"HEADER", "ENDSEC", "ANCHOR", "REFERENCE", "SIGNATURE"}
_KNOWN_HEADER = {"FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA",
                 "FILE_POPULATION", "SECTION_LANGUAGE", "SECTION_CONTEXT"}

_ENTITY_RE = re.compile(r"^#(\d+)\s*=\s*(.*)$", re.S)
_SIMPLE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)$", re.S)
_DATA_RE = re.compile(r"^DATA(\s*\(.*\))?$", re.S | re.I)


@dataclass
class AuditResult:
    path: str
    source: str                          # latin-1 decoded => 1 byte per char
    spans: list = field(default_factory=list)
    entities: dict = field(default_factory=dict)      # eid -> Entity
    entity_order: list = field(default_factory=list)  # eids in file order
    header: list = field(default_factory=list)        # [HeaderEntity]
    comments: list = field(default_factory=list)      # [CommentRec]
    attention: list = field(default_factory=list)     # [AttentionItem]
    strings: list = field(default_factory=list)       # [StringOcc]
    referenced_by: dict = field(default_factory=dict) # eid -> set(eids)
    line_starts: list = field(default_factory=list)
    schema_names: list = field(default_factory=list)
    has_iso_start: bool = False
    has_iso_end: bool = False

    # -- coordinates ------------------------------------------------------
    def line_of(self, offset: int) -> int:
        """1-based line number containing byte ``offset``."""
        return bisect_right(self.line_starts, offset)

    @property
    def total_bytes(self) -> int:
        return len(self.source)

    @property
    def total_lines(self) -> int:
        return len(self.line_starts)

    # -- verification ------------------------------------------------------
    def verify_coverage(self) -> bool:
        """True iff spans tile the file exactly (no gaps, no overlaps)."""
        pos = 0
        for s in self.spans:
            if s.start != pos:
                return False
            pos = s.end
        return pos == len(self.source)

    def bytes_by_kind(self) -> dict:
        out: dict = {k: 0 for k in Kind}
        for s in self.spans:
            out[s.kind] += s.end - s.start
        return out

    def orphan_spans(self) -> list:
        return [s for s in self.spans if s.kind is Kind.ORPHAN]

    def header_entity(self, name: str) -> Optional[HeaderEntity]:
        for h in self.header:
            if h.type_name == name.upper():
                return h
        return None


def _strip_comments(stmt: str) -> str:
    """Remove /* */ comments outside of string literals."""
    out = []
    i, n = 0, len(stmt)
    while i < n:
        c = stmt[i]
        if c == "'":
            j = i + 1
            while j < n:
                k = stmt.find("'", j)
                if k < 0:
                    j = n
                    break
                if k + 1 < n and stmt[k + 1] == "'":
                    j = k + 2
                    continue
                j = k + 1
                break
            out.append(stmt[i:j])
            i = j
        elif stmt.startswith("/*", i):
            k = stmt.find("*/", i + 2)
            i = n if k < 0 else k + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


class Auditor:
    """Scans a Part 21 file into a fully classified AuditResult."""

    def __init__(self, path: str, data: bytes):
        # latin-1 maps every byte to exactly one char: lossless accounting
        self.src = data.decode("latin-1")
        self.res = AuditResult(path=path, source=self.src)
        self.res.line_starts = [0] + \
            [m.end() for m in re.finditer(r"\n", self.src)]
        if self.res.line_starts and self.res.line_starts[-1] == len(self.src):
            # no content after the final newline -> last entry is not a line
            if len(self.src) and self.src.endswith("\n"):
                self.res.line_starts.pop()
        self.i = 0
        self.n = len(self.src)
        self.section: Optional[str] = None   # HEADER/DATA/ANCHOR/.../None
        self.iso_ended = False

    # -- helpers -----------------------------------------------------------
    def _attn(self, severity: str, start: int, message: str, excerpt: str = ""):
        line = self.res.line_of(start)
        self.res.attention.append(AttentionItem(
            severity=severity, where=f"line {line}", line=line,
            message=message, excerpt=excerpt[:200]))

    def _span(self, start: int, end: int, kind: Kind, note: str = "",
              ref: Any = None) -> Span:
        s = Span(start, end, kind, note, ref)
        self.res.spans.append(s)
        return s

    # -- main scan ---------------------------------------------------------
    def run(self) -> AuditResult:
        while self.i < self.n:
            c = self.src[self.i]
            if c in " \t\r\n":
                start = self.i
                while self.i < self.n and self.src[self.i] in " \t\r\n":
                    self.i += 1
                self._span(start, self.i, Kind.WHITESPACE)
            elif self.src.startswith("/*", self.i):
                self._comment()
            else:
                self._statement()
        self._finalize()
        return self.res

    def _comment(self) -> None:
        start = self.i
        end = self.src.find("*/", self.i + 2)
        if end < 0:
            self._span(start, self.n, Kind.ORPHAN, "unterminated comment")
            self._attn("orphan", start, "Unterminated /* comment runs to "
                       "end of file", self.src[start:start + 200])
            self.res.comments.append(CommentRec(
                start, self.n, self.src[start + 2:]))
            self.i = self.n
            return
        end += 2
        body = self.src[start + 2:end - 2]
        self._span(start, end, Kind.COMMENT, "comment", body)
        self.res.comments.append(CommentRec(start, end, body))
        self.i = end

    def _statement(self) -> None:
        """Consume up to the terminating ';' honouring strings/comments."""
        start = self.i
        i, n, src = self.i, self.n, self.src
        embedded: list[tuple[int, int]] = []
        while i < n:
            c = src[i]
            if c == ";":
                i += 1
                break
            if c == "'":
                j = i + 1
                while True:
                    k = src.find("'", j)
                    if k < 0:
                        j = n
                        break
                    if k + 1 < n and src[k + 1] == "'":
                        j = k + 2
                        continue
                    j = k + 1
                    break
                i = j
                continue
            if src.startswith("/*", i):
                k = src.find("*/", i + 2)
                k = n if k < 0 else k + 2
                embedded.append((i, k))
                i = k
                continue
            i += 1
        else:
            pass
        self.i = i
        raw = src[start:i]
        if not raw.rstrip().endswith(";"):
            self._span(start, i, Kind.ORPHAN, "unterminated statement")
            self._attn("orphan", start,
                       "Text at end of file is not a complete statement "
                       "(missing ';')", raw)
            return
        self._classify(start, i, raw, embedded)

    def _classify(self, start: int, end: int, raw: str,
                  embedded: list) -> None:
        for (cs, ce) in embedded:
            body = self.src[cs + 2:ce - 2] if self.src.startswith("/*", cs) else self.src[cs:ce]
            self.res.comments.append(CommentRec(
                cs, ce, body, embedded_in=f"inside statement at line "
                f"{self.res.line_of(start)}"))
        stmt = _strip_comments(raw).strip()
        assert stmt.endswith(";")
        stmt = stmt[:-1].strip()
        upper = stmt.upper()

        if upper == "ISO-10303-21":
            self.res.has_iso_start = True
            self._span(start, end, Kind.STRUCTURE, "file start marker")
            if start != 0 and any(s.kind not in (Kind.WHITESPACE, Kind.COMMENT)
                                  for s in self.res.spans):
                self._attn("warning", start,
                           "ISO-10303-21 marker is not the first statement")
            return
        if upper == "END-ISO-10303-21":
            self.res.has_iso_end = True
            self.iso_ended = True
            self._span(start, end, Kind.STRUCTURE, "file end marker")
            return
        if self.iso_ended:
            self._span(start, end, Kind.ORPHAN, "data after END-ISO-10303-21")
            self._attn("orphan", start,
                       "Statement appears after the END-ISO-10303-21 marker",
                       raw)
            return
        if upper in _SECTION_KEYWORDS or _DATA_RE.match(upper):
            kw = "DATA" if upper.startswith("DATA") else upper
            if kw == "ENDSEC":
                if self.section is None:
                    self._attn("warning", start, "ENDSEC without open section")
                self.section = None
                self._span(start, end, Kind.STRUCTURE, "end of section")
            else:
                if self.section is not None:
                    self._attn("warning", start,
                               f"{kw} section opened inside {self.section}")
                self.section = kw
                self._span(start, end, Kind.STRUCTURE, f"{kw} section start")
                if kw in ("ANCHOR", "REFERENCE", "SIGNATURE"):
                    self._attn("info", start,
                               f"{kw} section present (Part 21 edition 3); "
                               "its records are listed for review")
            return

        m = _ENTITY_RE.match(stmt)
        if m:
            self._entity(start, end, raw, int(m.group(1)), m.group(2).strip())
            return

        m = _SIMPLE_RE.match(stmt)
        if m and self.section == "HEADER":
            self._header_entity(start, end, raw, m.group(1).upper(),
                                m.group(2))
            return

        # Anything else is not consumed by the model.
        where = self.section or "outside any section"
        self._span(start, end, Kind.ORPHAN, f"unrecognized statement ({where})")
        self._attn("orphan", start,
                   f"Statement not recognized as STEP structure, header or "
                   f"entity ({where})", raw)

    def _header_entity(self, start: int, end: int, raw: str,
                       name: str, params_text: str) -> None:
        err = ""
        params: Optional[list] = None
        try:
            params = parse_params(params_text)
        except ParamError as e:
            err = str(e)
        span = self._span(start, end, Kind.HEADER, name)
        h = HeaderEntity(name, params, params_text, span, err)
        span.ref = h
        self.res.header.append(h)
        if name not in _KNOWN_HEADER:
            self._attn("warning", start,
                       f"Unusual header entity {name} (not one of the "
                       "standard Part 21 header records)", raw)
        if err:
            self._attn("warning", start,
                       f"Header entity {name}: parameters could not be fully "
                       f"parsed ({err}); raw text shown for review", raw)
        if params is not None:
            self._collect_strings(f"HEADER {name}", start, params)
        if name == "FILE_SCHEMA" and params:
            try:
                self.res.schema_names = [
                    s.decoded for s in params[0] if isinstance(s, Str)]
            except TypeError:
                pass

    def _entity(self, start: int, end: int, raw: str,
                eid: int, rhs: str) -> None:
        if self.section != "DATA":
            self._attn("warning", start,
                       f"Entity #{eid} found {('in ' + self.section) if self.section else 'outside any section'}"
                       " (expected inside DATA)")
        err = ""
        records: list[SimpleRecord] = []
        rhs = rhs.strip()
        try:
            records = self._parse_rhs(rhs)
        except ParamError as e:
            err = str(e)
            m = _SIMPLE_RE.match(rhs)
            if m:
                records = [SimpleRecord(m.group(1).upper(), None, m.group(2))]
            else:
                records = [SimpleRecord("?", None, rhs)]
        span = self._span(start, end, Kind.ENTITY, f"#{eid}")
        ent = Entity(eid, records, span, err)
        span.ref = ent
        if eid in self.res.entities:
            self._attn("warning", start,
                       f"Duplicate entity id #{eid}; later definition kept, "
                       "both occupy file spans", raw)
        self.res.entities[eid] = ent
        self.res.entity_order.append(eid)
        if err:
            self._attn("warning", start,
                       f"Entity #{eid}: parameters could not be fully parsed "
                       f"({err}); raw text shown for review", raw)
        for rec in records:
            if rec.params is not None:
                self._collect_strings(f"#{eid} {rec.type_name}", start,
                                      rec.params)
                self._collect_refs(eid, rec.params)

    def _parse_rhs(self, rhs: str) -> list:
        if rhs.startswith("("):
            # complex (multi-leaf) instance: ( A(...) B(...) ... )
            if not rhs.endswith(")"):
                raise ParamError("complex instance not closed with ')'")
            inner = rhs[1:-1].strip()
            records = []
            i, n = 0, len(inner)
            while i < n:
                while i < n and inner[i] in " \t\r\n":
                    i += 1
                if i >= n:
                    break
                m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", inner[i:])
                if not m:
                    raise ParamError(
                        f"expected TYPE( in complex instance at offset {i}")
                name = m.group(1).upper()
                j = i + len(m.group(0))
                depth = 1
                k = j
                while k < n and depth:
                    c = inner[k]
                    if c == "'":
                        kk = k + 1
                        while True:
                            q = inner.find("'", kk)
                            if q < 0:
                                raise ParamError("unterminated string")
                            if q + 1 < n and inner[q + 1] == "'":
                                kk = q + 2
                                continue
                            kk = q + 1
                            break
                        k = kk
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                    k += 1
                if depth:
                    raise ParamError(f"unterminated record {name}")
                ptext = inner[j:k - 1]
                records.append(SimpleRecord(name, parse_params(ptext), ptext))
                i = k
            if not records:
                raise ParamError("empty complex instance")
            return records
        m = _SIMPLE_RE.match(rhs)
        if not m:
            raise ParamError("right-hand side is not TYPE(...) form")
        ptext = m.group(2)
        return [SimpleRecord(m.group(1).upper(), parse_params(ptext), ptext)]

    # -- value walkers ------------------------------------------------------
    def _collect_strings(self, loc: str, offset: int, values: list,
                         path: str = "") -> None:
        line = self.res.line_of(offset)
        for idx, v in enumerate(values):
            p = f"{path}.{idx}" if path else str(idx)
            if isinstance(v, Str):
                self.res.strings.append(StringOcc(
                    f"{loc} arg {p}", line, v))
                if not v.clean:
                    self._attn("warning", offset,
                               f"String in {loc} arg {p} contains escape "
                               "sequences that could not be fully decoded; "
                               "review the raw form", v.raw)
            elif isinstance(v, list):
                self._collect_strings(loc, offset, v, p)
            elif isinstance(v, Typed):
                self._collect_strings(loc, offset, [v.value], p)

    def _collect_refs(self, eid: int, values: list) -> None:
        for v in values:
            if isinstance(v, Ref):
                self.res.referenced_by.setdefault(v.eid, set()).add(eid)
            elif isinstance(v, list):
                self._collect_refs(eid, v)
            elif isinstance(v, Typed):
                self._collect_refs(eid, [v.value])

    # -- end of scan ---------------------------------------------------------
    def _finalize(self) -> None:
        res = self.res
        if not res.has_iso_start:
            self._attn("warning", 0, "No ISO-10303-21; start marker found — "
                       "file may not be a STEP Part 21 file")
        if not res.has_iso_end:
            self._attn("warning", max(self.n - 1, 0),
                       "No END-ISO-10303-21; end marker found")
        # dangling references
        missing: dict[int, set] = {}
        for target, sources in res.referenced_by.items():
            if target not in res.entities:
                missing[target] = sources
        for target, sources in sorted(missing.items()):
            src_txt = ", ".join(f"#{s}" for s in sorted(sources)[:8])
            ent = res.entities.get(sorted(sources)[0])
            line = res.line_of(ent.span.start) if ent else 0
            res.attention.append(AttentionItem(
                "warning", f"line {line}", line,
                f"Reference to missing entity #{target} (from {src_txt})"))
        # standalone comments are review items by definition
        for c in res.comments:
            line = res.line_of(c.start)
            res.attention.append(AttentionItem(
                "comment", f"line {line}", line,
                "Comment (not part of the data model)"
                + (f" — {c.embedded_in}" if c.embedded_in else ""),
                c.text.strip()[:200]))
        # non-ASCII bytes outside of what latin-1 decoding hides
        bad = [(m.start(), m.group(0)) for m in
               re.finditer(r"[^\x09\x0a\x0d\x20-\x7e]", self.src)]
        if bad:
            first = bad[0][0]
            self._attn("warning", first,
                       f"{len(bad)} non-ASCII byte(s) present (first at line "
                       f"{res.line_of(first)}); shown as Latin-1")
        res.attention.sort(key=lambda a: a.line)


def audit_file(path: str) -> AuditResult:
    with open(path, "rb") as f:
        data = f.read()
    return Auditor(path, data).run()


def audit_bytes(data: bytes, path: str = "<memory>") -> AuditResult:
    return Auditor(path, data).run()


# ---------------------------------------------------------------------------
# Pretty-printing helpers shared by GUI and report export
# ---------------------------------------------------------------------------

def format_value(v: Any, maxlen: int = 80) -> str:
    if isinstance(v, Str):
        s = v.decoded
        if len(s) > maxlen:
            s = s[:maxlen] + "…"
        return f"'{s}'"
    if isinstance(v, list):
        inner = ", ".join(format_value(x, maxlen) for x in v)
        return f"({inner})"
    if isinstance(v, float):
        return f"{v:g}"
    return repr(v)


def iter_refs(values: list) -> Iterator[Ref]:
    for v in values or []:
        if isinstance(v, Ref):
            yield v
        elif isinstance(v, list):
            yield from iter_refs(v)
        elif isinstance(v, Typed):
            yield from iter_refs([v.value])
