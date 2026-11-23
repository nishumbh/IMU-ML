"""Magnetic strap plate (TPU 95A) for one limb.

Curved plate: a sector of a cylinder ring whose axis is the limb axis (Y). The pod's
magnet face sits on the plate's outer top at X = 0. Curvature comes from the radius R
(inner surface, against the limb). Thigh and shank use different R (common.py).

Two through-slots at X = +/- PLATE_SLOT_X carry the 38 mm strap (strap runs through
the slot, around the limb, back through the other slot). Three magnet pockets (Ø6.2 x
1.1 mm) face the pod on the centre column.

Print: TPU 95A, convex (outer) face down on the bed. See PRINT_NOTES.md.
"""
import math

import build123d as bd
from build123d import Mode
import common as C


def build_plate(R: float = C.R_THIGH, name: str = "strap_plate_thigh") -> bd.Part:
    T = C.PLATE_T
    W = C.PLATE_W
    Ro = R + T                       # outer radius
    half_chord = C.PLATE_CHORD / 2
    alpha = math.asin(min(half_chord / Ro, 0.999))  # half angle of the sector
    big = 3.0 * Ro

    # Ring (annulus) in the XZ plane, extruded along Y. Ring centre at (0, 0, -Ro).
    with bd.BuildPart() as ring:
        with bd.BuildSketch(bd.Plane.XZ):
            with bd.Locations((0, -Ro)):
                bd.Circle(Ro)
                bd.Circle(R, mode=Mode.SUBTRACT)
        bd.extrude(amount=W / 2, both=True)

    # Sector wedge with its apex at the ring centre, opening towards +Z.
    pts = [
        (0.0, -Ro),
        (-big * math.sin(alpha), -Ro + big * math.cos(alpha)),
        (big * math.sin(alpha), -Ro + big * math.cos(alpha)),
    ]
    with bd.BuildPart() as wedge:
        with bd.BuildSketch(bd.Plane.XZ):
            bd.Polygon(*pts)
        bd.extrude(amount=W / 2 + 1.0, both=True)

    plate = ring.part & wedge.part

    # Strap slots, through the plate thickness near each end (outside the pod footprint).
    for sx in (-C.PLATE_SLOT_X, C.PLATE_SLOT_X):
        slot = bd.Box(C.PLATE_SLOT_W, C.PLATE_SLOT_L, 40.0).moved(bd.Pos(sx, 0, -10.0))
        plate = plate - slot

    # Magnet pockets on the outer face at the centre column (open upward at z = 0).
    depth = C.PLATE_MAG_POCKET + 0.5
    for x, y in C.MAG_POS:
        pocket = bd.Cylinder(C.PLATE_MAG_D / 2, depth).moved(
            bd.Pos(x, y, 0.5 - depth / 2))
        plate = plate - pocket

    plate.label = name
    plate.color = "#7a5fb0"
    return plate


if __name__ == "__main__":
    for nm, r in (("thigh", C.R_THIGH), ("shank", C.R_SHANK)):
        p = build_plate(r, f"strap_plate_{nm}")
        print(nm, p.bounding_box().size, "solids", len(p.solids()), "valid", p.is_valid)
