#!/usr/bin/env python3
"""Generate cube_demo.step — a small but structurally complete AP214 file.

The generated file is the demo/test input for STEP Inspector.  Besides a
manifold B-rep cube it intentionally contains things the inspector should
surface during a proprietary-information review:

* comments with sensitive-looking text (standalone and embedded),
* an \\X2\\ encoded (non-ASCII) string,
* descriptive property text buried in the data,
* an unrecognized statement inside DATA (orphan),
* trailing text after END-ISO-10303-21 (orphan).
"""

import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "cube_demo.step")

lines = []
_next = 1


def e(text: str) -> str:
    """Emit one data entity, return its #id."""
    global _next
    eid = f"#{_next}"
    lines.append(f"{eid}={text};")
    _next += 1
    return eid


S = 40.0  # cube edge length

# --- geometry: 8 vertices ---------------------------------------------------
corners = [(x, y, z) for z in (0.0, S) for y in (0.0, S) for x in (0.0, S)]
pt = [e(f"CARTESIAN_POINT('',({c[0]:.1f},{c[1]:.1f},{c[2]:.1f}))")
      for c in corners]
vx = [e(f"VERTEX_POINT('',{p})") for p in pt]

dir_cache = {}
def d(v):
    if v not in dir_cache:
        dir_cache[v] = e(f"DIRECTION('',({v[0]:.1f},{v[1]:.1f},{v[2]:.1f}))")
    return dir_cache[v]

# --- 12 edges ----------------------------------------------------------------
edge_pairs = [(0, 1), (1, 3), (3, 2), (2, 0),       # bottom
              (4, 5), (5, 7), (7, 6), (6, 4),       # top
              (0, 4), (1, 5), (3, 7), (2, 6)]       # verticals

def unit(a, b):
    v = tuple(bb - aa for aa, bb in zip(corners[a], corners[b]))
    m = max(abs(x) for x in v)
    return tuple(x / m for x in v)

edges = []
for a, b in edge_pairs:
    vec = e(f"VECTOR('',{d(unit(a, b))},1.0)")
    ln = e(f"LINE('',{pt[a]},{vec})")
    edges.append(e(f"EDGE_CURVE('',{vx[a]},{vx[b]},{ln},.T.)"))

# --- 6 faces -----------------------------------------------------------------
# face -> (edge indices in loop order, edge senses, plane origin pt idx, normal)
faces_def = [
    ((0, 1, 2, 3),    (".F.", ".F.", ".F.", ".F."), 0, (0, 0, -1)),  # bottom
    ((4, 5, 6, 7),    (".T.", ".T.", ".T.", ".T."), 4, (0, 0, 1)),   # top
    ((0, 9, 4, 8),    (".T.", ".T.", ".F.", ".F."), 0, (0, -1, 0)),  # front
    ((2, 10, 6, 11),  (".T.", ".T.", ".F.", ".F."), 3, (0, 1, 0)),   # back  (y=S)
    ((3, 8, 7, 11),   (".T.", ".T.", ".T.", ".F."), 2, (-1, 0, 0)),  # left
    ((1, 10, 5, 9),   (".F.", ".T.", ".F.", ".F."), 1, (1, 0, 0)),   # right
]
faces = []
for eidx, senses, origin_i, normal in faces_def:
    oes = [e(f"ORIENTED_EDGE('',*,*,{edges[i]},{s})")
           for i, s in zip(eidx, senses)]
    loop = e(f"EDGE_LOOP('',({','.join(oes)}))")
    bound = e(f"FACE_OUTER_BOUND('',{loop},.T.)")
    ref = (1, 0, 0) if abs(normal[0]) < 1 else (0, 1, 0)
    ax = e(f"AXIS2_PLACEMENT_3D('',{pt[origin_i]},{d(normal)},{d(ref)})")
    plane = e(f"PLANE('',{ax})")
    faces.append(e(f"ADVANCED_FACE('',({bound}),{plane},.T.)"))

shell = e(f"CLOSED_SHELL('',({','.join(faces)}))")
brep = e(f"MANIFOLD_SOLID_BREP('cube body',{shell})")

# --- extra display geometry ---------------------------------------------------
lines.append("/* Inspection annotation below: datum circle and probe path"
             " (Acme part 88-X) */")
