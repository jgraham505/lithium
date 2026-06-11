# STEP Inspector

A desktop GUI application for **auditing STEP files (ISO 10303-21, `.step`/`.stp`/`.p21`)
for proprietary information** — without having to read them as raw text.

It shows the geometry, lists every entity and attribute, and — the core
feature — **proves that every byte of the file was read**. Anything that is
*not* consumed into the structured data model (comments, stray text, data
after the end marker, unparseable records) is presented as **orphaned data**
for human review.

![Geometry view](docs/geometry.png)

## Why

STEP files routinely leave a company's walls — sent to suppliers, customers,
and contract manufacturers. Besides the part geometry they can carry author
names, organization names, internal system identifiers, comments, custom
records, and free-text properties. Reviewing that by eyeballing a text file
is error-prone: it's easy to miss a comment or a non-standard record.

STEP Inspector turns the review into a checklist:

1. **Byte accounting.** The audit parser classifies the file into spans
   (entities, header, structure markers, comments, whitespace, orphaned) and
   verifies the spans **tile the file exactly** — no gaps, no overlaps. The
   green/orange banner tells you at a glance whether everything was read and
   what still needs eyes.
2. **Orphaned data.** Comments, unrecognized statements, text outside
   sections, anything after `END-ISO-10303-21`, unterminated constructs —
   all listed with line numbers and full text.
3. **Everything searchable.** Every entity with parsed attributes, every
   string literal in the file (with `\X2\…` escape sequences decoded),
   header metadata (author, organization, originating system, …).
4. **Second reader.** When available, the file is *also* read with the
   official [`steptools`](https://pypi.org/project/steptools/) library
   (STEP Tools, Inc.) and the two entity sets are cross-checked — extra
   confidence that the audit parser didn't miss an instance.

## AP242, PMI and GD&T

The audit layer is schema-agnostic: any ISO 10303-21 file is fully
byte-accounted regardless of AP (203/214/224/238/242 …), including Part 21
edition-3 constructs (`ANCHOR`/`REFERENCE`/`SIGNATURE` sections are read
and surfaced for review).

For files that carry extra product information — AP242 MBD in particular —
the **PMI / GD&T tab** presents it in readable form:

* **Geometric tolerances** — type (flatness, position, …), magnitude with
  units, the toleranced feature, resolved **datum reference letters**, and
  modifiers; both simple and complex-instance forms are handled.
* **Dimensions** — dimensional size/location with nominal values and
  plus/minus tolerance ranges.
* **Datums** — datum letters, datum features, datum targets.
* **Annotations & 3D text** — `TEXT_LITERAL`/composite text content (where
  free-text PMI notes live), annotation occurrences, draughting callouts.
* **Saved views** — cameras and presentation/draughting models.
* **Notes & properties** — descriptive items, property definitions,
  surface texture.

Consistent with the audit philosophy, the extractor never hides anything:
any PMI-related entity it cannot interpret is listed under **"Other
PMI-related items"** with its text content, flagged for review — and every
instance is always visible in the Entities tab and the triage map.
Tessellated PMI presentation (`TESSELLATED_CURVE_SET` leaders/frames and
triangulated sets) is rendered in the Geometry tab.

![PMI / GD&T tab](docs/pmi.png)

## Screenshots

| Overview & coverage proof | Entities browser |
|---|---|
| ![Overview](docs/overview.png) | ![Entities](docs/entities.png) |

| Color-coded file audit | Comments & orphaned data |
|---|---|
| ![Audit](docs/audit.png) | ![Review](docs/review.png) |

## The tabs

* **Overview** — file metadata, byte-accounting table, header fields
  (name, time stamp, author, organization, preprocessor, originating system,
  authorization), entity inventory, steptools cross-check result.
* **Entities** — all instances grouped by type. Selecting one shows parsed
  attributes (double-click any `#ref` to follow it), reverse references
  ("referenced by"), the raw source text, and the steptools EXPRESS/ARM view.
* **Geometry** — dependency-free 3D wireframe viewer: B-rep edges (lines,
  circles, ellipses, B-splines, trimmed curves), polylines, tessellated
  meshes and curve sets (AP242 tessellated PMI), vertices and free points. Drag to rotate, right-drag to pan,
  wheel to zoom. Curves the viewer can't evaluate are still drawn (as
  dashed chords) and counted — nothing is silently dropped.
* **PMI / GD&T** — geometric tolerances, dimensions, datums, annotation
  text, saved views, and notes/properties in readable form (see
  [AP242, PMI and GD&T](#ap242-pmi-and-gdt)).
* **File audit** — the complete file with a line-number gutter, colored by
  classification. "Next review item" cycles through comments and orphans.
  Double-click an entity line to open it in the Entities tab.
* **Comments & orphans** — the review queue: every comment, every orphaned
  span, plus warnings (unparseable parameters, dangling references,
  non-standard header records, non-ASCII bytes, …). Double-click jumps to
  the spot in the file.
* **Strings** — every string literal in the file in one searchable table;
  strings whose escape sequences couldn't be fully decoded are flagged with
  their raw form.

`File ▸ Export audit report…` writes the whole audit to a text file:
coverage proof, inventory, review items, orphan text, all strings, and a
**triage map** — one row per classified span showing its byte range,
line number(s), category, identity, and where it was triaged to
(consumed into the data model / header / structure, or *not* consumed and
sent to the review queue). The map ends with a byte total that must equal
the file size, so the report itself is auditable. Omit the map for very
large files with `--no-map` in CLI mode.

## Install & run

Requires Python 3.10+ with Tkinter (on Debian/Ubuntu: `apt install python3-tk`).

```sh
pip install steptools        # optional second reader (see note below)
python -m step_inspector [path/to/file.step]
```

or install the package, which adds a `step-inspector` command:

```sh
pip install .
step-inspector path/to/file.step
```

### Headless report (CI / scripting)

```sh
python -m step_inspector --report file.step > file.audit.txt
```

Exit code is `2` if byte-coverage verification fails, `0` otherwise.

### About the steptools second reader

The `steptools` package is proprietary (STEP Tools, Inc.) and its read API
requires a license key — free keys are issued at
[steptools.com](https://www.steptools.com/). Set the key string in the
`STEPTOOLS_LICENSE` environment variable. **Without it the application is
fully functional**: the audit parser, geometry, and all review features are
independent; only the cross-check panel reports that the second reader is
unavailable. You can also skip it explicitly with `--no-steptools`.

## Demo file

`samples/cube_demo.step` (regenerate with `python samples/make_sample.py`)
is a structurally complete AP214 cube that deliberately contains the things
this tool exists to catch: comments with sensitive-looking text, an
`\X2\`-encoded string, free-text properties, a non-standard record inside
`DATA`, and trailing text after the end marker.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Limitations

* The geometry view is a review aid, not a CAD kernel: surfaces are shown by
  their edge wireframes, and assembly placement transforms are not applied
  (each part renders in its local frame).
* Part 21 edition-3 sections (`ANCHOR`/`REFERENCE`/`SIGNATURE`) are read and
  surfaced for review rather than interpreted.

## License

MIT — see [LICENSE](LICENSE). The optional `steptools` library has its own
proprietary license terms.
