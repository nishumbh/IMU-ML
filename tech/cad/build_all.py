"""Regenerate every CAD part, export STL / 3MF / STEP to tech/cad/out/, and report.

Run:  .venv/bin/python tech/cad/build_all.py [--no-export]

Steps: (1) interference check of the assembly (assembly.py), (2) build each part,
(3) export STEP (build123d), STL (build123d) and 3MF (written here, since build123d has
no 3MF exporter), (4) printability summary: overhang faces and wall-thickness rules,
(5) bounding boxes.
"""
import math
import os
import sys
import zipfile
from xml.sax.saxutils import escape

import build123d as bd
import common as C
import assembly
from thigh_pod_body import build_body
from thigh_pod_lid import build_lid
from strap_mount import build_plate

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
MAX_OVERHANG_DEG = 45.0


def write_3mf(shape, path, tol=0.02, ang=0.2):
    verts, tris = shape.tessellate(tol, ang)
    v_xml = "".join(
        f'<vertex x="{p.X:.4f}" y="{p.Y:.4f}" z="{p.Z:.4f}"/>' for p in verts)
    t_xml = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="1" type="model"><mesh>'
        f"<vertices>{v_xml}</vertices><triangles>{t_xml}</triangles>"
        "</mesh></object></resources>"
        '<build><item objectid="1"/></build></model>')
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def overhang_faces(shape):
    """Area (mm^2) of surface needing support: triangles facing down more than 45 deg
    from vertical, excluding the bed contact (triangles at the minimum z).

    Works on the tessellation, so curved faces (the plate arc) are judged per triangle.
    Returns (triangle count, area, bridge area) where bridge area is the part of that
    which faces straight down (horizontal ceilings, printed as bridges).
    """
    zmin = shape.bounding_box().min.Z
    verts, tris = shape.tessellate(0.05, 0.1)
    pts = [(v.X, v.Y, v.Z) for v in verts]
    limit = -math.cos(math.radians(MAX_OVERHANG_DEG))
    count, area, bridge = 0, 0.0, 0.0
    for a, b, c in tris:
        p, q, r = pts[a], pts[b], pts[c]
        ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
        vx, vy, vz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-12:
            continue
        ta = 0.5 * length
        nz /= length
        cz = (p[2] + q[2] + r[2]) / 3.0
        if nz > limit or cz < zmin + 0.05:
            continue
        count += 1
        area += ta
        if nz < -0.99:
            bridge += ta
    return count, area, bridge


def flip(shape):
    """Turn a part so its outer top face is on the bed (lid, plate)."""
    s = shape.rotate(bd.Axis.X, 180)
    bb = s.bounding_box()
    return s.moved(bd.Pos(0, 0, -bb.min.Z))


def on_end(shape):
    """Stand a curved plate on one of its arc ends (108 mm tall, 22 mm footprint)."""
    s = shape.rotate(bd.Axis.Y, 90)
    return s.moved(bd.Pos(0, 0, -s.bounding_box().min.Z))


def export(part, stem):
    os.makedirs(OUT, exist_ok=True)
    bd.export_step(part, os.path.join(OUT, stem + ".step"))
    bd.export_stl(part, os.path.join(OUT, stem + ".stl"), tolerance=0.02, angular_tolerance=0.2)
    write_3mf(part, os.path.join(OUT, stem + ".3mf"))


def main() -> int:
    do_export = "--no-export" not in sys.argv

    print("== 1. assembly interference check ==")
    n_problems = assembly.check(True) + assembly.check(False)
    if n_problems:
        print(f"FAILED: {n_problems} interference(s)")
        return 1

    print("\n== 2. build parts ==")
    parts = {
        "thigh_pod_body": build_body(True),
        "thigh_pod_lid": build_lid(True),
        "shank_pod_body": build_body(False),
        "shank_pod_lid": build_lid(False),
        "strap_plate_thigh": build_plate(C.R_THIGH, "strap_plate_thigh"),
        "strap_plate_shank": build_plate(C.R_SHANK, "strap_plate_shank"),
    }
    orient = {
        "thigh_pod_body": ("bottom on bed", lambda s: s),
        "shank_pod_body": ("bottom on bed", lambda s: s),
        "thigh_pod_lid": ("outer face on bed", flip),
        "shank_pod_lid": ("outer face on bed", flip),
        "strap_plate_thigh": ("standing on an arc end", on_end),
        "strap_plate_shank": ("standing on an arc end", on_end),
    }

    print("\n== 3. export ==")
    for stem, part in parts.items():
        if do_export:
            export(part, stem)
            print(f"  {stem}.step / .stl / .3mf written to tech/cad/out/")

    print("\n== 4. printability (as oriented) ==")
    wall_rules = {
        "outer wall": C.WALL,
        "boss / standoff wall around Ø3.2 insert": (C.BOSS_D - C.INSERT_HOLE_D) / 2,
        "lid skin over IMU pocket": C.LID_T - C.MPU_POCKET_DEPTH,
        "magnet floor under 1.6 mm pocket": C.FLOOR - C.POD_MAG_POCKET,
        "plate thickness": C.PLATE_T,
        "plate magnet skin": C.PLATE_T - C.PLATE_MAG_POCKET,
    }
    for label, value in wall_rules.items():
        flag = "ok" if value >= C.MIN_WALL - 1e-9 else "BELOW 1.6"
        print(f"  {label:42s} {value:4.2f} mm  {flag}")
    for stem, part in parts.items():
        label, fn = orient[stem]
        oriented = fn(part)
        n, area, bridge = overhang_faces(oriented)
        print(f"  {stem:20s} [{label}] surfaces >45deg from vertical: {n:4d} triangles,"
              f" {area:7.1f} mm^2 (flat bridges {bridge:6.1f} mm^2)")

    print("\n== 5. bounding boxes (mm, as built) ==")
    for stem, part in parts.items():
        s = part.bounding_box().size
        print(f"  {stem:20s} {s.X:7.2f} x {s.Y:6.2f} x {s.Z:6.2f}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