circ_ax = e(f"AXIS2_PLACEMENT_3D('',"
            f"{e('CARTESIAN_POINT(' + chr(39) + chr(39) + ',(20.0,20.0,40.0))')},"
            f"{d((0, 0, 1))},{d((1, 0, 0))})")
circle = e(f"CIRCLE('datum circle D1',{circ_ax},12.5)")
probe_pts = [e(f"CARTESIAN_POINT('',({x:.1f},{y:.1f},{z:.1f}))")
             for x, y, z in [(-10, -10, 50), (20, -10, 48),
                             (50, 20, 48), (50, 50, 50)]]
poly = e(f"POLYLINE('probe path',({','.join(probe_pts)}))")

# --- representation context ----------------------------------------------------
uncert_unit = e("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
ang_unit = e("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
sang_unit = e("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
uncert = e(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(0.005),"
           f"{uncert_unit},'distance_accuracy_value','tolerance')")
ctx = e(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({uncert}))"
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({uncert_unit},{ang_unit},{sang_unit}))"
        f"REPRESENTATION_CONTEXT('Context #1','3D Context'))")

world_ax = e(f"AXIS2_PLACEMENT_3D('',"
             f"{e('CARTESIAN_POINT(' + chr(39) + chr(39) + ',(0.0,0.0,0.0))')},"
             f"{d((0, 0, 1))},{d((1, 0, 0))})")
shape_rep = e(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',({world_ax},{brep}),{ctx})")
geo_set = e(f"GEOMETRIC_CURVE_SET('annotations',({circle},{poly}))")
ann_rep = e(f"GEOMETRICALLY_BOUNDED_WIREFRAME_SHAPE_REPRESENTATION("
            f"'annotations',({geo_set}),{ctx})")

# --- product structure ----------------------------------------------------------
app = e("APPLICATION_CONTEXT('automotive design')")
app_pds = e(f"PRODUCT_DEFINITION_CONTEXT('part definition',{app},'design')")
app_pc = e(f"PRODUCT_CONTEXT('',{app},'mechanical')")
product = e(f"PRODUCT('ACME-88-X','Sensor Housing Block',"
            f"'Machined from billet per ACME-SPEC-9912',({app_pc}))")
pdf = e(f"PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE"
        f"('rev C','design release',{product},.NOT_KNOWN.)")
pd = e(f"PRODUCT_DEFINITION('design','',{pdf},{app_pds})")
pds = e(f"PRODUCT_DEFINITION_SHAPE('','',{pd})")
e(f"SHAPE_DEFINITION_REPRESENTATION({pds},{shape_rep})")
e(f"SHAPE_REPRESENTATION_RELATIONSHIP('','',{shape_rep},{ann_rep})")
cat = e("PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(%s))" % product)

# property with buried text + an \X2\ encoded string (Greek mu and degree)
lines.append("/* NOTE: heat treat per internal spec HT-77, contact"
             " j.smith@acme.example x4471 */")
descr = e("DESCRIPTIVE_REPRESENTATION_ITEM('material note',"
          "'17-4PH H900, surface roughness \\X2\\00B5\\X0\\m Ra 0.8,"
          " bake 4h @ 482\\X2\\00B0\\X0\\C')")
prop_rep = e(f"REPRESENTATION('material',({descr}),{ctx})")
prop = e(f"PROPERTY_DEFINITION('material spec','internal use only',{pds})")
e(f"PROPERTY_DEFINITION_REPRESENTATION({prop},{prop_rep})")

data_section = "\n".join(lines)

header = """ISO-10303-21;
HEADER;
/* Exported from AcmeCAD 11.2 -- INTERNAL BUILD, do not distribute */
FILE_DESCRIPTION(('Sensor housing block, milling demo'),'2;1');
FILE_NAME('cube_demo.step','2026-06-11T10:30:00',('J. Smith'),
  ('Acme Precision Instruments, Bldg 7'),
  'AcmeCAD 11.2 (build 4471-internal)','AcmeCAD STEP AP214','J. Smith (eng release)');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
"""

footer = """CUSTOM_TOOL_STATE('vise offset G54 X-112.4 Y8.02','op30 fixture B');
ENDSEC;
END-ISO-10303-21;
Internal routing: APPROVED rev C 2026-06-09, do not send outside Acme. ;
"""

with open(OUT, "w", newline="\n") as f:
    f.write(header)
    f.write(data_section)
    f.write("\n")
    f.write(footer)

print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes, "
      f"{_next - 1} entities)")
